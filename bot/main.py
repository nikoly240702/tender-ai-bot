"""
Главный файл Telegram бота для анализа тендеров.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Добавляем родительскую директорию в путь для импорта модулей системы
sys.path.insert(0, str(Path(__file__).parent.parent))

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import BotConfig
from bot.handlers import start, search, history
from bot.database import get_database

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent / 'bot.log')
    ]
)
logger = logging.getLogger(__name__)


async def main():
    """Главная функция запуска бота."""

    # Проверяем конфигурацию
    try:
        BotConfig.validate()
        logger.info("✅ Конфигурация валидна")
    except ValueError as e:
        logger.error(f"❌ Ошибка конфигурации: {e}")
        return

    # Инициализируем базу данных
    logger.info("🗄️  Инициализация базы данных...")
    await get_database()

    # Инициализируем бота и диспетчер
    bot = Bot(token=BotConfig.BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Регистрируем роутеры
    dp.include_router(start.router)
    dp.include_router(search.router)
    dp.include_router(history.router)

    logger.info("🤖 Бот запускается...")

    try:
        # Удаляем старые webhook (если были)
        await bot.delete_webhook(drop_pending_updates=True)

        # Запускаем polling
        logger.info("✅ Бот успешно запущен!")
        await dp.start_polling(bot)

    except Exception as e:
        logger.error(f"❌ Ошибка при запуске бота: {e}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")
