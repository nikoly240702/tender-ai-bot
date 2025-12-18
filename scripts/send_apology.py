"""
Скрипт для отправки извинений пользователям после технических неполадок.

Использование:
    # Тест на себе:
    python scripts/send_apology.py --test-user TELEGRAM_ID

    # Отправить всем:
    python scripts/send_apology.py --all
"""

import asyncio
import sys
import os
import argparse
from pathlib import Path
from datetime import datetime

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from aiogram import Bot
from aiogram.types import BufferedInputFile
from tender_sniper.database import get_sniper_db
from tender_sniper.instant_search import InstantSearch


APOLOGY_MESSAGE = """
🔧 <b>Технические работы завершены</b>

Уважаемый пользователь!

Приносим извинения за временные неполадки. В период с 17 по 18 декабря некоторые HTML-отчеты по тендерам формировались некорректно (приходили пустыми).

✅ <b>Проблема устранена</b>

Мы улучшили алгоритм поиска для повышения точности и релевантности результатов.

Если у вас есть активные фильтры, вы можете запустить новый поиск через меню бота.

Спасибо за понимание! 🙏

<i>С уважением, команда Tender Sniper</i>
"""


async def send_apology_to_user(bot: Bot, telegram_id: int) -> bool:
    """Отправка извинения конкретному пользователю."""
    try:
        await bot.send_message(
            chat_id=telegram_id,
            text=APOLOGY_MESSAGE,
            parse_mode="HTML"
        )
        print(f"✅ Сообщение отправлено пользователю {telegram_id}")
        return True
    except Exception as e:
        print(f"❌ Ошибка отправки пользователю {telegram_id}: {e}")
        return False


async def send_test_search_report(bot: Bot, telegram_id: int, keywords: list):
    """Отправка тестового HTML отчета пользователю."""
    try:
        print(f"🔍 Выполняем поиск по: {keywords}")

        searcher = InstantSearch()

        # Создаем временный фильтр для поиска
        temp_filter = {
            'id': 0,
            'name': 'Тестовый поиск',
            'keywords': keywords,
            'exclude_keywords': [],
            'price_min': None,
            'price_max': None,
            'regions': [],
            'tender_types': [],
            'law_types': []
        }

        results = await searcher.search_by_filter(
            filter_data=temp_filter,
            max_tenders=20,
            expanded_keywords=[]
        )

        matches = results.get('matches', [])
        total_found = results.get('total_found', 0)

        print(f"📊 Найдено: {total_found} тендеров, прошло скоринг: {len(matches)}")

        if matches:
            # Генерируем HTML отчет
            html_content = searcher.generate_html_report(
                tenders=matches,
                filter_name='Тестовый поиск (после исправления)',
                stats=results.get('stats', {})
            )

            # Отправляем файл
            filename = f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
            file = BufferedInputFile(
                html_content.encode('utf-8'),
                filename=filename
            )

            await bot.send_document(
                chat_id=telegram_id,
                document=file,
                caption=f"📊 Тестовый отчет после исправления\n\n"
                       f"Ключевые слова: {', '.join(keywords)}\n"
                       f"Найдено: {len(matches)} тендеров"
            )
            print(f"✅ HTML отчет отправлен ({len(matches)} тендеров)")
        else:
            await bot.send_message(
                chat_id=telegram_id,
                text=f"⚠️ По запросу '{', '.join(keywords)}' не найдено релевантных тендеров.\n\n"
                     f"Всего тендеров от RSS: {total_found}\n"
                     f"Прошло скоринг: 0\n\n"
                     f"Попробуйте другие ключевые слова.",
                parse_mode="HTML"
            )
            print(f"⚠️ Нет результатов после скоринга")

    except Exception as e:
        print(f"❌ Ошибка поиска: {e}")
        import traceback
        traceback.print_exc()


async def get_all_users_with_filters():
    """Получение всех пользователей с активными фильтрами."""
    db = await get_sniper_db()
    filters = await db.get_all_active_filters()

    # Уникальные telegram_id
    user_ids = set()
    for f in filters:
        if f.get('telegram_id'):
            user_ids.add(f['telegram_id'])

    return list(user_ids)


async def main():
    parser = argparse.ArgumentParser(description='Отправка извинений пользователям')
    parser.add_argument('--test-user', type=int, help='Telegram ID для тестовой отправки')
    parser.add_argument('--all', action='store_true', help='Отправить всем пользователям')
    parser.add_argument('--with-report', action='store_true', help='Отправить тестовый отчет')
    parser.add_argument('--keywords', type=str, default='компьютеры,ноутбуки',
                       help='Ключевые слова для тестового отчета (через запятую)')

    args = parser.parse_args()

    bot_token = os.getenv('BOT_TOKEN')
    if not bot_token:
        print("❌ BOT_TOKEN не найден в .env")
        return

    bot = Bot(token=bot_token)

    try:
        if args.test_user:
            print(f"\n📤 Отправка тестового сообщения пользователю {args.test_user}...")
            await send_apology_to_user(bot, args.test_user)

            if args.with_report:
                keywords = [k.strip() for k in args.keywords.split(',')]
                await send_test_search_report(bot, args.test_user, keywords)

        elif args.all:
            print("\n📋 Получение списка пользователей...")
            user_ids = await get_all_users_with_filters()
            print(f"   Найдено {len(user_ids)} пользователей с активными фильтрами")

            confirm = input(f"\nОтправить сообщения {len(user_ids)} пользователям? (yes/no): ")
            if confirm.lower() != 'yes':
                print("Отменено")
                return

            success = 0
            failed = 0
            for uid in user_ids:
                if await send_apology_to_user(bot, uid):
                    success += 1
                else:
                    failed += 1
                await asyncio.sleep(0.5)  # Задержка чтобы не превысить лимиты

            print(f"\n📊 Итого: успешно {success}, ошибок {failed}")
        else:
            parser.print_help()
    finally:
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())
