"""Background job: переводит просроченные по дедлайну карточки в REJECTED,
а старые REJECTED — удаляет насовсем.

Запускается раз в час из bot/main.py.
"""
import asyncio
import logging

from cabinet import pipeline_service

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 3600  # 1 час


async def expire_deadline_loop():
    """Бесконечный цикл: раз в час — автопросрочка, затем удаление старых REJECTED."""
    await asyncio.sleep(150)
    while True:
        try:
            rejected = await pipeline_service.auto_reject_expired_cards()
            if rejected:
                logger.info(f'Auto-rejected {rejected} cards with expired submission deadline')
            deleted = await pipeline_service.delete_expired_rejected_cards()
            if deleted:
                logger.info(f'Deleted {deleted} REJECTED cards older than '
                            f'{pipeline_service.REJECTED_HARD_DELETE_AGE_DAYS} days')
        except Exception as e:
            logger.error(f'expire_deadline_loop failed: {e}', exc_info=True)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
