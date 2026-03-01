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
