"""
SQLAlchemy adapter для bot/database.

Обертка над unified database.py для обратной совместимости.
"""

import logging
from typing import Optional, List, Dict, Any
from sqlalchemy import select, update, delete, func
from sqlalchemy.exc import IntegrityError

# Импортируем из unified database
from database import (
    User as UserModel,
    AccessRequest as AccessRequestModel,
    get_session,
    DatabaseSession
)

logger = logging.getLogger(__name__)


class BotDatabase:
    """
    Адаптер для bot database на SQLAlchemy.

    Совместим с старым интерфейсом aiosqlite.
    """

    def __init__(self):
        """Инициализация адаптера."""
        pass

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """Получение пользователя по telegram_id."""
        async with DatabaseSession() as session:
            result = await session.execute(
                select(UserModel).where(UserModel.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()

            if not user:
                return None

            return {
                'id': user.id,
                'telegram_id': user.telegram_id,
                'username': user.username,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'is_active': user.is_active,
                'created_at': user.created_at.isoformat() if user.created_at else None,
                'last_activity': user.last_activity.isoformat() if user.last_activity else None
            }

    async def create_user(
        self,
        telegram_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None
    ) -> int:
        """Создание нового пользователя."""
        async with DatabaseSession() as session:
            user = UserModel(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name
            )
            session.add(user)
            await session.flush()
            return user.id

    async def get_all_users(self) -> List[Dict[str, Any]]:
        """Получение всех пользователей."""
        async with DatabaseSession() as session:
            result = await session.execute(select(UserModel))
            users = result.scalars().all()

            return [{
                'id': user.id,
                'telegram_id': user.telegram_id,
                'username': user.username,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'is_active': user.is_active,
                'created_at': user.created_at.isoformat() if user.created_at else None
            } for user in users]

    async def deactivate_user(self, telegram_id: int):
        """Деактивация пользователя."""
        async with DatabaseSession() as session:
            await session.execute(
                update(UserModel)
                .where(UserModel.telegram_id == telegram_id)
                .values(is_active=False)
            )

    async def activate_user(self, telegram_id: int):
        """Активация пользователя."""
        async with DatabaseSession() as session:
            await session.execute(
                update(UserModel)
                .where(UserModel.telegram_id == telegram_id)
                .values(is_active=True)
            )

    # ============================================
    # ACCESS REQUESTS
    # ============================================

    async def create_access_request(
        self,
        telegram_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        reason: Optional[str] = None
    ) -> int:
        """Создание запроса на доступ."""
        async with DatabaseSession() as session:
            request = AccessRequestModel(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                reason=reason,
                status='pending'
            )
            session.add(request)
            await session.flush()
            return request.id

    async def get_pending_requests(self) -> List[Dict[str, Any]]:
        """Получение всех pending запросов."""
        async with DatabaseSession() as session:
            result = await session.execute(
                select(AccessRequestModel)
                .where(AccessRequestModel.status == 'pending')
                .order_by(AccessRequestModel.created_at.desc())
            )
            requests = result.scalars().all()

            return [{
                'id': req.id,
                'telegram_id': req.telegram_id,
                'username': req.username,
                'first_name': req.first_name,
                'last_name': req.last_name,
                'reason': req.reason,
                'status': req.status,
                'created_at': req.created_at.isoformat() if req.created_at else None
            } for req in requests]

    async def approve_access_request(self, request_id: int):
        """Одобрение запроса."""
        async with DatabaseSession() as session:
            await session.execute(
                update(AccessRequestModel)
                .where(AccessRequestModel.id == request_id)
                .values(status='approved')
            )

    async def reject_access_request(self, request_id: int):
        """Отклонение запроса."""
        async with DatabaseSession() as session:
            await session.execute(
                update(AccessRequestModel)
                .where(AccessRequestModel.id == request_id)
                .values(status='rejected')
            )


# Глобальный singleton
_bot_db_instance = None


async def get_bot_db() -> BotDatabase:
    """Получение singleton instance bot database."""
    global _bot_db_instance

    if _bot_db_instance is None:
        _bot_db_instance = BotDatabase()

    return _bot_db_instance


__all__ = ['BotDatabase', 'get_bot_db']
