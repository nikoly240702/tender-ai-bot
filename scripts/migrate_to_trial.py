#!/usr/bin/env python3
"""
Скрипт миграции существующих пользователей на триал 14 дней.

Запуск:
    python scripts/migrate_to_trial.py

Что делает:
1. Находит всех пользователей с tier='free' без trial_expires_at
2. Устанавливает им trial на 14 дней от текущей даты
3. Обновляет лимиты до trial уровня
"""

import asyncio
import logging
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, update
from database import SniperUser, DatabaseSession

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def migrate_users_to_trial():
    """Переводит всех free пользователей на триал 14 дней."""

    now = datetime.utcnow()
    expires = now + timedelta(days=14)

    logger.info("=" * 60)
    logger.info("МИГРАЦИЯ ПОЛЬЗОВАТЕЛЕЙ НА TRIAL")
    logger.info("=" * 60)

    async with DatabaseSession() as session:
        # Находим всех free пользователей без триала
        result = await session.execute(
            select(SniperUser).where(
                SniperUser.subscription_tier == 'free',
                SniperUser.trial_expires_at.is_(None)
            )
        )
        users = result.scalars().all()

        logger.info(f"Найдено {len(users)} пользователей для миграции")

        if not users:
            logger.info("Нет пользователей для миграции")
            return

        # Обновляем пользователей
        migrated = 0
        for user in users:
            try:
                await session.execute(
                    update(SniperUser)
                    .where(SniperUser.id == user.id)
                    .values(
                        subscription_tier='trial',
                        trial_started_at=now,
                        trial_expires_at=expires,
                        filters_limit=15,
                        notifications_limit=50
                    )
                )
                migrated += 1
                logger.info(f"  ✓ User {user.telegram_id} -> trial (expires {expires.date()})")
            except Exception as e:
                logger.error(f"  ✗ User {user.telegram_id}: {e}")

        logger.info("=" * 60)
        logger.info(f"Миграция завершена: {migrated}/{len(users)} пользователей")
        logger.info("=" * 60)


async def show_stats():
    """Показывает статистику по тарифам."""

    logger.info("\nСТАТИСТИКА ПО ТАРИФАМ:")

    async with DatabaseSession() as session:
        for tier in ['free', 'trial', 'basic', 'premium']:
            result = await session.execute(
                select(SniperUser).where(SniperUser.subscription_tier == tier)
            )
            count = len(result.scalars().all())
            logger.info(f"  {tier.upper()}: {count}")


async def main():
    """Главная функция."""

    import argparse
    parser = argparse.ArgumentParser(description='Миграция пользователей на trial')
    parser.add_argument('--dry-run', action='store_true', help='Только показать статистику')
    args = parser.parse_args()

    await show_stats()

    if args.dry_run:
        logger.info("\n--dry-run: Миграция не выполнена")
    else:
        print("\nВы уверены, что хотите мигрировать пользователей? (y/N): ", end="")
        confirm = input().strip().lower()

        if confirm == 'y':
            await migrate_users_to_trial()
            await show_stats()
        else:
            logger.info("Миграция отменена")


if __name__ == "__main__":
    asyncio.run(main())
