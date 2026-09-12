"""
Worker-сервис: только TenderSniperService (мэтчинг/скрапинг тендеров).

Вынесен из bot/main.py в отдельный Railway-сервис, чтобы CPU-тяжёлая
работа мэтчинга не конкурировала за GIL/event loop с HTTP-сервером
кабинета (см. docs/superpowers/specs/2026-09-11-matching-worker-separation-design.md).

Никогда не запускает aiogram Dispatcher/polling — TenderSniperService
уже полностью самодостаточен (свой Bot для отправки уведомлений, своё
подключение к БД), поэтому здесь нет и не должно быть роутеров/polling.
"""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.config import BotConfig
from bot.env_validator import EnvValidator
from bot.health_check import start_worker_health_check_server, update_health_status
from tender_sniper.config import is_tender_sniper_enabled
from tender_sniper.monitoring import (
    init_sentry, capture_exception, flush_events,
    init_telegram_error_alerts, send_error_to_telegram,
)
from tender_sniper.service import TenderSniperService

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


class GracefulShutdown:
    """Тот же паттерн, что в bot/main.py — не импортируем оттуда напрямую,
    чтобы worker не тянул за собой весь модуль bot.main (aiogram Dispatcher,
    все роутеры и т.д.) только ради одного вспомогательного класса."""

    def __init__(self):
        self.shutdown_timeout = 30

    async def shutdown(self, signal_type, loop):
        logger.info(f"⚠️  Получен сигнал {signal_type.name}, начинаем graceful shutdown...")
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if tasks:
            logger.info(f"⏳ Ожидаем завершения {len(tasks)} задач (макс {self.shutdown_timeout}с)...")
            done, pending = await asyncio.wait(
                tasks, timeout=self.shutdown_timeout, return_when=asyncio.ALL_COMPLETED
            )
            if pending:
                logger.warning(f"⚠️  {len(pending)} задач не успели завершиться, отменяем")
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
        logger.info("✅ Graceful shutdown завершён")
        loop.stop()


async def main():
    logger.info("🔍 Проверка переменных окружения...")
    EnvValidator.validate_and_exit_if_invalid(strict=False)

    health_check_runner = None
    health_check_port = int(os.getenv('HEALTH_CHECK_PORT', '8080'))
    logger.info(f"🏥 Запуск worker health check сервера на порту {health_check_port}...")
    health_check_runner = await start_worker_health_check_server(port=health_check_port)

    sentry_enabled = init_sentry(
        environment="production",
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
    )
    if sentry_enabled:
        logger.info("✅ Sentry мониторинг активирован")
        update_health_status("sentry", "ok")
    else:
        logger.info("ℹ️  Sentry мониторинг отключен (SENTRY_DSN не указан)")
        update_health_status("sentry", "disabled")

    admin_id = int(os.getenv('ADMIN_TELEGRAM_ID', '0'))
    if admin_id:
        init_telegram_error_alerts(admin_chat_id=admin_id)
        logger.info(f"✅ Telegram error alerts настроены для админа {admin_id}")

    try:
        BotConfig.validate()
        logger.info("✅ Конфигурация валидна")
    except ValueError as e:
        logger.error(f"❌ Ошибка конфигурации: {e}")
        capture_exception(e, level="fatal", tags={"component": "worker_config"})
        return

    shutdown_handler = GracefulShutdown()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig, lambda s=sig: asyncio.create_task(shutdown_handler.shutdown(s, loop))
        )
    logger.info("✅ Graceful shutdown handler зарегистрирован")

    sniper_service = None
    sniper_task = None
    if not is_tender_sniper_enabled():
        logger.warning("⚠️  Tender Sniper отключен в конфигурации — worker простаивает")
        update_health_status("sniper_service", "disabled")
    else:
        try:
            logger.info("🎯 Инициализация Tender Sniper Service...")
            sniper_service = TenderSniperService(
                bot_token=BotConfig.BOT_TOKEN,
                poll_interval=120,
                max_tenders_per_poll=100,
            )
            await sniper_service.initialize()

            async def run_sniper():
                try:
                    await sniper_service.start()
                except Exception as e:
                    logger.error(f"❌ Ошибка Tender Sniper: {e}", exc_info=True)

            sniper_task = asyncio.create_task(run_sniper())
            logger.info("✅ Tender Sniper Service запущен")
            update_health_status("sniper_service", "ok")
        except Exception as e:
            logger.error(f"❌ Не удалось запустить Tender Sniper: {e}", exc_info=True)
            update_health_status("sniper_service", f"error: {e}")
            capture_exception(e, level="fatal", tags={"component": "worker_main"})
            await send_error_to_telegram(e, context="Запуск worker (tender_sniper.worker_main)")

    try:
        if sniper_task:
            await sniper_task
        else:
            # Ничего не запущено (feature flag выключен) — держим процесс
            # живым, чтобы health check продолжал отвечать, а не крашился
            # в рестарт-луп.
            await asyncio.Event().wait()
    finally:
        if sniper_service:
            logger.info("🛑 Остановка Tender Sniper Service...")
            await sniper_service.stop()
        if health_check_runner:
            logger.info("🛑 Остановка health check сервера...")
            await health_check_runner.cleanup()
        flush_events(timeout=2)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Worker остановлен пользователем")
