"""
Health Check endpoint для мониторинга и Railway/Docker.
+ YooKassa webhook для приёма платежей.

Поднимает простой HTTP сервер для проверки здоровья приложения.
"""

import logging
import asyncio
import os
from pathlib import Path
from aiohttp import web, ClientSession, ClientTimeout
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# ============================================
# REVERSE PROXY → uvicorn admin (127.0.0.1:8081)
# ============================================

ADMIN_UPSTREAM = os.getenv('ADMIN_UPSTREAM_URL', 'http://127.0.0.1:8081')
_HOP_HEADERS = {
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailers', 'transfer-encoding', 'upgrade', 'host',
    'content-encoding', 'content-length',
}


async def admin_proxy_handler(request: web.Request) -> web.Response:
    """Прокси /admin/* → ADMIN_UPSTREAM/*.

    Auth: ничего не проверяем — admin app сам требует HTTPBasic
    через ADMIN_USERNAME/ADMIN_PASSWORD env vars. Браузер кэширует
    после первого ввода.
    """
    sub_path = request.match_info.get('path', '')
    target_url = f"{ADMIN_UPSTREAM}/{sub_path}"
    if request.query_string:
        target_url += '?' + request.query_string

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_HEADERS
    }
    # X-Forwarded-* — подсказки upstream-у
    headers['X-Forwarded-Host'] = request.host
    headers['X-Forwarded-Proto'] = request.scheme
    headers['X-Forwarded-For'] = request.remote or ''

    body = await request.read() if request.method not in ('GET', 'HEAD') else None

    try:
        async with ClientSession(timeout=ClientTimeout(total=60)) as session:
            async with session.request(
                request.method,
                target_url,
                headers=headers,
                data=body,
                allow_redirects=False,
            ) as upstream_resp:
                resp_body = await upstream_resp.read()
                resp_headers = {
                    k: v for k, v in upstream_resp.headers.items()
                    if k.lower() not in _HOP_HEADERS
                }
                return web.Response(
                    body=resp_body,
                    status=upstream_resp.status,
                    headers=resp_headers,
                )
    except asyncio.TimeoutError:
        return web.Response(status=504, text='Admin upstream timeout')
    except Exception as e:
        logger.error(f'admin proxy error: {e}', exc_info=True)
        return web.Response(status=502, text=f'Admin upstream error: {e}')

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

        # Определяем общий статус: любой статус, не начинающийся с "error",
        # считается здоровым. Это включает "ok", "running", "disabled" и т.п.
        # Раньше проверка была строгая (только "ok*") и роняла healthcheck,
        # когда bot переходил в "running" (строка 607 в bot/main.py).
        all_ok = all(
            not str(check).startswith("error")
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
            days_from_metadata = metadata.get('days')  # Кол-во дней из метаданных платежа
            customer_email = (
                metadata.get('customer_email')
                or metadata.get('email')
                or (obj.get('receipt') or {}).get('customer', {}).get('email')
            )

            if not telegram_id or not tier:
                logger.warning(f"⚠️ Missing metadata in webhook: {data}")
                return web.json_response({"status": "error", "message": "Missing metadata"}, status=400)

            if tier not in ('starter', 'pro', 'premium', 'ai_unlimited'):
                logger.warning(f"⚠️ Unknown tier in webhook: {tier}")
                return web.json_response({"status": "error", "message": f"Unknown tier: {tier}"}, status=400)

            telegram_id = int(telegram_id)

            # Парсим days из метаданных (если есть)
            subscription_days = 30  # дефолт
            if days_from_metadata:
                try:
                    subscription_days = int(days_from_metadata)
                except ValueError:
                    pass

            logger.info(f"✅ Payment succeeded: {payment_id}, user={telegram_id}, tier={tier}, amount={amount}₽, days={subscription_days}")

            # Активируем подписку
            try:
                from tender_sniper.database.sqlalchemy_adapter import get_sniper_db
                from database import Payment

                db = await get_sniper_db()

                # Определяем лимиты для тарифа (days берём из метаданных)
                tier_limits = {
                    'starter': {'filters': 5, 'notifications': 50},
                    'pro': {'filters': 15, 'notifications': 9999},
                    'premium': {'filters': 30, 'notifications': 9999},
                }
                limits = tier_limits.get(tier, tier_limits['starter'])
                limits['days'] = subscription_days  # Используем дни из метаданных

                # Обновляем подписку пользователя
                user = await db.get_user_by_telegram_id(telegram_id)
                if user:
                    now = datetime.utcnow()
                    expires_at = now + timedelta(days=subscription_days)

                    if tier == 'ai_unlimited':
                        # AI Unlimited — аддон: не меняем основной тариф,
                        # только устанавливаем has_ai_unlimited + дату истечения
                        await db.activate_ai_unlimited(
                            user_id=user['id'],
                            days=subscription_days
                        )
                        # Записываем платёж в БД
                        await db.record_payment(
                            user_id=user['id'],
                            payment_id=payment_id,
                            amount=amount,
                            tier=tier,
                            status='succeeded'
                        )
                        logger.info(f"✅ AI Unlimited activated: user={telegram_id}, days={subscription_days}, expires={expires_at}")
                    else:
                        # Обычная подписка
                        # Получаем текущую дату окончания подписки
                        user_sub = await db.get_user_subscription_info(telegram_id)
                        current_expires = user_sub.get('trial_expires_at') if user_sub else None

                        # Если есть активная подписка - добавляем дни к ней
                        if current_expires:
                            if isinstance(current_expires, str):
                                try:
                                    current_expires = datetime.fromisoformat(current_expires.replace('Z', '+00:00'))
                                    if current_expires.tzinfo:
                                        current_expires = current_expires.replace(tzinfo=None)
                                except:
                                    current_expires = now

                            if current_expires > now:
                                expires_at = current_expires + timedelta(days=limits['days'])
                                logger.info(f"📅 Extending subscription: {current_expires} + {limits['days']} days = {expires_at}")
                            else:
                                expires_at = now + timedelta(days=limits['days'])
                        else:
                            expires_at = now + timedelta(days=limits['days'])

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

                        # Broadcast conversion attribution
                        try:
                            from bot.handlers.broadcast_tracking import attribute_conversion
                            await attribute_conversion(user_id=user['id'], payment_id=0)
                        except Exception as attr_err:
                            logger.error(f"Broadcast attribution error: {attr_err}")

                        # Save email and apply multi-account upgrade (Telegram <-> Max linking)
                        if customer_email:
                            try:
                                from tender_sniper.database.sqlalchemy_adapter import DatabaseSession
                                from database import SniperUser as SniperUserModel
                                from sqlalchemy import select, update as sa_update
                                from bot.utils.tier_priority import should_upgrade

                                async with DatabaseSession() as session:
                                    # Save email on paying user
                                    await session.execute(
                                        sa_update(SniperUserModel)
                                        .where(SniperUserModel.id == user['id'])
                                        .values(email=customer_email)
                                    )

                                    # Find siblings with same email
                                    siblings_result = await session.execute(
                                        select(SniperUserModel).where(
                                            SniperUserModel.email == customer_email,
                                            SniperUserModel.id != user['id'],
                                        )
                                    )
                                    siblings = siblings_result.scalars().all()

                                    for sibling in siblings:
                                        if should_upgrade(
                                            current_tier=sibling.subscription_tier,
                                            current_expires=sibling.trial_expires_at,
                                            new_tier=tier,
                                            new_expires=expires_at,
                                        ):
                                            await session.execute(
                                                sa_update(SniperUserModel)
                                                .where(SniperUserModel.id == sibling.id)
                                                .values(
                                                    subscription_tier=tier,
                                                    trial_expires_at=expires_at,
                                                    filters_limit=limits['filters'],
                                                    notifications_limit=limits['notifications'],
                                                )
                                            )
                                            logger.info(
                                                f"🔗 Multi-account upgrade: sibling user {sibling.id} "
                                                f"(tg={sibling.telegram_id}) → {tier}"
                                            )
                            except Exception as e:
                                logger.error(f"Failed multi-account email upgrade: {e}", exc_info=True)

                    # Помечаем что пользователь оплачивал (для скидки первого месяца)
                    try:
                        existing_data = user.get('data') or {}
                        if not existing_data.get('has_paid_before'):
                            existing_data['has_paid_before'] = True
                            await db.update_user_json_data(user['id'], existing_data)
                    except Exception as e:
                        logger.warning(f"Failed to set has_paid_before: {e}")

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

                            # Начисляем бонус рефереру если пользователь был приглашён
                            try:
                                from bot.handlers.referral import award_referral_payment_bonus
                                bonus_given = await award_referral_payment_bonus(telegram_id, bot)
                                if bonus_given:
                                    logger.info(f"🎁 Referral payment bonus awarded for user {telegram_id}")
                            except Exception as ref_e:
                                logger.error(f"Error awarding referral bonus: {ref_e}")

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


async def landing_handler(request):
    """
    Landing page: GET /

    Отдаёт HTML-лендинг из landing/index.html.
    """
    landing_path = Path(__file__).parent.parent / 'landing' / 'index.html'
    if landing_path.exists():
        return web.FileResponse(landing_path)
    # Fallback если файл не найден
    return web.Response(text="Tender Sniper — coming soon", content_type="text/html")


async def _process_bitrix24_ai_analyze(deal_id: str):
    """Фоновая задача: запускает AI анализ документации и обновляет сделку в Б24."""
    try:
        from tender_sniper.database import get_sniper_db
        db = await get_sniper_db()

        notif = await db.get_notification_by_bitrix24_deal_id(deal_id)
        if not notif:
            logger.warning(f"Bitrix24 webhook: notification not found for deal_id={deal_id}")
            return

        user_id = notif['user_id']
        tender_number = notif['tender_number']

        user = await db.get_user_by_id(user_id)
        if not user:
            logger.warning(f"Bitrix24 webhook: user {user_id} not found")
            return

        webhook_url = (user.get('data') or {}).get('bitrix24_webhook_url', '')
        if not webhook_url:
            logger.warning(f"Bitrix24 webhook: no webhook_url for user {user_id}")
            return

        subscription_tier = user.get('subscription_tier', 'trial')

        logger.info(f"Bitrix24 webhook: running AI analysis for tender={tender_number}, deal={deal_id}")
        from bot.handlers.webapp import _run_ai_analysis
        formatted, is_ai, extraction = await _run_ai_analysis(tender_number, subscription_tier)

        from bot.handlers.bitrix24 import update_bitrix24_deal_ai_results
        await update_bitrix24_deal_ai_results(webhook_url, deal_id, extraction, formatted)
        logger.info(f"Bitrix24 webhook: deal {deal_id} updated with AI results")

    except Exception as e:
        logger.error(f"Bitrix24 AI analyze webhook error for deal {deal_id}: {e}", exc_info=True)


async def bitrix24_analyze_handler(request):
    """
    POST /webhook/bitrix24/analyze — вебхук от Битрикс24 для запуска AI анализа.

    Битрикс24 вызывает этот URL при смене стадии (через Автоматизацию).
    Параметры:
        ?secret=TOKEN  — опциональная защита (env BITRIX24_ANALYZE_SECRET)
    Body JSON: {"deal_id": "123"}  или {"document_id": ["DEAL", "123"]}
    """
    secret = request.query.get('secret', '')
    expected = os.environ.get('BITRIX24_ANALYZE_SECRET', '')
    if expected and secret != expected:
        return web.json_response({'error': 'Unauthorized'}, status=401)

    deal_id = ''
    try:
        body = await request.json()
        deal_id = str(
            body.get('deal_id') or
            body.get('id') or
            (body.get('document_id', [None, None])[1] if isinstance(body.get('document_id'), list) else '') or
            body.get('data', {}).get('FIELDS', {}).get('ID', '')
        ).strip()
    except Exception:
        try:
            params = dict(await request.post())
            deal_id = str(params.get('deal_id') or params.get('id', '')).strip()
        except Exception:
            pass

    if not deal_id or deal_id == 'None':
        return web.json_response({'error': 'deal_id required'}, status=400)

    # Запускаем анализ в фоне — сразу отвечаем Битриксу OK
    asyncio.create_task(_process_bitrix24_ai_analyze(deal_id))
    return web.json_response({'ok': True, 'deal_id': deal_id})


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

    # Битрикс24 → AI анализ документации
    app.router.add_post('/webhook/bitrix24/analyze', bitrix24_analyze_handler)

    # Корневой endpoint — лендинг
    app.router.add_get('/', landing_handler)

    # Web Cabinet (профиль, документы, тендеры)
    try:
        from cabinet import setup_cabinet_routes
        setup_cabinet_routes(app)
        logger.info("Cabinet routes mounted at /cabinet/*")
    except Exception as e:
        logger.warning(f"Failed to mount cabinet routes: {e}")

    # Admin panel reverse-proxy: /admin/* → uvicorn 127.0.0.1:8081
    # Admin app требует HTTPBasic auth (ADMIN_USERNAME/PASSWORD из env).
    app.router.add_route('*', '/admin', admin_proxy_handler)
    app.router.add_route('*', '/admin/', admin_proxy_handler)
    app.router.add_route('*', '/admin/{path:.*}', admin_proxy_handler)
    logger.info(f"Admin proxy mounted at /admin/* → {ADMIN_UPSTREAM}")

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

    _health_status["status"] = "healthy"

    logger.info(f"✅ Health check server started on port {port}")
    logger.info(f"   GET http://0.0.0.0:{port}/ - Landing page")
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
