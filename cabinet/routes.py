"""
Маршрутизация веб-кабинета — HTML-страницы и API.
"""

import os
import logging
from pathlib import Path
from aiohttp import web
import aiohttp_jinja2
import jinja2

from .auth import (
    verify_telegram_login, generate_session_token, get_current_user,
    require_auth, require_team_member,
)
from . import api

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / 'templates'
STATIC_DIR = Path(__file__).parent / 'static'


async def _global_ctx_processor(request):
    """Глобальный context для всех шаблонов: is_admin_user, ADMIN_USER_ID."""
    is_admin = False
    try:
        user = await get_current_user(request)
        if user:
            admin_id = int(os.getenv('ADMIN_USER_ID') or os.getenv('ADMIN_TELEGRAM_ID') or '0')
            is_admin = bool(admin_id and user.get('telegram_id') == admin_id)
    except Exception:
        pass
    return {'is_admin_user': is_admin}


def setup_cabinet_routes(app: web.Application):
    """Регистрация маршрутов кабинета в aiohttp app."""

    aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        auto_reload=False,
        context_processors=[_global_ctx_processor, aiohttp_jinja2.request_processor],
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
    app.router.add_get('/cabinet/pipeline', pipeline_page)
    app.router.add_get('/cabinet/pipeline/archive', pipeline_archive_page)
    app.router.add_get('/cabinet/team', team_page)
    app.router.add_get('/cabinet/invite/{token}', invite_page)

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
    app.router.add_get('/cabinet/api/filters/{id}/notify-targets', api.get_filter_notify_targets)
    app.router.add_post('/cabinet/api/filters/{id}/notify-targets', api.update_filter_notify_targets)
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
    # JSON API — Bitrix24 integration
    app.router.add_post('/cabinet/api/settings/bitrix24', api.save_bitrix24_settings)
    app.router.add_post('/cabinet/api/settings/bitrix24/test', api.test_bitrix24_settings)
    app.router.add_post('/cabinet/api/tenders/{tender_number}/bitrix24', api.export_tender_to_bitrix24)
    # JSON API — Tender-GPT
    app.router.add_post('/cabinet/api/gpt/chat', api.api_gpt_chat)
    # JSON API — Subscription
    app.router.add_get('/cabinet/api/subscription', api.api_subscription_info)
    app.router.add_post('/cabinet/api/subscription/pay', api.api_subscription_pay)
    # JSON API — Calendar
    app.router.add_get('/cabinet/api/calendar', api.api_calendar)

    # JSON API — Pipeline
    app.router.add_post('/cabinet/api/pipeline/from-feed/{tender_number}', api.pipeline_create_from_feed)
    app.router.add_post('/cabinet/api/pipeline/cards', api.pipeline_create_manual)
    app.router.add_get('/cabinet/api/pipeline/cards/{id}', api.pipeline_get_card)
    app.router.add_get('/cabinet/api/pipeline/cards/{id}/full', api.pipeline_card_full)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/stage', api.pipeline_move_stage)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/result', api.pipeline_set_result)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/assignee', api.pipeline_set_assignee)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/prices', api.pipeline_set_prices)
    app.router.add_delete('/cabinet/api/pipeline/cards/{id}', api.pipeline_delete_card)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/unarchive', api.pipeline_unarchive_card)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/notes', api.pipeline_add_note)
    app.router.add_get('/cabinet/api/pipeline/cards/{id}/files', api.pipeline_list_files)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/files', api.pipeline_upload_file)
    app.router.add_delete('/cabinet/api/pipeline/files/{fid}', api.pipeline_delete_file)
    app.router.add_get('/cabinet/api/pipeline/files/{fid}/download', api.pipeline_download_file)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/checklist', api.pipeline_add_checklist)
    app.router.add_patch('/cabinet/api/pipeline/checklist/{cid}', api.pipeline_toggle_checklist)
    app.router.add_delete('/cabinet/api/pipeline/checklist/{cid}', api.pipeline_delete_checklist)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/relations', api.pipeline_add_relation)
    app.router.add_delete('/cabinet/api/pipeline/relations/{rid}', api.pipeline_delete_relation)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/ai-enrich', api.pipeline_ai_enrich)

    # Holodilnik supplier search
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/holodilnik-search', api.holodilnik_start_search)
    app.router.add_get('/cabinet/api/pipeline/cards/{id}/holodilnik-status', api.holodilnik_get_status)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/holodilnik-toggle', api.holodilnik_toggle_select)

    # Supplier request: оценка по своему каталогу + очистка ТЗ
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/estimate-own', api.supplier_estimate)
    app.router.add_post('/cabinet/api/pipeline/cards/{id}/clean-request', api.supplier_clean_request)

    # JSON API — Team
    app.router.add_get('/cabinet/api/team/members', api.team_get_members)
    app.router.add_delete('/cabinet/api/team/members/{id}', api.team_remove_member)
    app.router.add_post('/cabinet/api/team/leave', api.team_leave)
    app.router.add_get('/cabinet/api/team/invites', api.team_list_invites)
    app.router.add_post('/cabinet/api/team/invites', api.team_create_invite)
    app.router.add_delete('/cabinet/api/team/invites/{id}', api.team_revoke_invite)
    app.router.add_get('/cabinet/api/team/dashboard', api.team_dashboard)

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


