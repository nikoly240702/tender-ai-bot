"""Разовый бэкфилл Портала поставщиков (Москва).

Живой job (tender_sniper/jobs/mos_portal_poll.py) смотрит только на узкое
окно (10 мин + нахлёст), поэтому КС, опубликованные ДО того как job был
включён (13.09.2026), никогда не попадут в его окно. Этот скрипт разово
подтягивает более широкое окно назад, прогоняет через тот же матчер/дедуп/
уведомления, что и живой job — переиспользует те же хелперы и константы,
так что ничего не задвоится, когда живой job естественным образом дойдёт
до этого же периода через свои перекрывающиеся окна.

Запуск (один раз, вручную):
    python3 scripts/mos_portal_backfill.py --days 3
"""
import argparse
import asyncio
import logging
from datetime import datetime, timedelta

from bot.config import BotConfig
from tender_sniper.sources.mos_portal_client import MosPortalClient
from tender_sniper.matching import SmartMatcher
from tender_sniper.notifications.telegram_notifier import TelegramNotifier
from tender_sniper.database import get_sniper_db
from tender_sniper.jobs.mos_portal_poll import (
    COMPANY_ID,
    MOS_MIN_SCORE_FOR_NOTIFICATION,
    MOSCOW_TZ,
    PAGE_SIZE,
    _map_page_to_tenders,
    _to_api_timestamp,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

MAX_PAGES_BACKFILL = 50  # разовый прогон за несколько дней — шире потолка обычного 10-минутного цикла


def _deadline_passed(tender: dict, now) -> bool:
    """Портал отдаёт submission_deadline как naive-строку в MSK (см.
    ks_dto_to_tender/beginDate,endDate — те же naive-строки, что мы видели в
    живом ответе API: '2026-09-15T09:00:00'). Нет данных о сроке — не
    отсеиваем (осторожнее пропустить, чем ложно скрыть реальный матч)."""
    deadline_str = tender.get('submission_deadline')
    if not deadline_str:
        return False
    try:
        deadline = datetime.fromisoformat(deadline_str)
    except ValueError:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=MOSCOW_TZ)
    return deadline < now


async def main(days: int):
    client = MosPortalClient()
    matcher = SmartMatcher()
    db = await get_sniper_db()
    bot_token = BotConfig.BOT_TOKEN
    notifier = TelegramNotifier(bot_token=bot_token) if bot_token else None
    if notifier is None:
        raise RuntimeError("BOT_TOKEN не задан — некому отправлять уведомления")

    now = datetime.now(MOSCOW_TZ)
    window_from = now - timedelta(days=days)

    tenders = []
    skip = 0
    for _ in range(MAX_PAGES_BACKFILL):
        resp = await client.search_auctions(
            _to_api_timestamp(window_from), _to_api_timestamp(now), skip=skip, take=PAGE_SIZE,
        )
        page = resp.get("data") or resp.get("items") or []
        if not page:
            break
        tenders.extend(_map_page_to_tenders(page))
        if len(page) < PAGE_SIZE:
            break
        skip += PAGE_SIZE
    else:
        logger.warning(f"Бэкфилл: достигнут потолок {MAX_PAGES_BACKFILL} страниц")

    print(f"Портал поставщиков: подтянуто {len(tenders)} тендеров за {days} дн.")

    filters = await db.get_all_active_filters(company_id=COMPANY_ID)
    seen_tenders = set()
    sent_count = 0
    skipped_deadline = 0

    for tender in tenders:
        if _deadline_passed(tender, now):
            skipped_deadline += 1
            continue
        tender_number = tender['number']
        for filter_data in filters:
            match = matcher.match_tender(tender, filter_data)
            if not match or match['score'] < MOS_MIN_SCORE_FOR_NOTIFICATION:
                continue

            filter_id = filter_data['id']
            filter_name = filter_data['name']
            user_id = filter_data['user_id']
            notify_chat_ids = filter_data.get('notify_chat_ids') or []
            notify_thread_id = filter_data.get('notify_thread_id')
            target_chat_ids = notify_chat_ids if notify_chat_ids else [filter_data['telegram_id']]

            already_notified = await db.is_tender_notified(tender_number, user_id, company_id=COMPANY_ID)
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

                success = await notifier.send_tender_notification(
                    telegram_id=target_chat_id,
                    tender=tender,
                    match_info={'score': match['score'], 'matched_keywords': match.get('matched_keywords', [])},
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
                        match_info=match, source='mos_portal_backfill',
                        notified_chat_id=target_chat_id,
                    )
                    sent_count += 1
                    print(f"Отправлено: {tender_number} -> {target_chat_id} (фильтр '{filter_name}', score={match['score']})")
                else:
                    logger.warning(f"Бэкфилл: не удалось отправить {tender_number} -> {target_chat_id}")

    print(f"Итого: тендеров {len(tenders)}, пропущено по истёкшему сроку {skipped_deadline}, отправлено {sent_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(main(args.days))
