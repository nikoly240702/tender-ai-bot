"""Опрос Портала поставщиков (Москва) — второй источник тендеров, матчится
только по фильтрам company_id=57. См.
docs/superpowers/specs/2026-09-13-mos-portal-integration-design.md.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional, Tuple

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


def compute_poll_window(last_poll: Optional[datetime], now: datetime) -> Tuple[datetime, datetime]:
    if last_poll is None:
        return now - timedelta(minutes=DEFAULT_LOOKBACK_MINUTES), now
    return last_poll - timedelta(minutes=OVERLAP_MINUTES), now


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
    client = MosPortalClient()
    await _check_token_expiry(client)
    matcher = SmartMatcher()
    bot_token = os.environ.get('BOT_TOKEN', '')
    notifier = TelegramNotifier(bot_token=bot_token) if bot_token else None
    db = await get_sniper_db()
    last_poll: Optional[datetime] = None

    while True:
        try:
            now = datetime.utcnow()
            window_from, window_to = compute_poll_window(last_poll, now)
            tenders = []
            skip = 0
            for _ in range(MAX_PAGES_PER_CYCLE):
                resp = await client.search_auctions(
                    window_from.isoformat(), window_to.isoformat(), skip=skip, take=PAGE_SIZE,
                )
                page = resp.get("data") or resp.get("items") or []
                if not page:
                    break
                tenders.extend(ks_dto_to_tender(dto) for dto in page)
                if len(page) < PAGE_SIZE:
                    break
                skip += PAGE_SIZE
            else:
                logger.warning(f"Портал поставщиков: достигнут потолок {MAX_PAGES_PER_CYCLE} страниц за цикл")

            if tenders:
                filters = await db.get_all_active_filters(company_id=COMPANY_ID)
                seen_tenders = set()  # (chat_id, tender_number) — дедуп внутри одного цикла
                for tender in tenders:
                    tender_number = tender['number']
                    for filter_data in filters:
                        match = matcher.match_tender(tender, filter_data)
                        if not match:
                            continue

                        filter_id = filter_data['id']
                        filter_name = filter_data['name']
                        user_id = filter_data['user_id']
                        notify_chat_ids = filter_data.get('notify_chat_ids') or []
                        notify_thread_id = filter_data.get('notify_thread_id')
                        target_chat_ids = notify_chat_ids if notify_chat_ids else [filter_data['telegram_id']]

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
                                subscription_tier='premium',
                                message_thread_id=notify_thread_id if target_chat_id < 0 else None,
                            )
                            if success:
                                await db.save_notification(
                                    user_id=user_id, filter_id=filter_id, filter_name=filter_name,
                                    tender_data=tender, score=match['score'],
                                    matched_keywords=match.get('matched_keywords', []),
                                    match_info=match,
                                )
                            else:
                                logger.warning(f"Портал поставщиков: не удалось отправить {tender_number} -> {target_chat_id}")

            last_poll = now
        except Exception as e:
            logger.error(f"Портал поставщиков: ошибка цикла опроса: {e}", exc_info=True)

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
