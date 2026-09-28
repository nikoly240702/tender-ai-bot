"""
Авторизация веб-кабинета через Telegram Login Widget.

Проверка HMAC-SHA256 подписи, управление сессиями.
"""

import os
import hmac
import hashlib
import secrets
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from aiohttp import web

logger = logging.getLogger(__name__)


def verify_telegram_login(data: Dict[str, str], bot_token: str) -> bool:
    """
    Проверка авторизации через Telegram Login Widget (HMAC-SHA256).

    Algorithm:
    1. Extract hash from data
    2. Build data_check_string (sorted fields, excluding hash)
    3. secret_key = SHA256(bot_token)
    4. computed_hash = HMAC-SHA256(secret_key, data_check_string)
    5. Compare with received hash
    6. Check auth_date freshness
    """
    received_hash = data.get('hash', '')
    if not received_hash:
        return False

    # Build check string (all fields except hash, sorted)
    check_items = []
    for key in sorted(data.keys()):
        if key != 'hash':
            check_items.append(f"{key}={data[key]}")
    data_check_string = '\n'.join(check_items)

    # Compute HMAC
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    computed_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256
    ).hexdigest()

    # Secure comparison
    if not hmac.compare_digest(computed_hash, received_hash):
        logger.warning("Telegram login: hash mismatch")
        return False

    # Check freshness (allow 24 hours)
    auth_date = data.get('auth_date', '0')
    try:
        auth_timestamp = int(auth_date)
        now = int(datetime.utcnow().timestamp())
        if now - auth_timestamp > 86400:
            logger.warning("Telegram login: auth_date too old")
            return False
    except (ValueError, TypeError):
        return False

    return True


def generate_session_token() -> str:
    """Генерация безопасного токена сессии (64 hex chars)."""
    return secrets.token_hex(32)


# ---------------------------------------------------------------------------
# Подписка: режим «только чтение»
# ---------------------------------------------------------------------------

# Без активной подписки кабинет открыт на просмотр, но не на изменения.
# Исключения: оплата (иначе из блокировки не выйти), свои данные и
# настройки. Переключатель фильтров тоже пропускается сюда, но включать
# им фильтры нельзя — это решает filter_toggle_allowed в обработчике.
WRITE_ALLOWED_PREFIXES = (
    '/cabinet/api/subscription',
    '/cabinet/api/profile',
    '/cabinet/api/settings',
)
CABINET_PREFIX = '/cabinet/'
SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})


def subscription_is_active(expires_at, now: Optional[datetime] = None) -> bool:
    """Активна ли подписка на момент now.

    Пустая дата означает «подписки не было», а не «бессрочная»: поле
    заполняется при каждой выдаче тарифа, и его отсутствие — признак
    того, что человеку ничего не выдавали.
    """
    if not expires_at:
        return False

    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            logger.warning("Непонятная дата окончания подписки: %r", expires_at)
            return False

    if not isinstance(expires_at, datetime):
        return False

    return expires_at > (now or datetime.now())


def is_admin_telegram_id(telegram_id) -> bool:
    """Владелец сервиса проходит любые проверки подписки.

    Страховка от того, чтобы запереть самого себя из-за даты в базе.
    """
    raw = os.getenv('ADMIN_USER_ID') or os.getenv('ADMIN_TELEGRAM_ID') or ''
    try:
        return bool(raw.strip()) and int(raw) == int(telegram_id)
    except (TypeError, ValueError):
        return False


def write_allowed(method: str, path: str, *, subscription_active: bool,
                  is_admin: bool = False) -> bool:
    """Пропускать ли изменяющий запрос при текущем состоянии подписки."""
    if method.upper() in SAFE_METHODS:
        return True
    if not path.startswith(CABINET_PREFIX):
        return True
    if subscription_active or is_admin:
        return True
    if path.startswith(WRITE_ALLOWED_PREFIXES):
        return True
    # Выключение фильтров разрешено, поэтому запрос доходит до обработчика.
    return path.endswith('/toggle')


