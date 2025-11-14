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
from bot.handlers import start, search, history, admin
from bot.db import get_database
from bot.middlewares import AccessControlMiddleware

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

    # Проверяем наличие прокси
    import os
    proxy_url = os.getenv('PROXY_URL', '').strip()
    if proxy_url:
        # Скрываем пароль в логах
        safe_proxy = proxy_url.split('@')[-1] if '@' in proxy_url else proxy_url
        logger.info(f"🔐 Прокси настроен: {safe_proxy}")
    else:
        logger.info("⚠️ Прокси не настроен - будут использоваться mock-данные")

    # Инициализируем базу данных
    logger.info("🗄️  Инициализация базы данных...")
    await get_database()

    # Синхронизируем пользователей из переменной окружения ALLOWED_USERS в базу данных
    if BotConfig.ALLOWED_USERS:
        from bot.database.access_manager import AccessManager
        access_manager = AccessManager()
        access_manager.sync_from_env()
        logger.info("✅ Пользователи из ALLOWED_USERS синхронизированы с базой данных")

    # Инициализируем бота и диспетчер
    bot = Bot(token=BotConfig.BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Подключаем middleware для контроля доступа
    access_middleware = AccessControlMiddleware()
    dp.message.middleware(access_middleware)
    dp.callback_query.middleware(access_middleware)

    # Логируем информацию о контроле доступа
    if BotConfig.ALLOWED_USERS:
        logger.info(f"🔐 Контроль доступа: включен ({len(BotConfig.ALLOWED_USERS)} пользователей)")
    else:
        logger.info("⚠️ Контроль доступа: выключен (бот доступен всем)")

    # Регистрируем роутеры
    dp.include_router(admin.router)  # Админ-панель регистрируем первой
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
