"""
Кэш пользователей для уменьшения запросов к БД.

Хранит данные пользователя в памяти на 60 секунд,
избегая повторных запросов к PostgreSQL на Railway.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Кэш пользователей: {telegram_id: (user_data, timestamp)}
_user_cache: Dict[int, tuple] = {}
_CACHE_TTL = 60  # секунд


def get_cached_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получить пользователя из кэша если не устарел."""
    if telegram_id in _user_cache:
        user_data, timestamp = _user_cache[telegram_id]
        if datetime.now() - timestamp < timedelta(seconds=_CACHE_TTL):
            return user_data
        else:
            # Устарело - удаляем
            del _user_cache[telegram_id]
    return None


def set_cached_user(telegram_id: int, user_data: Dict[str, Any]):
    """Сохранить пользователя в кэш."""
    _user_cache[telegram_id] = (user_data, datetime.now())


def invalidate_user_cache(telegram_id: int):
    """Инвалидировать кэш пользователя (после изменений)."""
    if telegram_id in _user_cache:
        del _user_cache[telegram_id]


def clear_user_cache():
    """Полная очистка кэша."""
    _user_cache.clear()


def cleanup_expired_cache():
    """Удалить устаревшие записи из кэша."""
    now = datetime.now()
    expired = [
        tid for tid, (_, ts) in _user_cache.items()
        if now - ts >= timedelta(seconds=_CACHE_TTL)
    ]
    for tid in expired:
        del _user_cache[tid]