def filter_toggle_allowed(*, will_enable: bool, subscription_active: bool,
                          is_admin: bool = False) -> bool:
    """Без подписки фильтр можно только выключить.

    Иначе блокировка обходится в два клика: выключил и включил обратно.
    """
    return subscription_active or is_admin or not will_enable


@web.middleware
async def subscription_readonly_middleware(request: web.Request, handler):
    """Единственная точка, где держится правило «смотреть можно, менять нельзя».

    Middleware висит на всём приложении, поэтому первым делом отсеивает
    всё, что не относится к кабинету: вебхуки оплаты и Битрикс24
    приходят без сессии и к подписке отношения не имеют.
    """
    if (request.method.upper() in SAFE_METHODS
            or not request.path.startswith(CABINET_PREFIX)):
        return await handler(request)

    user = await get_current_user(request)
    if user is None:
        # Неавторизованного развернёт require_auth — не подменяем 401 на 402.
        return await handler(request)

    active = subscription_is_active(user.get('trial_expires_at'))
    request['subscription_active'] = active
    request['is_admin'] = is_admin_telegram_id(user.get('telegram_id'))

    if write_allowed(request.method, request.path,
                     subscription_active=active, is_admin=request['is_admin']):
        return await handler(request)

    return web.json_response({
        'error': 'subscription_expired',
        'message': 'Подписка истекла. Данные доступны для просмотра, '
                   'изменения — после продления.',
    }, status=402)


async def get_current_user(request: web.Request) -> Optional[Dict[str, Any]]:
    """
    Получение текущего пользователя из cookie сессии.

    Returns:
        Dict с user_id, telegram_id или None
    """
    session_token = request.cookies.get('cabinet_session')
    if not session_token:
        return None

    try:
        from tender_sniper.database import get_sniper_db
        db = await get_sniper_db()
        session = await db.get_web_session(session_token)
        if not session:
            return None

        # Получаем пользователя
        user = await db.get_user_by_id(session['user_id'])
        if not user:
            return None

        return {
            'user_id': session['user_id'],
            'telegram_id': user['telegram_id'],
            'subscription_tier': user.get('subscription_tier', 'trial'),
            'trial_expires_at': user.get('trial_expires_at'),
            'session_token': session_token,
        }
    except Exception as e:
        logger.error(f"Error getting current user from session: {e}")
        return None


def require_auth(handler):
    """Декоратор для проверки авторизации."""
    async def wrapper(request):
        user = await get_current_user(request)
        if not user:
            # Для API — 401, для страниц — редирект
            if '/api/' in request.path:
                return web.json_response({'error': 'Unauthorized'}, status=401)
            raise web.HTTPFound('/cabinet/login')
        request['user'] = user
        return await handler(request)
    return wrapper


def require_team_member(handler, auto_create_for_pages: bool = True):
    """Проверяет членство в команде. Кладёт company и role в request.

    auto_create_for_pages=True: для страниц (не /api/) автоматически создаём
    команду при первом заходе, чтобы новый юзер сразу попадал в свой workspace.
    """
    async def wrapper(request):
        user = await get_current_user(request)
        if not user:
            if '/api/' in request.path:
                return web.json_response({'error': 'Unauthorized'}, status=401)
            raise web.HTTPFound('/cabinet/login')

        from cabinet.team_service import (
            get_active_company, get_or_create_company_for_user,
        )
        company = await get_active_company(user['user_id'], user.get('session_token'))
        is_api = '/api/' in request.path
        if not company:
            if is_api:
                return web.json_response({'error': 'Not in any team'}, status=403)
            if auto_create_for_pages:
                company = await get_or_create_company_for_user(user['user_id'])
            else:
                raise web.HTTPFound('/cabinet/pipeline')

        request['user'] = user
        request['company'] = company
        request['role'] = (
            'owner' if company['owner_user_id'] == user['user_id'] else 'member'
        )
        return await handler(request)
    return wrapper


def require_owner(handler):
    """Owner-only — обёртка над require_team_member с проверкой роли."""
    async def check_then_handle(request):
        if request.get('role') != 'owner':
            return web.json_response({'error': 'Owner only'}, status=403)
        return await handler(request)
    return require_team_member(check_then_handle)
