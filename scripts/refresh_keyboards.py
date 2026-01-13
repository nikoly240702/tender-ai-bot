#!/usr/bin/env python3
"""
Скрипт для принудительного обновления клавиатуры у всех пользователей.

Отправляет broadcast сообщение с новой ReplyKeyboardMarkup.

Запуск:
    python scripts/refresh_keyboards.py
"""

import asyncio
import os
import sys
import logging

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Актуальная главная клавиатура."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🔍 Мгновенный поиск"),
                KeyboardButton(text="📋 Мои фильтры"),
            ],
            [
                KeyboardButton(text="⭐ Избранное"),
                KeyboardButton(text="📦 Подписка"),
            ],
            [
                KeyboardButton(text="⚙️ Настройки"),
                KeyboardButton(text="❓ Помощь"),
            ],
        ],
        resize_keyboard=True,
        is_persistent=True
    )


async def refresh_all_keyboards():
    """Отправить новую клавиатуру всем пользователям."""

    bot_token = os.getenv('BOT_TOKEN')
    if not bot_token:
        logger.error("BOT_TOKEN not found in environment")
        return

    bot = Bot(token=bot_token)

    # Импортируем после загрузки env
    from database import DatabaseSession, SniperUser
    from sqlalchemy import select

    async with DatabaseSession() as session:
        result = await session.execute(
            select(SniperUser.telegram_id, SniperUser.username)
        )
        users = result.all()

    logger.info(f"Found {len(users)} users to update")

    success = 0
    failed = 0

    message_text = (
        "🔄 <b>Обновление бота!</b>\n\n"
        "Мы обновили интерфейс. Ваше меню было обновлено.\n\n"
        "Используйте кнопки ниже для навигации:"
    )

    keyboard = get_main_keyboard()

    for telegram_id, username in users:
        try:
            await bot.send_message(
                telegram_id,
                message_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            success += 1
            logger.info(f"✅ Updated keyboard for {username or telegram_id}")

            # Небольшая задержка чтобы не превысить лимиты Telegram
            await asyncio.sleep(0.05)

        except Exception as e:
            failed += 1
            logger.warning(f"❌ Failed to update {username or telegram_id}: {e}")

    await bot.session.close()

    logger.info(f"\n📊 Results: {success} success, {failed} failed")


if __name__ == "__main__":
    asyncio.run(refresh_all_keyboards())
