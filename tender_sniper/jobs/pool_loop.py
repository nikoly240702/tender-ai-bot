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
from typing import Any, Dict, List, Optional

from bot.config import BotConfig
from tender_sniper.jobs.pool_match import match_pool
from tender_sniper.jobs.pool_sweep_eis import sweep
from tender_sniper.notifications.telegram_notifier import TelegramNotifier
from tender_sniper.sources.eis_regions import ALL_CODES

logger = logging.getLogger(__name__)

# Стартовая задержка — как у остальных фоновых job'ов: дать подняться
# основному мэтчингу и БД, не устраивая гонку на холодном деплое.
START_DELAY_SECONDS = 240

# Раз в час: сборщик и так берёт часовыми срезами, чаще ходить не за чем.
POLL_INTERVAL_SECONDS = 3600

# Сколько строк пула разбираем за проход. 85 регионов за два часа дают
# порядка тысячи извещений в рабочем диапазоне; потолок с запасом, но не
# бесконечный — чтобы один проход не висел полчаса на матчинге.
MATCH_LIMIT = 3000

# Источник в уведомлении. Отличать обязательно: по нему потом видно, что
# именно добрал пул сверх сайта, и стоит ли отключать первый путь.
SOURCE = 'eis_pool'


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
    seen = set()

    for item in matches:
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

    return sent


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
            result = await match_pool(limit=MATCH_LIMIT, filters=filters)
            filters_by_id = {f['id']: f for f in filters}
            sent = await deliver(result.get('matches') or [], filters_by_id,
                                 db, notifier)

            # Итоговая строка печатается ВСЕГДА, даже при нулях: тишина в
            # логах иначе неотличима от «job не стартовал» — на этом уже
            # обожглись с Порталом поставщиков 13.09.
            logger.info("Пул ЕИС: цикл завершён — сохранено %d, проверено %d, "
                        "совпадений %d, отправлено %d",
                        stats.saved, result.get('checked', 0),
                        len(result.get('matches') or []), sent)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — цикл не должен умирать насовсем
            logger.error("Пул ЕИС: цикл упал: %s", e, exc_info=True)

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
