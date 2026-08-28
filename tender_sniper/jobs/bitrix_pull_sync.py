"""Background job: раз в час подтягиваем изменения статусов из Bitrix24
во все pipeline-команды у которых настроен webhook.

Сетка безопасности на случай пропущенной доставки вебхука — основной путь
теперь событийный (см. PULL_INTERVAL_SECONDS ниже).

Запускается из bot/main.py.
"""
import asyncio
import logging

from cabinet import bitrix_sync

logger = logging.getLogger(__name__)

PULL_INTERVAL_SECONDS = 3600  # 1 час — теперь это сетка безопасности,
# основной путь — событийный (bot/health_check.py::bitrix24_events_handler)
# + отдельный поллинг комментариев (tender_sniper/jobs/bitrix_comment_sync.py)
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
