"""Background job: каждые ~90 сек подтягивает новые комментарии ленты
Bitrix24 для карточек pipeline у компаний с настроенным webhook.

У Bitrix нет push-события на добавление комментария (в отличие от
create/update/delete сделки, см. bot/health_check.py::bitrix24_events_handler) —
поэтому единственный способ узнать про новые комментарии — периодически
спрашивать. Запускается из bot/main.py.
"""
import asyncio
import logging

from sqlalchemy import select

from cabinet import bitrix_sync
from database import DatabaseSession, Company

logger = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = 90
START_DELAY_SECONDS = 100  # чуть позже bitrix_pull_loop, чтобы не толкаться при старте


async def comment_sync_loop():
    await asyncio.sleep(START_DELAY_SECONDS)
    while True:
        try:
            async with DatabaseSession() as session:
                rows = await session.execute(select(Company))
                companies = list(rows.scalars().all())
            for company in companies:
                webhook = await bitrix_sync._get_company_webhook(company.id)
                if not webhook:
                    continue
                try:
                    result = await bitrix_sync.sync_comments_for_company(company.id)
                    if result.get('added'):
                        logger.info(
                            f'[bitrix-comment-sync] company={company.id} '
                            f'checked={result["checked"]} added={result["added"]}'
                        )
                except Exception as e:
                    logger.error(f'[bitrix-comment-sync] company={company.id}: {e}', exc_info=True)
        except Exception as e:
            logger.error(f'[bitrix-comment-sync] loop error: {e}', exc_info=True)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
