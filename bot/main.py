"""
Главный файл Telegram бота для анализа тендеров.
Version: 2.0.1
"""

import asyncio
import logging
import sys
import os
import signal
from pathlib import Path

# Добавляем родительскую директорию в путь для импорта модулей системы
sys.path.insert(0, str(Path(__file__).parent.parent))

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from bot.config import BotConfig
# search и history удалены - их функционал заменён на sniper_search
from bot.handlers import start, admin, sniper, sniper_search, admin_sniper, onboarding, inline_search, all_tenders, tender_actions, user_management, menu_priority
# Новый упрощённый wizard
from bot.handlers import sniper_wizard_new
# Подписки (Phase 2.1)
from bot.handlers import subscriptions
# Реферальная программа
from bot.handlers import referral
# Engagement Scheduler (follow-ups, digest, deadline reminders)
from bot.engagement_scheduler import engagement_router, EngagementScheduler
from bot.db import get_database
from bot.middlewares import AccessControlMiddleware, AdaptiveRateLimitMiddleware, SubscriptionMiddleware

# Импортируем Tender Sniper Service
from tender_sniper.service import TenderSniperService
from tender_sniper.config import is_tender_sniper_enabled
# Subscription expiration checker
from bot.subscription_checker import SubscriptionChecker
from tender_sniper.monitoring import (
    init_sentry, capture_exception, flush_events,
    init_telegram_error_alerts, send_error_to_telegram
)

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


# ============================================
# GRACEFUL SHUTDOWN HANDLER
# ============================================

class GracefulShutdown:
    """
    Обработчик graceful shutdown для безопасной остановки бота.

    При получении SIGTERM/SIGINT:
    1. Устанавливает флаг shutdown_requested
    2. Ждет завершения текущих задач (макс 30 секунд)
    3. Останавливает event loop
    """

    def __init__(self):
        self.shutdown_requested = False
        self.shutdown_timeout = 30  # секунд

    async def shutdown(self, signal_type, loop):
        """Обработка сигнала завершения."""
        logger.info(f"⚠️  Получен сигнал {signal_type.name}, начинаем graceful shutdown...")
        self.shutdown_requested = True

        # Получаем все активные задачи кроме текущей
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

        if tasks:
            logger.info(f"⏳ Ожидаем завершения {len(tasks)} задач (макс {self.shutdown_timeout}с)...")

            # Ждем завершения задач с таймаутом
            done, pending = await asyncio.wait(
                tasks,
                timeout=self.shutdown_timeout,
                return_when=asyncio.ALL_COMPLETED
            )

            if pending:
                logger.warning(f"⚠️  {len(pending)} задач не успели завершиться за {self.shutdown_timeout}с")
                # Отменяем незавершенные задачи
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            else:
                logger.info(f"✅ Все {len(done)} задач успешно завершены")

        logger.info("✅ Graceful shutdown завершен")
        loop.stop()


def run_migrations():
    """Запускаем миграции Alembic перед стартом приложения."""
    import subprocess

    logger.info("=" * 70)
    logger.info("🔄 ЗАПУСК МИГРАЦИЙ ALEMBIC")
    logger.info("=" * 70)

    try:
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            check=True
        )

        logger.info("✅ Миграции выполнены успешно!")
        if result.stdout:
            logger.info(f"Вывод:\n{result.stdout}")

    except subprocess.CalledProcessError as e:
        logger.error(f"❌ ОШИБКА МИГРАЦИЙ: {e}")
        logger.error(f"Stdout: {e.stdout}")
        logger.error(f"Stderr: {e.stderr}")
        raise RuntimeError("Миграции не прошли! Останавливаем приложение.") from e
    except FileNotFoundError:
        logger.error("❌ alembic не найден! Проверьте установку.")
        raise

    logger.info("=" * 70)


