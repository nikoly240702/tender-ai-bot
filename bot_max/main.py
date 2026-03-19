"""
VK Max bot — long-polling entry point.

Reads MAX_BOT_TOKEN from env. If token is not set, start_max_bot() exits silently.
"""

import os
import logging
import asyncio

from bot_max.client import MaxBotClient
from bot_max.handlers import dispatch_update

logger = logging.getLogger(__name__)


async def start_max_bot():
    """Initialize Max bot client and run long-polling loop."""
    token = os.getenv("MAX_BOT_TOKEN", "").strip()
    if not token:
        logger.info("MAX_BOT_TOKEN not set — Max bot disabled")
        return

    client = MaxBotClient(token)

    # Verify token
    try:
        me = await client.get_me()
        bot_name = me.get("name", me.get("username", "unknown"))
        logger.info(f"Max bot started: {bot_name}")
    except Exception as e:
        logger.error(f"Max bot: failed to verify token (/me): {e}")
        await client.close()
        return

    marker = None

    while True:
        try:
            data = await client.get_updates(marker=marker, timeout=30)
            updates = data.get("updates", [])
            new_marker = data.get("marker")

            if new_marker is not None:
                marker = new_marker

            for update in updates:
                try:
                    await dispatch_update(client, update)
                except Exception as e:
                    logger.error(f"Max bot: error processing update: {e}", exc_info=True)

        except asyncio.CancelledError:
            logger.info("Max bot: polling cancelled, shutting down")
            break
        except Exception as e:
            logger.error(f"Max bot: polling error: {e}", exc_info=True)
            # Wait before reconnecting
            await asyncio.sleep(5)

    await client.close()
    logger.info("Max bot stopped")
