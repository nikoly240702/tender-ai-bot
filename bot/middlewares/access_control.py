"""
Middleware для контроля доступа к боту.
"""

from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message
from bot.config import BotConfig
from bot.database.access_manager import AccessManager
import logging

logger = logging.getLogger(__name__)

# Инициализируем менеджер доступа
access_manager = AccessManager()


class AccessControlMiddleware(BaseMiddleware):
    """Проверяет доступ пользователей к боту."""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        """
        Проверяет, есть ли у пользователя доступ к боту.

        Args:
            handler: Следующий обработчик
            event: Событие (сообщение)
            data: Данные middleware

        Returns:
            Результат выполнения handler или None если доступ запрещен
        """
        user_id = event.from_user.id

        # Администратор всегда имеет доступ
        if BotConfig.ADMIN_USER_ID and user_id == BotConfig.ADMIN_USER_ID:
            logger.info(f"Доступ разрешен для администратора {user_id}")
            return await handler(event, data)

        # Если белый список не настроен - доступ всем
        if BotConfig.ALLOWED_USERS is None:
            return await handler(event, data)

        # Проверяем доступ через базу данных
        if access_manager.is_user_allowed(user_id):
            # Доступ разрешен
            logger.info(f"Доступ разрешен для пользователя {user_id} (@{event.from_user.username})")

            # Обновляем информацию о пользователе в БД
            access_manager.update_user_info(
                user_id=user_id,
                username=event.from_user.username,
                first_name=event.from_user.first_name,
                last_name=event.from_user.last_name
            )

            return await handler(event, data)
        else:
            # Доступ запрещен
            logger.warning(f"Доступ запрещен для пользователя {user_id} (@{event.from_user.username})")
            await event.answer(
                "Извините, у вас нет доступа к этому боту.\n\n"
                f"Ваш User ID: `{user_id}`\n\n"
                "Обратитесь к администратору для получения доступа.",
                parse_mode="Markdown"
            )
            return
