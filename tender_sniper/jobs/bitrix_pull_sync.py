"""Background job: каждые 5 мин подтягиваем изменения статусов из Bitrix24
во все pipeline-команды у которых настроен webhook.

Запускается из bot/main.py.
"""
import asyncio
import logging

from cabinet import bitrix_sync

logger = logging.getLogger(__name__)

PULL_INTERVAL_SECONDS = 300  # 5 минут
START_DELAY_SECONDS = 90     # отложенный старт чтобы не дублироваться при rolling deploy


async def pull_loop():
    await asyncio.sleep(START_DELAY_SECONDS)
    while True:
        try:
            result = await bitrix_sync.pull_changes_for_all_companies()
            if result.get('updated'):
                logger.info(
                    f'[bitrix-pull-loop] companies={result["companies"]} '
                    f'checked={result["checked"]} updated={result["updated"]}'
                )
        except Exception as e:
            logger.error(f'pull_changes_for_all_companies failed: {e}', exc_info=True)
        await asyncio.sleep(PULL_INTERVAL_SECONDS)
