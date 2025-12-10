"""
Health Check endpoint для мониторинга и Railway/Docker.

Поднимает простой HTTP сервер для проверки здоровья приложения.
"""

import logging
import asyncio
from aiohttp import web
from datetime import datetime

logger = logging.getLogger(__name__)

# Глобальные переменные для отслеживания состояния
_health_status = {
    "status": "starting",
    "started_at": datetime.utcnow().isoformat(),
    "checks": {}
}


async def health_check_handler(request):
    """
    Health check endpoint: GET /health

    Returns:
        200 OK если все системы работают
        503 Service Unavailable если есть проблемы
    """
    try:
        # Проверяем database connection
        try:
            from tender_sniper.database.sqlalchemy_adapter import DatabaseSession
            from sqlalchemy import text
            async with DatabaseSession() as session:
                await session.execute(text("SELECT 1"))
            _health_status["checks"]["database"] = "ok"
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            _health_status["checks"]["database"] = f"error: {str(e)}"

        # Проверяем Tender Sniper Service
        try:
            from tender_sniper.service import TenderSniperService
            # Проверка будет добавлена если сервис экспортирует is_running
            _health_status["checks"]["sniper_service"] = "ok"
        except Exception as e:
            _health_status["checks"]["sniper_service"] = f"error: {str(e)}"

        # Определяем общий статус
        all_ok = all(
            check == "ok" or check.startswith("ok")
            for check in _health_status["checks"].values()
        )

        _health_status["status"] = "healthy" if all_ok else "degraded"
        _health_status["timestamp"] = datetime.utcnow().isoformat()

        status_code = 200 if all_ok else 503

        return web.json_response(_health_status, status=status_code)

    except Exception as e:
        logger.error(f"Health check error: {e}", exc_info=True)
        return web.json_response(
            {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            },
            status=500
        )


async def readiness_handler(request):
    """
    Readiness check endpoint: GET /ready

    Проверяет, готово ли приложение принимать запросы.
    """
    if _health_status["status"] in ["healthy", "degraded"]:
        return web.json_response({"ready": True}, status=200)
    else:
        return web.json_response({"ready": False}, status=503)


async def liveness_handler(request):
    """
    Liveness check endpoint: GET /live

    Проверяет, жив ли процесс (для Kubernetes/Railway).
    """
    return web.json_response({"alive": True}, status=200)


async def start_health_check_server(port: int = 8080):
    """
    Запуск health check HTTP сервера.

    Args:
        port: Порт для health check endpoint (default: 8080)
    """
    app = web.Application()

    # Регистрируем endpoints
    app.router.add_get('/health', health_check_handler)
    app.router.add_get('/ready', readiness_handler)
    app.router.add_get('/live', liveness_handler)

    # Корневой endpoint
    app.router.add_get('/', health_check_handler)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

    _health_status["status"] = "healthy"

    logger.info(f"✅ Health check server started on port {port}")
    logger.info(f"   GET http://0.0.0.0:{port}/health - Full health check")
    logger.info(f"   GET http://0.0.0.0:{port}/ready - Readiness probe")
    logger.info(f"   GET http://0.0.0.0:{port}/live - Liveness probe")

    return runner


def update_health_status(component: str, status: str):
    """
    Обновление статуса компонента.

    Args:
        component: Название компонента (database, bot, sniper_service)
        status: Статус ('ok', 'error: ...', 'degraded')
    """
    _health_status["checks"][component] = status
    logger.debug(f"Health status updated: {component} = {status}")


__all__ = [
    'start_health_check_server',
    'update_health_status'
]
