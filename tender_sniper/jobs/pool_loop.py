"""Второй путь доставки тендеров: общий пул через интеграционный сервис ЕИС.

Связывает три готовые части, которые до сих пор лежали порознь: сборщик
(pool_sweep_eis) кладёт извещения в tender_pool, матчер (pool_match)
сопоставляет их с фильтрами локально, а отправки не делал никто —
матчер намеренно её не делает, чтобы не заводить третью копию логики
рассылки.

**Зачем второй путь.** Первый ходит на сайт zakupki.gov.ru, а тот
блокирует хостинговые адреса. Замер 22.09.2026 на живом воркере за час:
1 893 попытки загрузки, из них 1 601 отбита (85%), и 12 циклов поиска из
97 вернулись пустыми. Уведомления всё же идут — 81 активный фильтр
перебирает столько ключевых слов, что проходящие 15% дают поток, — но
охват теряется молча: изнутри «не пустили» неотличимо от «ничего
подходящего не нашлось». Интеграционный сервис не блокируется вовсе:
замер 20.09.2026 — 0 успешных запросов из 9 к сайту против 5 из 5 к
сервису, а 21.09.2026 через него прошло 2 550 архивов подряд без отказов.

**Работает РЯДОМ с прежним путём, а не вместо.** Что пробилось через
сайт — приходит за две минуты, что не пробилось — в течение четырёх
часов. Дублей не будет: is_tender_notified проверяется ДО отправки, а
ограничение UNIQUE (user_id, company_id, tender_number) от источника не
зависит.

**Чем платим.** Сервис отказывается отдавать часы свежее двух (замер
21.09.2026: в 13:14 час 12 отвергнут, час 11 отдан), плюс сборщик берёт
с запасом — итого задержка 3-4 часа. Решение владельца 22.09.2026:
«важно не терять тендеры, скорость появления может упасть».
"""
import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from bot.config import BotConfig
from tender_sniper.jobs.pool_match import mark_processed, match_pool
from tender_sniper.jobs.pool_sweep_eis import sweep
from tender_sniper.notifications.telegram_notifier import TelegramNotifier
from tender_sniper.sources.eis_regions import ALL_CODES

logger = logging.getLogger(__name__)

# Стартовая задержка — как у остальных фоновых job'ов: дать подняться
# основному мэтчингу и БД, не устраивая гонку на холодном деплое.
START_DELAY_SECONDS = 240

# Раз в час: сборщик и так берёт часовыми срезами, чаще ходить не за чем.
POLL_INTERVAL_SECONDS = 3600

# Сколько строк пула разбираем за проход — и это же предохранитель на
# время обкатки.
#
# Ограничивать надо ИМЕННО разбор, а не отправку. match_pool помечает
# matched_at у всех строк, которые прошли через него, поэтому совпадение,
# отсечённое потолком рассылки, потерялось бы навсегда — ровно то, чего
# нельзя допускать. Неразобранные строки, наоборот, остаются с
# matched_at = NULL и дождутся следующего часа: платим задержкой, а не
# потерей.
#
# Замер 22.09.2026: подтверждённых моделью совпадений 17 на 400 строк,
# то есть около 4%. При 600 строках выходит ~25 уведомлений за проход
# против нынешних ~240 в сутки по всем источникам. Снимается переменной
# окружения, когда по логам станет видна настоящая частота.
MATCH_LIMIT = int(os.getenv('EIS_POOL_MATCH_LIMIT', '600'))

# Источник в уведомлении. Отличать обязательно: по нему потом видно, что
# именно добрал пул сверх сайта, и стоит ли отключать первый путь.
SOURCE = 'eis_pool'


# Признак закупки у единственного поставщика в названии способа закупки.
# Проверяем по подстроке, а не по точному значению: формулировок в
# классификаторе несколько, и они длинные.
_DIRECT_MARKERS = ('статьи 93', 'единственн')


def is_direct(procedure_type: Optional[str]) -> bool:
    """Закупка у единственного поставщика — участвовать в ней нечего.

    Не «закрыта раньше времени», а не предполагала подачи вовсе. Замер
    22.09.2026 по боевым данным:

        ст. 93 ч. 12        394 протокола, медиана 0 суток,
                            268 из 394 (68%) закрылись в день публикации
        электронный аукцион 7 021 протокол, медиана 10 суток
        запрос котировок    3 188 протоколов, медиана 8 суток

    В разобранном примере (0376200000226000027) извещение опубликовано в
    11:17:59, заявка подана в 11:17:04 — НА МИНУТУ РАНЬШЕ публикации, —
    а протокол итогов подписан в 11:36. Девятнадцать минут, и победитель
    был известен до старта.

    Поэтому такие закупки не рассылаются: прислать их как тендер значит
    позвать туда, где участвовать невозможно. В пуле они остаются — сам
    факт, что заказчик покупает этот товар напрямую, стоит знать, но это
    повод для прямого предложения, а не для заявки.
    """
    low = (procedure_type or '').lower()
    return any(marker in low for marker in _DIRECT_MARKERS)


