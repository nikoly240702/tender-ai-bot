"""
Маршрутизация веб-кабинета — HTML-страницы и API.
"""

import os
import logging
from pathlib import Path
from aiohttp import web
import aiohttp_jinja2
import jinja2

from .auth import verify_telegram_login, generate_session_token, get_current_user, require_auth
from . import api

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / 'templates'
STATIC_DIR = Path(__file__).parent / 'static'


def setup_cabinet_routes(app: web.Application):
    """Регистрация маршрутов кабинета в aiohttp app."""

    aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        auto_reload=False,
    )
    app.router.add_static('/cabinet/static/', path=str(STATIC_DIR), name='cabinet_static')

    # HTML Pages
    app.router.add_get('/cabinet/login', login_page)
    app.router.add_get('/cabinet/', dashboard_page)
    app.router.add_get('/cabinet/profile', profile_page)
    app.router.add_get('/cabinet/documents', documents_page)
    app.router.add_get('/cabinet/filters', filters_page)
    app.router.add_get('/cabinet/search', search_page)
    app.router.add_get('/cabinet/stats', stats_page)
    app.router.add_get('/cabinet/settings', settings_page)
    app.router.add_get('/cabinet/gpt', gpt_page)
    app.router.add_get('/cabinet/subscription', subscription_page)
    app.router.add_get('/cabinet/calendar', calendar_page)

    # Auth
    app.router.add_get('/cabinet/auth/telegram', telegram_auth_callback)
    app.router.add_get('/cabinet/logout', logout)

    # JSON API — Profile
    app.router.add_get('/cabinet/api/profile', api.get_profile)
    app.router.add_post('/cabinet/api/profile', api.save_profile)
    # JSON API — Tenders
    app.router.add_get('/cabinet/api/tenders', api.get_tenders)
    # JSON API — Documents
    app.router.add_get('/cabinet/api/documents', api.get_documents)
    app.router.add_get('/cabinet/api/documents/{id}/download', api.download_document)
    app.router.add_post('/cabinet/api/documents/generate/{tender}', api.generate_documents)
    # JSON API — Filters CRUD
    app.router.add_get('/cabinet/api/filters', api.get_filters)
    app.router.add_post('/cabinet/api/filters/create', api.create_filter)
    app.router.add_put('/cabinet/api/filters/{id}', api.update_filter)
    app.router.add_post('/cabinet/api/filters/{id}', api.update_filter)  # fallback for browsers
    app.router.add_delete('/cabinet/api/filters/{id}', api.delete_filter)
    app.router.add_post('/cabinet/api/filters/{id}/toggle', api.toggle_filter)
    # JSON API — Search
    app.router.add_get('/cabinet/api/search', api.search_tenders)
    app.router.add_get('/cabinet/api/regions', api.get_regions)
    # JSON API — Stats
    app.router.add_get('/cabinet/api/stats', api.get_stats)
    # JSON API — Tender actions
    app.router.add_post('/cabinet/api/tenders/{tender_number}/feedback', api.tender_feedback)
    app.router.add_post('/cabinet/api/tenders/{tender_number}/export-sheets', api.export_to_sheets)
    # JSON API — Settings
    app.router.add_get('/cabinet/api/settings', api.get_settings)
    app.router.add_post('/cabinet/api/settings', api.save_settings)
    # JSON API — Tender-GPT
    app.router.add_post('/cabinet/api/gpt/chat', api.api_gpt_chat)
    # JSON API — Subscription
    app.router.add_get('/cabinet/api/subscription', api.api_subscription_info)
    app.router.add_post('/cabinet/api/subscription/pay', api.api_subscription_pay)
    # JSON API — Calendar
    app.router.add_get('/cabinet/api/calendar', api.api_calendar)

    logger.info("Cabinet routes registered at /cabinet/*")


# ============================================
# HTML PAGE HANDLERS
# ============================================