# ============================================
# PIPELINE PAGES
# ============================================

_RU_MONTHS = (
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
)


def _format_deadline_short(value) -> str:
    """Парсит ISO/datetime и возвращает 'D месяца' (с годом если не текущий).
    Возвращает '' если не разобралось."""
    from datetime import datetime, date
    if not value:
        return ''
    dt = None
    if isinstance(value, (datetime, date)):
        dt = value
    elif isinstance(value, str):
        s = value.strip().replace('T', ' ')
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                dt = datetime.strptime(s[:len(fmt) + 2 if '%H' in fmt else len(fmt)], fmt)
                break
            except ValueError:
                continue
        if dt is None:
            try:
                dt = datetime.fromisoformat(s)
            except ValueError:
                return value  # fallback — оставляем как было
    if dt is None:
        return ''
    today_year = datetime.utcnow().year
    month_label = _RU_MONTHS[dt.month - 1]
    if dt.year == today_year:
        return f'{dt.day} {month_label}'
    return f'{dt.day} {month_label} {dt.year}'


@require_team_member
async def pipeline_page(request: web.Request) -> web.Response:
    """Server-render Kanban доски."""
    from cabinet import pipeline_service, team_service
    from datetime import datetime
    user = request['user']
    company = request['company']
    role = request['role']

    # Backfill meta для карточек у которых data.name пуст (старые без notification-данных)
    try:
        await pipeline_service.backfill_card_meta(company['id'])
    except Exception:
        pass  # не блокируем рендер на ошибке

    cards = await pipeline_service.list_company_cards(company['id'])
    members = await team_service.list_members_with_users(company['id'])

    # Карта user_id → display_name для последних изменений
    members_by_id = {m['user_id']: m for m in members}

    # Last change history per card — кто и когда последний раз трогал карточку
    last_changes = await pipeline_service.get_last_changes_map([c['id'] for c in cards])

    # Обогащаем card display-полями
    now = datetime.utcnow()
    for c in cards:
        lc = last_changes.get(c['id'])
        if lc:
            uid = lc['user_id']
            mem = members_by_id.get(uid)
            c['_last_change_by'] = (mem.get('display_name') if mem else f'User {uid}')
            c['_last_change_at'] = lc['created_at']
            # Сколько прошло (минут / часов / дней)
            delta = now - lc['created_at']
            if delta.total_seconds() < 60:
                ago = 'только что'
            elif delta.total_seconds() < 3600:
                m = int(delta.total_seconds() // 60)
                ago = f'{m} мин назад'
            elif delta.total_seconds() < 86400:
                h = int(delta.total_seconds() // 3600)
                ago = f'{h} ч назад'
            else:
                d = delta.days
                ago = f'{d} дн назад'
            c['_last_change_ago'] = ago
        else:
            c['_last_change_by'] = None
            c['_last_change_at'] = None
            c['_last_change_ago'] = None

        # assignee display name
        if c['assignee_user_id']:
            mem = members_by_id.get(c['assignee_user_id'])
            c['_assignee_name'] = mem.get('display_name') if mem else f'#{c["assignee_user_id"]}'
            c['_assignee_initial'] = (c['_assignee_name'] or '?')[0].upper()
        else:
            c['_assignee_name'] = None
            c['_assignee_initial'] = None

        # Deadline short (например "8 мая" или "12 мая 2027")
        c['_deadline_short'] = _format_deadline_short((c.get('data') or {}).get('deadline'))

    by_stage = {s: [] for s in pipeline_service.ALL_STAGES}
    for c in cards:
        if c['stage'] in by_stage:
            by_stage[c['stage']].append(c)

    # JSON-safe для встраивания в data-attribute (JS)
    members_json = [
        {
            'user_id': m['user_id'],
            'role': m['role'],
            'display_name': m.get('display_name', f'User {m["user_id"]}'),
            'username': m.get('username'),
        }
        for m in members
    ]

    return _render_template(
        'pipeline.html', request,
        active_page='pipeline',
        user_name=user.get('username') or user.get('first_name') or 'Вы',
        user_tier=user.get('subscription_tier', ''),
        nav_counts={},
        company_name=company['name'],
        is_owner=(role == 'owner'),
        current_user_id=user['user_id'],
        stages=pipeline_service.ALL_STAGES,
        stage_labels=pipeline_service.STAGE_LABELS,
        cards_by_stage=by_stage,
        members=members,
        members_json=members_json,
    )


@require_team_member
async def pipeline_archive_page(request: web.Request) -> web.Response:
    from cabinet import pipeline_service, team_service
    user = request['user']
    company = request['company']
    role = request['role']
    cards = await pipeline_service.list_archived_cards(company['id'])
    members = await team_service.list_members_with_users(company['id'])
    return _render_template(
        'pipeline_archive.html', request,
        active_page='pipeline',
        user_name=user.get('username') or user.get('first_name') or 'Вы',
        user_tier=user.get('subscription_tier', ''),
        nav_counts={},
        company_name=company['name'],
        is_owner=(role == 'owner'),
        cards=cards,
        members=members,
    )


@require_team_member
async def team_page(request: web.Request) -> web.Response:
    from cabinet import pipeline_service, team_service
    user = request['user']
    company = request['company']
    role = request['role']
    members = await team_service.list_members_with_users(company['id'])
    invites = []
    dashboard = None
    if role == 'owner':
        invites = await team_service.list_active_invites(company['id'])
        dashboard = await pipeline_service.team_dashboard(company['id'])
    return _render_template(
        'team.html', request,
        active_page='team',
        user_name=user.get('username') or user.get('first_name') or 'Вы',
        user_tier=user.get('subscription_tier', ''),
        nav_counts={},
        company_name=company['name'],
        is_owner=(role == 'owner'),
        current_user_id=user['user_id'],
        members=members,
        invites=invites,
        dashboard=dashboard,
    )


async def invite_page(request: web.Request) -> web.Response:
    """Приём инвайт-ссылки. Если не залогинен — на login с next.
    Если валидна и юзер не в команде — присоединяет, редирект на /cabinet/pipeline.
    """
    from cabinet import team_service
    token = request.match_info['token']
    user = await get_current_user(request)
    if not user:
        raise web.HTTPFound(f'/cabinet/login?next=/cabinet/invite/{token}')

    invite = await team_service.validate_invite_token(token)
    if not invite:
        return _render_template(
            'invite.html', request,
            error='Ссылка недействительна или истекла',
            user_name=user.get('username') or user.get('first_name') or 'Вы',
            user_tier=user.get('subscription_tier', ''),
            active_page='invite',
        )

    result = await team_service.accept_invite(token, user['user_id'])
    if result['ok']:
        raise web.HTTPFound('/cabinet/pipeline')
    return _render_template(
        'invite.html', request,
        error=result.get('error', 'Не удалось присоединиться'),
        user_name=user.get('username') or user.get('first_name') or 'Вы',
        user_tier=user.get('subscription_tier', ''),
        active_page='invite',
    )
