"""Опрос Портала поставщиков (Москва) — второй источник тендеров, матчится
только по фильтрам company_id=57. См.
docs/superpowers/specs/2026-09-13-mos-portal-integration-design.md.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from bot.config import BotConfig
from tender_sniper.sources.mos_portal_client import MosPortalClient, decode_jwt_exp
from tender_sniper.sources.mos_portal_mapper import ks_dto_to_tender
from tender_sniper.matching import SmartMatcher
from tender_sniper.notifications.telegram_notifier import TelegramNotifier
from tender_sniper.database import get_sniper_db

logger = logging.getLogger(__name__)

COMPANY_ID = 57
POLL_INTERVAL_SECONDS = 600  # 10 минут
OVERLAP_MINUTES = 30
DEFAULT_LOOKBACK_MINUTES = 60
MAX_PAGES_PER_CYCLE = 10
PAGE_SIZE = 200
MOSCOW_TZ = ZoneInfo("Europe/Moscow")
# ВАЖНО: этот порог — НЕ то же самое, что MIN_SCORE_FOR_NOTIFICATION в
# tender_sniper/service.py (=35). Там порог гейтит COMPOSITE score: сырой
# SmartMatcher score + AI-relevance буст (+10/+15, см.
# tender_sniper/instant_search.py:872-879), посчитанный по name+description.
# У этой job'ы нет шага AI-буста, а description у Портала поставщиков ВСЕГДА
# пустой (list-эндпоинт его не отдаёт) — то есть здесь гейтится строго
# меньший, name-only «сырой» score против того же порога было бы неверно.
# Замер по реальным filters_v2.yaml показал легитимные совпадения (каталожные
# и словоформы) со скором 9-16 — то есть ниже 35 они бы молча отбрасывались.
# 20 — обоснованная стартовая точка по этому замеру, НЕ финальное тюнингованное
# число. Пересмотреть, когда появится статистика по реальному трафику Портала
# поставщиков (см. лог ниже — near-miss'ы логируются для этого).
MOS_MIN_SCORE_FOR_NOTIFICATION = 20


def compute_poll_window(last_poll: Optional[datetime], now: datetime) -> Tuple[datetime, datetime]:
    if last_poll is None:
        return now - timedelta(minutes=DEFAULT_LOOKBACK_MINUTES), now
    return last_poll - timedelta(minutes=OVERLAP_MINUTES), now


def _to_api_timestamp(dt: datetime) -> str:
    """UTC-строка с суффиксом Z для параметров запроса к API. Портал
    поставщиков — .NET-сервис за прокси/шлюзом, который где-то по пути
    декодирует '%2B' в '+', а затем ещё раз интерпретирует уже раскодированный
    '+' как пробел (двойное form-decoding) — сервер получает
    "23:09:26 03:00" вместо "23:09:26+03:00" и не может распарсить дату
    (подтверждено эмпирически: запрос с оффсетом +03:00 стабильно давал 400
    Bad Request, тот же самый момент времени в форме UTC+Z прошёл). Формат
    'Z' не содержит '+' вовсе, поэтому проблема не возникает — при этом сам
    момент времени (и корректность MSK-вычисления окна) не меняется, меняется
    только то, как он сериализуется на проводе."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _map_page_to_tenders(page: list) -> list:
    """Мапит страницу DTO Портала поставщиков в тендеры. Одна кривая запись
    (например без "id" — ks_dto_to_tender кидает KeyError по замыслу) не
    должна обрушивать весь цикл: если бы исключение дошло до цикл-левел
    except, last_poll не сдвинулся бы, и следующий цикл упёрся бы в ту же
    запись, пока она не выйдет из окна поиска (до часа простоя)."""
    tenders = []
    for dto in page:
        try:
            tenders.append(ks_dto_to_tender(dto))
        except Exception as e:
            logger.warning(f"Портал поставщиков: пропущен неразбираемый DTO: {e}")
    return tenders


async def _check_token_expiry(client: MosPortalClient) -> None:
    exp = decode_jwt_exp(client.token)
    if exp is None:
        logger.warning("Портал поставщиков: не удалось прочитать exp токена")
        return
    days_left = (datetime.utcfromtimestamp(exp) - datetime.utcnow()).days
    if days_left < 30:
        logger.warning(f"Портал поставщиков: токен PP_TOKEN истекает через {days_left} дн.!")