async def deliver(matches: List[Dict[str, Any]],
                  filters_by_id: Dict[int, Dict[str, Any]],
                  db, notifier: Optional[TelegramNotifier]) -> int:
    """Рассылает совпадения пула. Возвращает число отправленных.

    Вынесено из цикла отдельной функцией, чтобы дедуп и маршрутизацию по
    чатам можно было проверить тестом, не поднимая ни сети, ни БД.

    Порядок проверок повторяет Портал поставщиков осознанно: сначала
    ОДИН раз на (тендер, пользователь) — до перебора чатов, иначе
    save_notification после первого чата ложно заблокирует второй
    notify_chat_id того же пользователя в этом же проходе.
    """
    sent = 0
    skipped_direct = 0
    seen = set()

    for item in matches:
        if is_direct(item.get('procedure_type')):
            skipped_direct += 1
            continue
        filter_data = filters_by_id.get(item['filter_id'])
        if not filter_data:
            continue

        user_id = filter_data['user_id']
        company_id = filter_data.get('company_id')
        tender_number = item['tender_number']

        if await db.is_tender_notified(tender_number, user_id,
                                       company_id=company_id):
            continue

        notify_chat_ids = filter_data.get('notify_chat_ids') or []
        thread_id = filter_data.get('notify_thread_id')
        targets = notify_chat_ids or [filter_data['telegram_id']]

        for chat_id in targets:
            key = (chat_id, tender_number)
            if key in seen:
                continue
            if chat_id < 0 and await db.is_tender_sent_to_chat(tender_number, chat_id):
                seen.add(key)
                continue
            seen.add(key)

            if notifier is None:
                logger.warning("Пул ЕИС: BOT_TOKEN не задан, %s не отправлен",
                               tender_number)
                continue

            ok = await notifier.send_tender_notification(
                telegram_id=chat_id,
                tender=item['tender'],
                match_info={'score': item['score'],
                            'matched_keywords':
                                (item.get('match_info') or {}).get('matched_keywords', [])},
                filter_name=item['filter_name'],
                is_auto_notification=True,
                subscription_tier=filter_data.get('subscription_tier', 'premium'),
                message_thread_id=thread_id if chat_id < 0 else None,
            )
            if not ok:
                logger.warning("Пул ЕИС: не удалось отправить %s -> %s",
                               tender_number, chat_id)
                continue

            await db.save_notification(
                user_id=user_id, filter_id=item['filter_id'],
                filter_name=item['filter_name'], tender_data=item['tender'],
                score=item['score'],
                matched_keywords=(item.get('match_info') or {}).get('matched_keywords', []),
                match_info=item.get('match_info') or {},
                source=SOURCE, notified_chat_id=chat_id,
            )
            sent += 1

    if skipped_direct:
        logger.info("Пул ЕИС: не разослано как закупки у единственного "
                    "поставщика — %d", skipped_direct)
    return sent


async def _pending() -> int:
    """Сколько строк пула ждёт разбора. Для итоговой строки лога."""
    from sqlalchemy import func, select

    from database import DatabaseSession, TenderPool
    try:
        async with DatabaseSession() as session:
            return int(await session.scalar(
                select(func.count()).select_from(TenderPool)
                .where(TenderPool.matched_at.is_(None))) or 0)
    except Exception:  # noqa: BLE001 — счётчик в логе не повод ронять цикл
        return -1


async def pool_loop(region_codes=None) -> None:
    """Сбор пула через ЕИС, локальный матчинг и рассылка — раз в час."""
    from tender_sniper.database import get_sniper_db

    await asyncio.sleep(START_DELAY_SECONDS)
    regions = list(region_codes or ALL_CODES)
    logger.info("Пул ЕИС: job запущен, регионов %d", len(regions))
    notifier: Optional[TelegramNotifier] = None

    while True:
        try:
            stats = await sweep(regions)

            db = await get_sniper_db()
            if notifier is None and BotConfig.BOT_TOKEN:
                notifier = TelegramNotifier(bot_token=BotConfig.BOT_TOKEN)

            filters = await db.get_all_active_filters()
            # dry_run=True — чтобы строки НЕ помечались разобранными до
            # рассылки: перезапуск воркера между разбором и отправкой
            # иначе превращает найденное в навсегда потерянное. Помечаем
            # ниже, после deliver.
            result = await match_pool(limit=MATCH_LIMIT, filters=filters,
                                      dry_run=True)
            filters_by_id = {f['id']: f for f in filters}
            sent = await deliver(result.get('matches') or [], filters_by_id,
                                 db, notifier)
            await mark_processed(result.get('checked_numbers') or [])

            # Итоговая строка печатается ВСЕГДА, даже при нулях: тишина в
            # логах иначе неотличима от «job не стартовал» — на этом уже
            # обожглись с Порталом поставщиков 13.09.
            #
            # Очередь в строке обязательна: разбор ограничен MATCH_LIMIT, и
            # если сбор приносит больше, чем проход успевает разобрать,
            # хвост растёт молча. Растущее число здесь — сигнал поднять
            # лимит, а не признак поломки.
            logger.info("Пул ЕИС: цикл завершён — сохранено %d, проверено %d, "
                        "совпадений %d, отправлено %d, в очереди %d",
                        stats.saved, result.get('checked', 0),
                        len(result.get('matches') or []), sent,
                        await _pending())
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — цикл не должен умирать насовсем
            logger.error("Пул ЕИС: цикл упал: %s", e, exc_info=True)

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
