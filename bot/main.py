"""
Главный файл Telegram бота для анализа тендеров.
"""

import asyncio
import logging
import sys
import os
from pathlib import Path

# Добавляем родительскую директорию в путь для импорта модулей системы
sys.path.insert(0, str(Path(__file__).parent.parent))

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from bot.config import BotConfig
from bot.handlers import start, search, history, admin, access_requests, sniper, sniper_search, admin_sniper, onboarding, inline_search
from bot.db import get_database
from bot.middlewares import AccessControlMiddleware, AdaptiveRateLimitMiddleware

# Импортируем Tender Sniper Service
from tender_sniper.service import TenderSniperService
from tender_sniper.config import is_tender_sniper_enabled
from tender_sniper.monitoring import init_sentry, capture_exception, flush_events

# Импортируем production infrastructure
from bot.health_check import start_health_check_server, update_health_status
from bot.env_validator import EnvValidator

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

    # ============================================
    # PRODUCTION: Валидация окружения
    # ============================================
    logger.info("🔍 Проверка переменных окружения...")
    EnvValidator.validate_and_exit_if_invalid(strict=False)

    # ============================================
    # PRODUCTION: Health Check Server
    # ============================================
    health_check_port = int(os.getenv('HEALTH_CHECK_PORT', '8080'))
    logger.info(f"🏥 Запуск health check сервера на порту {health_check_port}...")
    health_check_runner = await start_health_check_server(port=health_check_port)

    # Инициализация Sentry для мониторинга ошибок
    sentry_enabled = init_sentry(
        environment="production",
        traces_sample_rate=0.1,  # 10% трассировки
        profiles_sample_rate=0.1  # 10% профилирования
    )
    if sentry_enabled:
        logger.info("✅ Sentry мониторинг активирован")
        update_health_status("sentry", "ok")
    else:
        logger.info("ℹ️  Sentry мониторинг отключен (SENTRY_DSN не указан)")
        update_health_status("sentry", "disabled")

    # Проверяем конфигурацию
    try:
        BotConfig.validate()
        logger.info("✅ Конфигурация валидна")
        update_health_status("config", "ok")
    except ValueError as e:
        logger.error(f"❌ Ошибка конфигурации: {e}")
        update_health_status("config", f"error: {e}")
        capture_exception(e, level="fatal", tags={"component": "config"})
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
    try:
        await get_database()
        update_health_status("database", "ok")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        update_health_status("database", f"error: {e}")
        raise

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

    # Подключаем rate limiting для защиты от спама
    rate_limiter = AdaptiveRateLimitMiddleware(period=60, block_duration=300)
    dp.message.middleware(rate_limiter)
    dp.callback_query.middleware(rate_limiter)
    logger.info("✅ Rate Limiting активирован")

    # Логируем информацию о контроле доступа
    if BotConfig.ALLOWED_USERS:
        logger.info(f"🔐 Контроль доступа: включен ({len(BotConfig.ALLOWED_USERS)} пользователей)")
    else:
        logger.info("⚠️ Контроль доступа: выключен (бот доступен всем)")

    # Регистрируем роутеры
    dp.include_router(access_requests.router)  # Запросы доступа регистрируем первыми
    dp.include_router(admin.router)  # Админ-панель
    dp.include_router(admin_sniper.router)  # Расширенная админ-панель Tender Sniper
    dp.include_router(onboarding.router)  # Онбординг для новых пользователей
    dp.include_router(inline_search.router)  # Inline поиск и quick actions
    dp.include_router(sniper_search.router)  # Tender Sniper Search (новый workflow)
    dp.include_router(sniper.router)  # Tender Sniper (приоритет)
    dp.include_router(start.router)
    # Старые handlers временно отключены
    # dp.include_router(search.router)
    # dp.include_router(history.router)

    logger.info("🤖 Бот запускается...")

    # Инициализируем Tender Sniper Service (если включен)
    sniper_service = None
    sniper_task = None
    if is_tender_sniper_enabled():
        try:
            logger.info("🎯 Инициализация Tender Sniper Service...")
            sniper_service = TenderSniperService(
                bot_token=BotConfig.BOT_TOKEN,
                poll_interval=300,  # 5 минут
                max_tenders_per_poll=100
            )
            await sniper_service.initialize()

            # Запускаем мониторинг в фоновом режиме
            async def run_sniper():
                try:
                    await sniper_service.start()
                except Exception as e:
                    logger.error(f"❌ Ошибка Tender Sniper: {e}", exc_info=True)

            sniper_task = asyncio.create_task(run_sniper())
            logger.info("✅ Tender Sniper Service запущен в фоновом режиме")
            update_health_status("sniper_service", "ok")
        except Exception as e:
            logger.error(f"❌ Не удалось запустить Tender Sniper: {e}", exc_info=True)
            update_health_status("sniper_service", f"error: {e}")
    else:
        logger.info("ℹ️  Tender Sniper отключен в конфигурации")
        update_health_status("sniper_service", "disabled")

    try:
        # Удаляем старые webhook (если были)
        await bot.delete_webhook(drop_pending_updates=True)

        # Устанавливаем команды бота
        commands = [
            BotCommand(command="start", description="🏠 Главное меню"),
            BotCommand(command="sniper", description="🎯 Tender Sniper - поиск и мониторинг"),
            BotCommand(command="help", description="❓ Справка"),
        ]
        await bot.set_my_commands(commands)
        logger.info("✅ Команды бота установлены")
        update_health_status("bot", "ok")

        # Запускаем polling
        logger.info("✅ Бот успешно запущен!")
        update_health_status("bot", "running")
        await dp.start_polling(bot)

    except Exception as e:
        logger.error(f"❌ Ошибка при запуске бота: {e}", exc_info=True)
        update_health_status("bot", f"error: {e}")
        capture_exception(e, level="fatal", tags={"component": "main"})
    finally:
        # Останавливаем Tender Sniper если запущен
        if sniper_service:
            logger.info("🛑 Остановка Tender Sniper Service...")
            await sniper_service.stop()
        if sniper_task and not sniper_task.done():
            sniper_task.cancel()
            try:
                await sniper_task
            except asyncio.CancelledError:
                pass

        await bot.session.close()

        # Останавливаем health check сервер
        if health_check_runner:
            logger.info("🛑 Остановка health check сервера...")
            await health_check_runner.cleanup()

        # Отправляем все накопленные события в Sentry перед завершением
        flush_events(timeout=2)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")
