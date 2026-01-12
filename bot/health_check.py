"""
Health Check endpoint для мониторинга и Railway/Docker.
+ YooKassa webhook для приёма платежей.

Поднимает простой HTTP сервер для проверки здоровья приложения.
"""

import logging
import asyncio
import os
from aiohttp import web
from datetime import datetime, timedelta

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


async def yookassa_webhook_handler(request):
    """
    YooKassa webhook endpoint: POST /payment/webhook

    Обрабатывает уведомления об оплате от YooKassa.
    """
    try:
        data = await request.json()
        event = data.get('event')
        obj = data.get('object', {})

        logger.info(f"📥 YooKassa webhook received: {event}")

        if event == 'payment.succeeded':
            # Получаем данные платежа
            payment_id = obj.get('id')
            metadata = obj.get('metadata', {})
            amount = float(obj.get('amount', {}).get('value', 0))

            telegram_id = metadata.get('telegram_id')
            tier = metadata.get('tier')

            if not telegram_id or not tier:
                logger.warning(f"⚠️ Missing metadata in webhook: {data}")
                return web.json_response({"status": "error", "message": "Missing metadata"}, status=400)

            telegram_id = int(telegram_id)

            logger.info(f"✅ Payment succeeded: {payment_id}, user={telegram_id}, tier={tier}, amount={amount}₽")

            # Активируем подписку
            try:
                from tender_sniper.database.sqlalchemy_adapter import get_sniper_db
                from database import Payment

                db = await get_sniper_db()

                # Определяем лимиты для тарифа
                tier_limits = {
                    'basic': {'filters': 5, 'notifications': 100, 'days': 30},
                    'premium': {'filters': 20, 'notifications': 9999, 'days': 30},
                }
                limits = tier_limits.get(tier, tier_limits['basic'])

                # Обновляем подписку пользователя
                user = await db.get_user_by_telegram_id(telegram_id)
                if user:
                    # Вычисляем дату окончания подписки
                    expires_at = datetime.utcnow() + timedelta(days=limits['days'])

                    await db.update_user_subscription(
                        user_id=user['id'],
                        tier=tier,
                        filters_limit=limits['filters'],
                        notifications_limit=limits['notifications'],
                        expires_at=expires_at
                    )

                    # Записываем платёж в БД
                    await db.record_payment(
                        user_id=user['id'],
                        payment_id=payment_id,
                        amount=amount,
                        tier=tier,
                        status='succeeded'
                    )

                    logger.info(f"✅ Subscription activated: user={telegram_id}, tier={tier}, expires={expires_at}")

                    # Отправляем уведомление пользователю
                    try:
                        from aiogram import Bot
                        bot_token = os.getenv('BOT_TOKEN')
                        if bot_token:
                            bot = Bot(token=bot_token)
                            await bot.send_message(
                                telegram_id,
                                f"🎉 <b>Оплата прошла успешно!</b>\n\n"
                                f"Тариф: <b>{tier.capitalize()}</b>\n"
                                f"Сумма: <b>{amount:.0f} ₽</b>\n"
                                f"Действует до: <b>{expires_at.strftime('%d.%m.%Y')}</b>\n\n"
                                f"Спасибо за подписку! 🚀",
                                parse_mode="HTML"
                            )
                            await bot.session.close()
                    except Exception as e:
                        logger.error(f"Failed to send payment notification: {e}")
                else:
                    logger.warning(f"User not found: telegram_id={telegram_id}")

            except Exception as e:
                logger.error(f"❌ Failed to activate subscription: {e}", exc_info=True)
                return web.json_response({"status": "error", "message": str(e)}, status=500)

        elif event == 'payment.canceled':
            payment_id = obj.get('id')
            logger.info(f"❌ Payment canceled: {payment_id}")

        elif event == 'refund.succeeded':
            payment_id = obj.get('payment_id')
            logger.info(f"💸 Refund succeeded for payment: {payment_id}")

        return web.json_response({"status": "ok"})

    except Exception as e:
        logger.error(f"❌ Webhook error: {e}", exc_info=True)
        return web.json_response({"status": "error", "message": str(e)}, status=500)


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

    # YooKassa webhook
    app.router.add_post('/payment/webhook', yookassa_webhook_handler)

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