async def main():
    """Главная функция запуска бота."""

    # ============================================
    # PRODUCTION: Миграции базы данных
    # ============================================
    run_migrations()

    # ============================================
    # PRODUCTION: Graceful Shutdown Handler
    # ============================================
    shutdown_handler = GracefulShutdown()
    loop = asyncio.get_running_loop()

    # Регистрируем обработчики сигналов
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda s=sig: asyncio.create_task(shutdown_handler.shutdown(s, loop))
        )

    logger.info("✅ Graceful shutdown handler зарегистрирован")

    # ============================================
    # PRODUCTION: Валидация окружения
    # ============================================
    logger.info("🔍 Проверка переменных окружения...")
    EnvValidator.validate_and_exit_if_invalid(strict=False)

    # ============================================
    # PRODUCTION: Health Check Server
    # ============================================
    # Если админ-панель запущена отдельно (ADMIN_PANEL_ENABLED=1), она обрабатывает /health
    health_check_runner = None
    if not os.getenv('ADMIN_PANEL_ENABLED'):
        health_check_port = int(os.getenv('HEALTH_CHECK_PORT', '8080'))
        logger.info(f"🏥 Запуск health check сервера на порту {health_check_port}...")
        health_check_runner = await start_health_check_server(port=health_check_port)
    else:
        logger.info("ℹ️  Health check делегирован Admin Panel")

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

    # Инициализируем Telegram уведомления об ошибках для админа
    admin_id = int(os.getenv('ADMIN_TELEGRAM_ID', '0'))
    if admin_id:
        init_telegram_error_alerts(admin_chat_id=admin_id)
        logger.info(f"✅ Telegram error alerts настроены для админа {admin_id}")
    else:
        logger.info("ℹ️  Telegram error alerts отключены (ADMIN_TELEGRAM_ID не указан)")

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

    # ОТКРЫТЫЙ ДОСТУП: все пользователи регистрируются автоматически
    # Блокировка и управление тарифами через админ-панель (/admin)

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

    # Подключаем проверку подписки
    subscription_middleware = SubscriptionMiddleware()
    dp.message.middleware(subscription_middleware)
    dp.callback_query.middleware(subscription_middleware)
    logger.info("✅ Subscription Middleware активирован")

    # Логируем информацию о контроле доступа
    logger.info("🔓 Режим доступа: ОТКРЫТЫЙ (все пользователи регистрируются автоматически)")
    if BotConfig.ADMIN_USER_ID:
        logger.info(f"👑 Админ: {BotConfig.ADMIN_USER_ID}")
    else:
        logger.warning("⚠️ ADMIN_USER_ID не задан - управление пользователями недоступно")

    # Регистрируем роутеры
    # ВАЖНО: menu_priority ПЕРВЫМ - для обработки кнопок меню в любом FSM состоянии
    dp.include_router(menu_priority.router)
    dp.include_router(admin.router)  # Админ-панель
    dp.include_router(admin_sniper.router)  # Расширенная админ-панель Tender Sniper
    dp.include_router(onboarding.router)  # Онбординг для новых пользователей
    dp.include_router(tender_actions.router)  # Inline кнопки для тендеров (детали, избранное, скрыть)
    dp.include_router(user_management.router)  # Команды /favorites, /hidden, /stats, /settings
    dp.include_router(inline_search.router)  # Inline поиск и quick actions
    dp.include_router(all_tenders.router)  # Все мои тендеры - единая история
    dp.include_router(sniper_wizard_new.router)  # Новый упрощённый wizard (feature flag)
    dp.include_router(subscriptions.router)  # Подписки (Phase 2.1)
    dp.include_router(referral.router)  # Реферальная программа
    dp.include_router(engagement_router)  # Engagement (digest, deadlines)
    dp.include_router(sniper_search.router)  # Tender Sniper Search (старый workflow)
    dp.include_router(sniper.router)  # Tender Sniper меню
    dp.include_router(start.router)

    # Глобальный обработчик ошибок
    @dp.error()
    async def error_handler(event):
        """Глобальный обработчик необработанных исключений."""
        exception = event.exception
        logger.error(f"❌ Необработанная ошибка: {exception}", exc_info=True)
        capture_exception(exception, level="error", tags={"component": "handler"})

        # Пытаемся уведомить пользователя
        try:
            update = event.update
            if update:
                if update.message:
                    await update.message.answer(
                        "❌ Произошла ошибка. Попробуйте /start для перезапуска."
                    )
                elif update.callback_query:
                    await update.callback_query.answer(
                        "❌ Ошибка. Попробуйте /start",
                        show_alert=True
                    )
        except Exception as notify_error:
            logger.error(f"Не удалось уведомить пользователя об ошибке: {notify_error}")

        return True  # Ошибка обработана

    logger.info("✅ Глобальный error handler зарегистрирован")
    logger.info("🤖 Бот запускается...")

    # Запускаем Subscription Checker (проверка истекающих подписок)
    subscription_checker = None
    subscription_checker_task = None
    try:
        logger.info("🔔 Запуск Subscription Checker...")
        subscription_checker = SubscriptionChecker(
            bot_token=BotConfig.BOT_TOKEN,
            check_interval_hours=6  # Проверка каждые 6 часов
        )

        async def run_subscription_checker():
            try:
                await subscription_checker.start()
            except Exception as e:
                logger.error(f"❌ Ошибка Subscription Checker: {e}", exc_info=True)

        subscription_checker_task = asyncio.create_task(run_subscription_checker())
        logger.info("✅ Subscription Checker запущен в фоновом режиме")
    except Exception as e:
        logger.error(f"❌ Не удалось запустить Subscription Checker: {e}", exc_info=True)

    # Запускаем Engagement Scheduler (follow-ups, digest, deadline reminders)
    engagement_scheduler = None
    engagement_scheduler_task = None
    try:
        logger.info("📅 Запуск Engagement Scheduler...")
        engagement_scheduler = EngagementScheduler(
            bot_token=BotConfig.BOT_TOKEN
        )

        async def run_engagement_scheduler():
            try:
                await engagement_scheduler.start()
            except Exception as e:
                logger.error(f"❌ Ошибка Engagement Scheduler: {e}", exc_info=True)

        engagement_scheduler_task = asyncio.create_task(run_engagement_scheduler())
        logger.info("✅ Engagement Scheduler запущен в фоновом режиме")
    except Exception as e:
        logger.error(f"❌ Не удалось запустить Engagement Scheduler: {e}", exc_info=True)

    # Запускаем Data Cleanup Scheduler (очистка старых данных)
    data_cleanup_task = None
    try:
        logger.info("🗑️ Запуск Data Cleanup Scheduler...")

        async def run_data_cleanup():
            """Периодическая очистка старых данных (раз в 24 часа)."""
            while True:
                try:
                    await asyncio.sleep(24 * 60 * 60)  # Раз в сутки

                    from tender_sniper.database import get_sniper_db
                    db = await get_sniper_db()

                    # Очищаем уведомления старше 60 дней для всех пользователей
                    from database import DatabaseSession, SniperNotification
                    from sqlalchemy import delete
                    from datetime import datetime, timedelta

                    cutoff_date = datetime.utcnow() - timedelta(days=60)

                    async with DatabaseSession() as session:
                        result = await session.execute(
                            delete(SniperNotification).where(
                                SniperNotification.sent_at < cutoff_date
                            )
                        )
                        deleted_count = result.rowcount
                        await session.commit()

                    if deleted_count > 0:
                        logger.info(f"🗑️ Data Cleanup: удалено {deleted_count} старых уведомлений (>60 дней)")
                    else:
                        logger.debug("🗑️ Data Cleanup: нет старых данных для удаления")

                except Exception as e:
                    logger.error(f"❌ Ошибка Data Cleanup: {e}", exc_info=True)

        data_cleanup_task = asyncio.create_task(run_data_cleanup())
        logger.info("✅ Data Cleanup Scheduler запущен (каждые 24 часа)")
    except Exception as e:
        logger.error(f"❌ Не удалось запустить Data Cleanup: {e}", exc_info=True)

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
        # Отправляем критическую ошибку в Telegram админу
        await send_error_to_telegram(e, context="Запуск бота (main)")
    finally:
        # Останавливаем Subscription Checker если запущен
        if subscription_checker:
            logger.info("🛑 Остановка Subscription Checker...")
            await subscription_checker.stop()
        if subscription_checker_task and not subscription_checker_task.done():
            subscription_checker_task.cancel()
            try:
                await subscription_checker_task
            except asyncio.CancelledError:
                pass

        # Останавливаем Engagement Scheduler если запущен
        if engagement_scheduler:
            logger.info("🛑 Остановка Engagement Scheduler...")
            await engagement_scheduler.stop()
        if engagement_scheduler_task and not engagement_scheduler_task.done():
            engagement_scheduler_task.cancel()
            try:
                await engagement_scheduler_task
            except asyncio.CancelledError:
                pass

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
