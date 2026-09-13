"""Разовый довылет: доотправить в группу уведомления, которые ушли ТОЛЬКО
в личку из-за бага is_tender_sent_to_chat (см. миграцию
20260913_notified_chat_id и коммит с фиксом). Не гоняет матчинг заново —
is_tender_notified() уже считает эти тендеры отправленными пользователю,
поэтому обычный повторный прогон бэкфилла просто пропустил бы их целиком.
Вместо этого читает уже сохранённые строки sniper_notifications (source
mos_portal/mos_portal_backfill) без notified_chat_id = group_id и досылает
именно недостающую часть — в группу, с тем же текстом/матчем.

Запуск (один раз, вручную):
    python3 scripts/mos_portal_group_catchup.py
"""
import asyncio
import logging

from bot.config import BotConfig
from tender_sniper.database import get_sniper_db
from tender_sniper.database.sqlalchemy_adapter import DatabaseSession, SniperNotificationModel
from tender_sniper.notifications.telegram_notifier import TelegramNotifier
from tender_sniper.jobs.mos_portal_poll import COMPANY_ID
from sqlalchemy import select, and_

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def main():
    db = await get_sniper_db()
    bot_token = BotConfig.BOT_TOKEN
    notifier = TelegramNotifier(bot_token=bot_token) if bot_token else None
    if notifier is None:
        raise RuntimeError("BOT_TOKEN не задан")

    async with DatabaseSession() as session:
        rows = (await session.execute(
            select(SniperNotificationModel).where(
                and_(
                    SniperNotificationModel.tender_source.in_(['mos_portal', 'mos_portal_backfill']),
                    SniperNotificationModel.company_id == COMPANY_ID,
                )
            )
        )).scalars().all()
        # snapshot нужных полей, пока сессия открыта
        saved = [
            {
                'filter_id': r.filter_id,
                'filter_name': r.filter_name,
                'tender_number': r.tender_number,
                'tender_name': r.tender_name,
                'tender_price': r.tender_price,
                'tender_url': r.tender_url,
                'tender_region': r.tender_region,
                'tender_customer': r.tender_customer,
                'score': r.score,
                'matched_keywords': r.matched_keywords,
                'match_info': r.match_info,
                'user_id': r.user_id,
            }
            for r in rows
        ]

    print(f"Найдено сохранённых уведомлений Портала поставщиков: {len(saved)}")

    checked = 0
    sent_count = 0
    for row in saved:
        if not row['filter_id']:
            continue
        filter_row = await db.get_filter_by_id(row['filter_id'])
        notify_chat_ids = filter_row.get('notify_chat_ids') if filter_row else None
        notify_thread_id = filter_row.get('notify_thread_id') if filter_row else None

        if not notify_chat_ids:
            continue
        group_chat_ids = [c for c in notify_chat_ids if c < 0]
        if not group_chat_ids:
            continue

        checked += 1
        tender = {
            'number': row['tender_number'],
            'name': row['tender_name'],
            'price': row['tender_price'],
            'url': row['tender_url'],
            'region': row['tender_region'],
            'customer_name': row['tender_customer'],
        }

        for group_chat_id in group_chat_ids:
            already = await db.is_tender_sent_to_chat(row['tender_number'], group_chat_id)
            if already:
                continue

            success = await notifier.send_tender_notification(
                telegram_id=group_chat_id,
                tender=tender,
                match_info=row['match_info'] or {'score': row['score'], 'matched_keywords': row['matched_keywords'] or []},
                filter_name=row['filter_name'],
                is_auto_notification=True,
                subscription_tier='premium',
                message_thread_id=notify_thread_id,
            )
            if success:
                await db.save_notification(
                    user_id=row['user_id'], filter_id=row['filter_id'], filter_name=row['filter_name'],
                    tender_data=tender, score=row['score'],
                    matched_keywords=row['matched_keywords'] or [],
                    match_info=row['match_info'], source='mos_portal_group_catchup',
                    notified_chat_id=group_chat_id,
                )
                sent_count += 1
                print(f"Довылет в группу: {row['tender_number']} -> {group_chat_id} (фильтр '{row['filter_name']}')")
            else:
                logger.warning(f"Довылет: не удалось отправить {row['tender_number']} -> {group_chat_id}")

    print(f"Итого: проверено с групповой маршрутизацией {checked}, довыслано в группу {sent_count}")


if __name__ == "__main__":
    asyncio.run(main())
