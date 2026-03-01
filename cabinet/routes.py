"""
Маршрутизация веб-кабинета — HTML-страницы и API.
"""

import os
import logging
from pathlib import Path
from aiohttp import web

from .auth import verify_telegram_login, generate_session_token, get_current_user, require_auth
from . import api

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / 'templates'


def setup_cabinet_routes(app: web.Application):
    """Регистрация маршрутов кабинета в aiohttp app."""

    # HTML Pages
    app.router.add_get('/cabinet/login', login_page)
    app.router.add_get('/cabinet/', dashboard_page)
    app.router.add_get('/cabinet/profile', profile_page)
    app.router.add_get('/cabinet/documents', documents_page)
    app.router.add_get('/cabinet/filters', filters_page)

    # Auth
    app.router.add_get('/cabinet/auth/telegram', telegram_auth_callback)
    app.router.add_get('/cabinet/logout', logout)

    # JSON API
    app.router.add_get('/cabinet/api/profile', api.get_profile)
    app.router.add_post('/cabinet/api/profile', api.save_profile)
    app.router.add_get('/cabinet/api/tenders', api.get_tenders)
    app.router.add_get('/cabinet/api/documents', api.get_documents)
    app.router.add_get('/cabinet/api/documents/{id}/download', api.download_document)
    app.router.add_post('/cabinet/api/documents/generate/{tender}', api.generate_documents)
    app.router.add_get('/cabinet/api/filters', api.get_filters)
    app.router.add_post('/cabinet/api/filters/{id}', api.update_filter)

    logger.info("Cabinet routes registered at /cabinet/*")


# ============================================
# HTML PAGE HANDLERS
# ============================================

async def login_page(request: web.Request) -> web.Response:
    """Страница входа через Telegram Login Widget."""
    user = await get_current_user(request)
    if user:
        raise web.HTTPFound('/cabinet/')

    return _render_template('login.html')


@require_auth
async def dashboard_page(request: web.Request) -> web.Response:
    """Главная страница кабинета — история тендеров."""
    return _render_template('dashboard.html')


@require_auth
async def profile_page(request: web.Request) -> web.Response:
    """Страница профиля компании."""
    return _render_template('profile.html')


@require_auth
async def documents_page(request: web.Request) -> web.Response:
    """Страница документов."""
    return _render_template('documents.html')


@require_auth
async def filters_page(request: web.Request) -> web.Response:
    """Страница фильтров."""
    return _render_template('filters.html')


# ============================================
# AUTH HANDLERS
# ============================================

async def telegram_auth_callback(request: web.Request) -> web.Response:
    """
    Callback от Telegram Login Widget.
    GET /cabinet/auth/telegram?id=...&first_name=...&hash=...
    """
    bot_token = os.getenv('BOT_TOKEN', '')
    if not bot_token:
        return web.Response(text="Bot token not configured", status=500)

    # Извлекаем параметры
    params = dict(request.query)
    if not params.get('id') or not params.get('hash'):
        return web.Response(text="Missing auth parameters", status=400)

    # Проверяем подпись
    if not verify_telegram_login(params, bot_token):
        return web.Response(text="Invalid auth data", status=403)

    telegram_id = int(params['id'])

    # Находим пользователя в БД
    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()
    user = await db.get_user_by_telegram_id(telegram_id)

    if not user:
        return web.Response(
            text="User not found. Please start the bot first: @TenderSniperBot",
            status=404
        )

    # Создаём сессию
    session_token = generate_session_token()
    ip = request.remote or request.headers.get('X-Forwarded-For', '')
    await db.create_web_session(
        user_id=user['id'],
        session_token=session_token,
        ip_address=ip,
    )

    # Устанавливаем cookie и редиректим
    response = web.HTTPFound('/cabinet/')
    response.set_cookie(
        'cabinet_session',
        session_token,
        max_age=30 * 24 * 3600,  # 30 дней
        httponly=True,
        samesite='Lax',
    )
    return response


async def logout(request: web.Request) -> web.Response:
    """Выход из кабинета."""
    session_token = request.cookies.get('cabinet_session')
    if session_token:
        from tender_sniper.database import get_sniper_db
        db = await get_sniper_db()
        await db.delete_web_session(session_token)

    response = web.HTTPFound('/cabinet/login')
    response.del_cookie('cabinet_session')
    return response


# ============================================
# TEMPLATE RENDERING
# ============================================

def _render_template(template_name: str) -> web.Response:
    """Отдаёт HTML-файл из templates/."""
    template_path = TEMPLATES_DIR / template_name
    if template_path.exists():
        return web.FileResponse(template_path)
    return web.Response(text=f"Template '{template_name}' not found", status=404)
