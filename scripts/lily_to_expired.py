"""
One-shot migration: переводит Лилю (id=2) из basic в expired.
Запускается один раз перед удалением старого тарифа basic из кода.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, update
from database import DatabaseSession, SniperUser


async def main():
    async with DatabaseSession() as session:
        user = await session.scalar(
            select(SniperUser).where(SniperUser.id == 2)
        )
        if not user:
            print("ERROR: user id=2 не найден")
            return
        if user.username != 'lily_frankova':
            print(f"WARN: user id=2 не Лиля, а {user.username}. Прерываю.")
            return
        if user.subscription_tier == 'expired':
            print("Лиля уже expired, ничего не делаю.")
            return

        await session.execute(
            update(SniperUser)
            .where(SniperUser.id == 2)
            .values(subscription_tier='expired')
        )
        await session.commit()
        print(f"OK: Лиля переведена с {user.subscription_tier} → expired")


if __name__ == '__main__':
    asyncio.run(main())
