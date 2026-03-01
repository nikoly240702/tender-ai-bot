"""
Одноразовый скрипт: восстанавливает trial пользователям,
пострадавшим от бага в AccessControlMiddleware.

Баг: пользователь создавался без trial_expires_at →
SubscriptionMiddleware сразу ставил subscription_tier='expired'.

Критерий затронутых: subscription_tier='expired' AND trial_expires_at IS NULL
Логика: trial_expires_at = created_at + 14 дней (что должно было быть с начала).
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta
from sqlalchemy import select, update, and_
from database import DatabaseSession, SniperUser


async def restore_trials():
    async with DatabaseSession() as session:
        # Находим всех затронутых
        result = await session.execute(
            select(SniperUser).where(
                and_(
                    SniperUser.subscription_tier == 'expired',
                    SniperUser.trial_expires_at == None,
                )
            )
        )
        affected = result.scalars().all()

        if not affected:
            print("✅ Затронутых пользователей не найдено.")
            return

        print(f"Найдено затронутых: {len(affected)}")
        print(f"{'telegram_id':<15} {'username':<25} {'created_at'}")
        print("-" * 60)

        for u in affected:
            new_expires = u.created_at + timedelta(days=14)
            print(f"{u.telegram_id:<15} {(u.username or 'без username'):<25} {u.created_at.strftime('%d.%m.%Y')} → trial до {new_expires.strftime('%d.%m.%Y')}")

        print()
        confirm = input(f"Восстановить trial для {len(affected)} пользователей? [y/N]: ").strip().lower()
        if confirm != 'y':
            print("Отменено.")
            return

        # Обновляем одним запросом
        await session.execute(
            update(SniperUser)
            .where(
                and_(
                    SniperUser.subscription_tier == 'expired',
                    SniperUser.trial_expires_at == None,
                )
            )
            .values(
                subscription_tier='trial',
                trial_started_at=SniperUser.created_at,
                trial_expires_at=SniperUser.created_at + timedelta(days=14),
            )
        )

        print(f"✅ Trial восстановлен для {len(affected)} пользователей.")


if __name__ == '__main__':
    asyncio.run(restore_trials())
