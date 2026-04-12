"""Broadcast click tracking. Intercepts bcast:* callbacks, marks click, redispatches."""
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import update, select

from database import DatabaseSession, BroadcastRecipient

logger = logging.getLogger(__name__)
router = Router()


def parse_bcast_callback(data: str) -> Optional[Dict[str, Any]]:
    """Parse 'bcast:<broadcast_id>:<recipient_id>:<action>' into dict."""
    if not data.startswith('bcast:'):
        return None
    parts = data.split(':', 3)
    if len(parts) != 4:
        return None
    try:
        return {
            'broadcast_id': int(parts[1]),
            'recipient_id': int(parts[2]),
            'action': parts[3],
        }
    except ValueError:
        return None


async def mark_broadcast_clicked(recipient_id: int, action: str) -> None:
    """Update broadcast_recipients row: set status=clicked, clicked_at=now, clicked_button=action."""
    async with DatabaseSession() as session:
        await session.execute(
            update(BroadcastRecipient)
            .where(BroadcastRecipient.id == recipient_id)
            .values(
                status='clicked',
                clicked_at=datetime.utcnow(),
                clicked_button=action,
            )
        )
        await session.commit()
    logger.info(f"Broadcast click tracked: recipient={recipient_id}, action={action}")


async def attribute_conversion(user_id: int, payment_id: int) -> None:
    """
    After successful payment — find the most recent broadcast_recipients entry
    for this user within 14-day window and mark as converted.
    """
    cutoff = datetime.utcnow() - timedelta(days=14)
    async with DatabaseSession() as session:
        recipient = await session.scalar(
            select(BroadcastRecipient)
            .where(
                BroadcastRecipient.user_id == user_id,
                BroadcastRecipient.delivered_at >= cutoff,
                BroadcastRecipient.status != 'converted',
            )
            .order_by(BroadcastRecipient.delivered_at.desc())
            .limit(1)
        )
        if not recipient:
            return

        await session.execute(
            update(BroadcastRecipient)
            .where(BroadcastRecipient.id == recipient.id)
            .values(
                status='converted',
                converted_at=datetime.utcnow(),
                converted_payment_id=payment_id,
            )
        )
        await session.commit()
        logger.info(f"Broadcast conversion attributed: recipient={recipient.id}, payment={payment_id}")


@router.callback_query(F.data.startswith("bcast:"))
async def handle_broadcast_click(callback: CallbackQuery):
    """Intercept broadcast button clicks, track, then redispatch to actual handler."""
    parsed = parse_bcast_callback(callback.data)
    if not parsed:
        logger.warning(f"Invalid bcast callback_data: {callback.data}")
        await callback.answer()
        return

    # Track the click
    try:
        await mark_broadcast_clicked(parsed['recipient_id'], parsed['action'])
    except Exception as e:
        logger.error(f"Failed to track broadcast click: {e}")

    # Redispatch by action
    action = parsed['action']
    if action == 'pay_starter':
        from bot.handlers.subscriptions import callback_select_tier
        callback.data = 'subscription_select_starter'
        await callback_select_tier(callback)
    elif action == 'tiers':
        from bot.handlers.subscriptions import callback_show_tiers
        callback.data = 'subscription_tiers'
        await callback_show_tiers(callback)
    else:
        await callback.answer(f"Unknown action: {action}")
