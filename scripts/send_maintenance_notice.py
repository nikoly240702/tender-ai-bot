"""
Рассылка уведомления о технических работах на zakupki.gov.ru.

Использование:
    # Тест на себе:
    python scripts/send_maintenance_notice.py --test-user 139459941

    # Отправить всем:
    python scripts/send_maintenance_notice.py --all
"""

import asyncio
import sys
import os
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest


MAINTENANCE_MESSAGE = """🔧 <b>Технические работы на сайте Госзакупок</b>

ЕИС «Закупки» проводит регламентные работы:
📅 с <b>10:00 14 марта</b> до <b>21:00 15 марта</b> (МСК)

Это не проблема нашего бота — сам сайт zakupki.gov.ru временно недоступен для всех.

⏸ Уведомления о новых тендерах <b>приостановлены</b> на это время.
✅ После завершения работ мониторинг <b>возобновится автоматически</b>.

Приносим извинения за неудобства 🙏"""


async def send_to_user(bot: Bot, telegram_id: int) -> bool:
    try:
        await bot.send_message(chat_id=telegram_id, text=MAINTENANCE_MESSAGE, parse_mode="HTML")
        return True
    except TelegramForbiddenError:
        print(f"  ⚠️  {telegram_id} заблокировал бота")
        return False
    except TelegramBadRequest as e:
        print(f"  ⚠️  {telegram_id} bad request: {e}")
        return False
    except Exception as e:
        print(f"  ❌ {telegram_id} ошибка: {e}")
        return False


async def get_all_telegram_ids() -> list[int]:
    from database import init_database, DatabaseSession
    from sqlalchemy import text
    await init_database()
    async with DatabaseSession() as session:
        # Берём всех активных пользователей (notifications_enabled=True, статус не banned)
        result = await session.execute(text(
            "SELECT telegram_id FROM sniper_users "
            "WHERE notifications_enabled = TRUE AND (status IS NULL OR status != 'banned')"
        ))
        return [row[0] for row in result if row[0]]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test-user', type=int, help='Telegram ID для теста')
    parser.add_argument('--all', action='store_true', help='Отправить всем активным пользователям')
    args = parser.parse_args()

    bot_token = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('BOT_TOKEN')
    if not bot_token:
        print("❌ BOT_TOKEN не найден")
        return

    bot = Bot(token=bot_token)

    try:
        if args.test_user:
            print(f"📤 Тест → {args.test_user}")
            ok = await send_to_user(bot, args.test_user)
            print("✅ Отправлено" if ok else "❌ Ошибка")

        elif args.all:
            print("📋 Получаем список пользователей...")
            user_ids = await get_all_telegram_ids()
            print(f"   Найдено {len(user_ids)} активных пользователей\n")

            success, failed = 0, 0
            for uid in user_ids:
                ok = await send_to_user(bot, uid)
                if ok:
                    success += 1
                    print(f"  ✅ {uid}")
                else:
                    failed += 1
                await asyncio.sleep(0.05)  # ~20 сообщений/сек, в рамках лимитов Telegram

            print(f"\n📊 Итого: ✅ {success} отправлено, ❌ {failed} ошибок")
        else:
            parser.print_help()
    finally:
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
