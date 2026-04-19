"""
Send pricing broadcast to specific segment with per-recipient tracking.

Usage:
    python scripts/send_pricing_broadcast.py --segment 1 --dry-run
    python scripts/send_pricing_broadcast.py --segment 1
    python scripts/send_pricing_broadcast.py --segment 2
    python scripts/send_pricing_broadcast.py --segment 3
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, update, text

from database import (
    init_database, DatabaseSession,
    SniperUser, BroadcastMessage, BroadcastRecipient,
)

# Segment 1: expired trial users who were active recently
# Segment 2: active trial users (trial not expired yet)
# Segment 3: old expired users (inactive for 30+ days)
SEGMENT_QUERIES = {
    # Segment 1: expired/trial users who were active recently (got notifications)
    1: """
        SELECT u.* FROM sniper_users u
        WHERE (u.subscription_tier = 'expired'
               OR (u.subscription_tier = 'trial' AND u.trial_expires_at < now()))
          AND (
            (SELECT count(*) FROM sniper_notifications n
             WHERE n.user_id = u.id AND n.sent_at > now() - interval '30 days') >= 3
          )
    """,
    # Segment 2: active trial users (trial not expired yet)
    2: """
        SELECT * FROM sniper_users
        WHERE subscription_tier = 'trial'
          AND trial_expires_at > now()
    """,
    # Segment 3: old expired users
    3: """
        SELECT * FROM sniper_users
        WHERE subscription_tier = 'expired'
          AND (status IS NULL OR status != 'banned')
    """,
}

MESSAGE_FILES = {
    1: 'scripts/broadcast_messages/segment1.html',
    2: 'scripts/broadcast_messages/segment2.html',
    3: 'scripts/broadcast_messages/segment3.html',
}


async def fetch_segment(segment: int) -> list[dict]:
    """Run segmentation query and return list of users."""
    query = SEGMENT_QUERIES[segment]
    async with DatabaseSession() as session:
        result = await session.execute(text(query))
        rows = result.mappings().all()
        return [dict(r) for r in rows]


def build_keyboard(broadcast_id: int, recipient_id: int) -> InlineKeyboardMarkup:
    """Build CTA keyboard with broadcast tracking encoded in callback_data."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Starter — 499 руб/мес",
            callback_data=f"bcast:{broadcast_id}:{recipient_id}:pay_starter"
        )],
        [InlineKeyboardButton(
            text="Все тарифы",
            callback_data=f"bcast:{broadcast_id}:{recipient_id}:tiers"
        )],
    ])


async def send_to_segment(segment: int, dry_run: bool = False):
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('BOT_TOKEN')
    if not bot_token:
        print("ERROR: BOT_TOKEN not set")
        return

    # init_database only if running standalone (not from bot.main)
    try:
        from database import get_database_url
        await init_database()
    except Exception:
        pass  # already initialized by bot.main

    # 1. Fetch users
    users = await fetch_segment(segment)
    print(f"Segment {segment}: {len(users)} users")
    for u in users[:10]:
        print(f"  - id={u['id']} username={u.get('username')} tier={u['subscription_tier']}")
    if len(users) > 10:
        print(f"  ... and {len(users) - 10} more")

    if not users:
        print("No users in segment.")
        return

    if dry_run:
        print("DRY RUN — no messages sent")
        return

    # 2. Read message
    message_path = Path(MESSAGE_FILES[segment])
    if not message_path.exists():
        print(f"ERROR: message file not found: {message_path}")
        return
    message = message_path.read_text(encoding='utf-8')

    # 3. Create broadcast record
    async with DatabaseSession() as session:
        bm = BroadcastMessage(
            message_text=message,
            target_tier=f'segment_{segment}',
            sent_at=datetime.utcnow(),
            total_recipients=len(users),
            successful=0,
            failed=0,
            created_by='pricing_broadcast_script',
        )
        session.add(bm)
        await session.flush()
        broadcast_id = bm.id
        await session.commit()

    print(f"Created broadcast id={broadcast_id}")

    # 4. Send to each user
    bot = Bot(token=bot_token)
    successful = 0
    failed = 0

    for user in users:
        # Create recipient row
        async with DatabaseSession() as session:
            recipient = BroadcastRecipient(
                broadcast_id=broadcast_id,
                user_id=user['id'],
                status='pending',
            )
            session.add(recipient)
            await session.flush()
            recipient_id = recipient.id
            await session.commit()

        keyboard = build_keyboard(broadcast_id, recipient_id)

        try:
            await bot.send_message(
                chat_id=user['telegram_id'],
                text=message,
                parse_mode='HTML',
                reply_markup=keyboard,
            )

            async with DatabaseSession() as session:
                await session.execute(
                    update(BroadcastRecipient)
                    .where(BroadcastRecipient.id == recipient_id)
                    .values(status='delivered', delivered_at=datetime.utcnow())
                )
                await session.commit()

            successful += 1
            print(f"  sent {user['telegram_id']} ({user.get('username')})")
        except Exception as e:
            failed += 1
            print(f"  FAIL {user['telegram_id']}: {e}")

        await asyncio.sleep(0.1)  # rate limit

    # 5. Update broadcast totals
    async with DatabaseSession() as session:
        await session.execute(
            update(BroadcastMessage)
            .where(BroadcastMessage.id == broadcast_id)
            .values(successful=successful, failed=failed)
        )
        await session.commit()

    print(f"\nDone. Successful: {successful}, Failed: {failed}")
    await bot.session.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--segment', type=int, required=True, choices=[1, 2, 3])
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    await send_to_segment(args.segment, dry_run=args.dry_run)


if __name__ == '__main__':
    asyncio.run(main())