async def login_page(request: web.Request) -> web.Response:
    """Страница входа через Telegram Login Widget."""
    user = await get_current_user(request)
    if user:
        raise web.HTTPFound('/cabinet/')

    return _render_template('login.html', request)


@require_auth
async def dashboard_page(request: web.Request) -> web.Response:
    """Главная страница кабинета — лента тендеров."""
    user = request['user']
    return _render_template(
        'dashboard.html',
        request,
        active_page='dashboard',
        user_name=user.get('username') or user.get('first_name') or 'Вы',
        user_tier=user.get('subscription_tier', ''),
        nav_counts={},
    )


@require_auth
async def profile_page(request: web.Request) -> web.Response:
    """Страница профиля компании."""
    user = request['user']
    return _render_template(
        'profile.html', request,
        active_page='profile',
        user_name=user.get('username') or user.get('first_name') or 'Вы',
        user_tier=user.get('subscription_tier', ''),
        nav_counts={},
    )


@require_auth
async def documents_page(request: web.Request) -> web.Response:
    """Страница документов."""
    user = request['user']
    return _render_template(
        'documents.html', request,
        active_page='documents',
        user_name=user.get('username') or user.get('first_name') or 'Вы',
        user_tier=user.get('subscription_tier', ''),
        nav_counts={},
    )


@require_auth
async def filters_page(request: web.Request) -> web.Response:
    """Страница фильтров."""
    user = request['user']
    return _render_template(
        'filters.html',
        request,
        active_page='filters',
        user_name=user.get('username') or user.get('first_name') or 'Вы',
        user_tier=user.get('subscription_tier', ''),
        nav_counts={},
    )


@require_auth
async def search_page(request: web.Request) -> web.Response:
    """Страница поиска тендеров."""
    user = request['user']
    return _render_template(
        'search.html',
        request,
        active_page='search',
        user_name=user.get('username') or user.get('first_name') or 'Вы',
        user_tier=user.get('subscription_tier', ''),
        nav_counts={},
    )


@require_auth
async def stats_page(request: web.Request) -> web.Response:
    """Страница статистики."""
    user = request['user']
    return _render_template(
        'stats.html', request,
        active_page='stats',
        user_name=user.get('username') or user.get('first_name') or 'Вы',
        user_tier=user.get('subscription_tier', ''),
        nav_counts={},
    )


@require_auth
async def settings_page(request: web.Request) -> web.Response:
    """Страница настроек."""
    user = request['user']
    return _render_template(
        'settings.html',
        request,
        active_page='settings',
        user_name=user.get('username') or user.get('first_name') or 'Вы',
        user_tier=user.get('subscription_tier', ''),
        nav_counts={},
    )


@require_auth
async def gpt_page(request: web.Request) -> web.Response:
    """Страница Tender-GPT чата."""
    user = request['user']
    return _render_template(
        'gpt.html', request,
        active_page='gpt',
        user_name=user.get('username') or user.get('first_name') or 'Вы',
        user_tier=user.get('subscription_tier', ''),
        nav_counts={},
    )


@require_auth
async def subscription_page(request: web.Request) -> web.Response:
    """Страница подписки и оплаты."""
    user = request['user']
    return _render_template(
        'subscription.html', request,
        active_page='subscription',
        user_name=user.get('username') or user.get('first_name') or 'Вы',
        user_tier=user.get('subscription_tier', ''),
        nav_counts={},
    )


@require_auth
async def calendar_page(request: web.Request) -> web.Response:
    """Страница календаря дедлайнов."""
    user = request['user']
    return _render_template(
        'calendar.html', request,
        active_page='calendar',
        user_name=user.get('username') or user.get('first_name') or 'Вы',
        user_tier=user.get('subscription_tier', ''),
        nav_counts={},
    )


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

def _render_template(template_name: str, request: web.Request = None, **context) -> web.Response:
    """Рендерит шаблон через aiohttp-jinja2. context передаётся в шаблон."""
    return aiohttp_jinja2.render_template(template_name, request, context)
