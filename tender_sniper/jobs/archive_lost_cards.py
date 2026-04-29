"""Background job: архивирует lost-карточки старше 90 дней.

Запускается раз в 24 часа из bot/main.py.
"""
import asyncio
import logging

from cabinet import pipeline_service

logger = logging.getLogger(__name__)

ARCHIVE_INTERVAL_SECONDS = 86400  # 24 часа


async def archive_loop():
    """Бесконечный цикл, раз в сутки архивирует старые lost-карточки."""
    # Стартовая задержка чтобы не упереться в одновременные старты при деплое
    await asyncio.sleep(120)
    while True:
        try:
            count = await pipeline_service.archive_old_lost_cards()
            if count:
                logger.info(f'Archived {count} lost cards (older than 90 days)')
        except Exception as e:
            logger.error(f'archive_old_lost_cards failed: {e}', exc_info=True)
        await asyncio.sleep(ARCHIVE_INTERVAL_SECONDS)
