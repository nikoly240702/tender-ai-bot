"""
Middleware для контроля доступа к боту.
"""

from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message
from bot.config import BotConfig
import logging

logger = logging.getLogger(__name__)


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
        # Если белый список не настроен - доступ всем
        if BotConfig.ALLOWED_USERS is None:
            return await handler(event, data)

        # Проверяем, есть ли пользователь в белом списке
        user_id = event.from_user.id

        if user_id in BotConfig.ALLOWED_USERS:
            # Доступ разрешен
            logger.info(f"Доступ разрешен для пользователя {user_id} (@{event.from_user.username})")
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