async def mos_portal_poll_loop():
    await asyncio.sleep(180)  # стартовая задержка, как у остальных фоновых job'ов
    logger.info("Портал поставщиков: job запущен")
    matcher = SmartMatcher()
    last_poll: Optional[datetime] = None
    notifier: Optional[TelegramNotifier] = None

    while True:
        try:
            # Внутри try: пока PP_TOKEN не задан (см. Task 6), конструктор
            # кидает RuntimeError — цикл должен пережить это и повторить
            # попытку на следующем POLL_INTERVAL_SECONDS, а не убить job навсегда.
            # db/notifier — по той же причине: если БД ещё не готова (холодный
            # деплой) или BOT_TOKEN временно недоступен, это не должно убить
            # job навсегда, только текущий цикл.
            client = MosPortalClient()
            await _check_token_expiry(client)
            db = await get_sniper_db()
            bot_token = BotConfig.BOT_TOKEN
            if notifier is None:
                notifier = TelegramNotifier(bot_token=bot_token) if bot_token else None

            # Портал поставщиков — московский, ожидает naive-время как MSK
            # (UTC+3, без DST), а не UTC. При окне опроса ~10 мин с нахлёстом
            # 30 мин разница в 3 часа означает, что окно систематически
            # отстаёт на ~3 часа каждый цикл — уведомления опаздывали бы
            # на ~3 часа. Строим "сейчас" в Europe/Moscow (корректный момент
            # времени), но на проводе шлём в UTC/Z — см. _to_api_timestamp.
            now = datetime.now(MOSCOW_TZ)
            window_from, window_to = compute_poll_window(last_poll, now)
            tenders = []
            skip = 0
            for _ in range(MAX_PAGES_PER_CYCLE):
                resp = await client.search_auctions(
                    _to_api_timestamp(window_from), _to_api_timestamp(window_to),
                    skip=skip, take=PAGE_SIZE,
                )
                page = resp.get("data") or resp.get("items") or []
                if not page:
                    break
                tenders.extend(_map_page_to_tenders(page))
                if len(page) < PAGE_SIZE:
                    break
                skip += PAGE_SIZE
            else:
                logger.warning(f"Портал поставщиков: достигнут потолок {MAX_PAGES_PER_CYCLE} страниц за цикл")

            sent_count = 0
            if tenders:
                filters = await db.get_all_active_filters(company_id=COMPANY_ID)
                seen_tenders = set()  # (chat_id, tender_number) — дедуп внутри одного цикла
                for tender_idx, tender in enumerate(tenders):
                    # Цикл tenders×filters синхронный (без await внутри), при
                    # ~2000 тендерах × ~50 фильтров может занять до ~40с и
                    # заблокировать event loop того же воркер-процесса, где
                    # крутится основной мониторинг zakupki.gov.ru и health
                    # check. Периодически отдаём управление event loop'у.
                    if tender_idx and tender_idx % 20 == 0:
                        await asyncio.sleep(0)
                    tender_number = tender['number']
                    for filter_data in filters:
                        match = matcher.match_tender(tender, filter_data)
                        if not match:
                            continue
                        if match['score'] < MOS_MIN_SCORE_FOR_NOTIFICATION:
                            logger.info(
                                f"Портал поставщиков: near-miss — фильтр '{filter_data['name']}', "
                                f"тендер '{tender.get('name')}' ({tender_number}), score={match['score']}"
                            )
                            continue

                        filter_id = filter_data['id']
                        filter_name = filter_data['name']
                        user_id = filter_data['user_id']
                        notify_chat_ids = filter_data.get('notify_chat_ids') or []
                        notify_thread_id = filter_data.get('notify_thread_id')
                        target_chat_ids = notify_chat_ids if notify_chat_ids else [filter_data['telegram_id']]

                        # Дедуп между циклами (окно опроса перекрывается на 30 мин
                        # при интервале 10 мин => один тендер попадает в ~4 цикла
                        # подряд). Проверяем ОДИН раз на (tender, user), как в
                        # tender_sniper/service.py — до цикла по target_chat_ids,
                        # а не внутри него: иначе save_notification после отправки
                        # в первый чат сделал бы is_tender_notified true и ложно
                        # заблокировал бы отправку во второй notify_chat_id того
                        # же пользователя в этом же цикле. Для личного чата
                        # (target_chat_id > 0) это единственная защита от дублей —
                        # без неё пользователь получает тендер ~4 раза.
                        already_notified = await db.is_tender_notified(
                            tender_number, user_id, company_id=COMPANY_ID
                        )
                        if already_notified:
                            continue

                        for target_chat_id in target_chat_ids:
                            dedup_key = (target_chat_id, tender_number)
                            if dedup_key in seen_tenders:
                                continue
                            if target_chat_id < 0:
                                already_in_chat = await db.is_tender_sent_to_chat(tender_number, target_chat_id)
                                if already_in_chat:
                                    seen_tenders.add(dedup_key)
                                    continue
                            seen_tenders.add(dedup_key)

                            if notifier is None:
                                logger.warning("Портал поставщиков: BOT_TOKEN не задан, уведомление не отправлено")
                                continue

                            success = await notifier.send_tender_notification(
                                telegram_id=target_chat_id,
                                tender=tender,
                                match_info={'score': match['score'],
                                            'matched_keywords': match.get('matched_keywords', [])},
                                filter_name=filter_name,
                                is_auto_notification=True,
                                subscription_tier=filter_data.get('subscription_tier', 'premium'),
                                message_thread_id=notify_thread_id if target_chat_id < 0 else None,
                            )
                            if success:
                                await db.save_notification(
                                    user_id=user_id, filter_id=filter_id, filter_name=filter_name,
                                    tender_data=tender, score=match['score'],
                                    matched_keywords=match.get('matched_keywords', []),
                                    match_info=match,
                                    source='mos_portal',
                                    notified_chat_id=target_chat_id,
                                )
                                sent_count += 1
                            else:
                                logger.warning(f"Портал поставщиков: не удалось отправить {tender_number} -> {target_chat_id}")

            # Итоговая строка по циклу — печатается ВСЕГДА, даже при 0
            # тендерах/0 отправок, чтобы тишина в логах не была неотличима от
            # "job не стартовал" (столкнулись с этим на живом деплое 13.09).
            logger.info(f"Портал поставщиков: цикл завершён — тендеров {len(tenders)}, отправлено {sent_count}")

            last_poll = now
        except Exception as e:
            logger.error(f"Портал поставщиков: ошибка цикла опроса: {e}", exc_info=True)

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
