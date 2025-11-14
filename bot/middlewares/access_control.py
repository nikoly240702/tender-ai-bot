"""
Middleware для контроля доступа к боту.
"""

from typing import Callable, Dict, Any, Awaitable, Union
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
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
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: Union[Message, CallbackQuery],
        data: Dict[str, Any]
    ) -> Any:
        """
        Проверяет, есть ли у пользователя доступ к боту.

        Args:
            handler: Следующий обработчик
            event: Событие (сообщение или callback)
            data: Данные middleware

        Returns:
            Результат выполнения handler или None если доступ запрещен
        """
        user_id = event.from_user.id

        # Администратор всегда имеет доступ
        if BotConfig.ADMIN_USER_ID and user_id == BotConfig.ADMIN_USER_ID:
            logger.info(f"✅ Доступ разрешен для администратора {user_id}")
            return await handler(event, data)

        # Если белый список не настроен (None) - доступ всем
        # Если это пустой set() - закрытый доступ
        if BotConfig.ALLOWED_USERS is None:
            logger.info(f"✅ Открытый режим: доступ разрешен для {user_id}")
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

            # Отправляем запрос админу только для команды /start
            if isinstance(event, Message) and event.text == '/start' and BotConfig.ADMIN_USER_ID:
                try:
                    from aiogram.types import InlineKeyboardButton
                    from aiogram.utils.keyboard import InlineKeyboardBuilder

                    user_info = f"@{event.from_user.username}" if event.from_user.username else "без username"
                    full_name = f"{event.from_user.first_name or ''} {event.from_user.last_name or ''}".strip()

                    builder = InlineKeyboardBuilder()
                    builder.row(
                        InlineKeyboardButton(
                            text="✅ Одобрить",
                            callback_data=f"access_approve_{user_id}"
                        ),
                        InlineKeyboardButton(
                            text="❌ Отклонить",
                            callback_data=f"access_reject_{user_id}"
                        )
                    )

                    # Получаем бота из data
                    bot = data.get('bot')
                    if bot:
                        await bot.send_message(
                            chat_id=BotConfig.ADMIN_USER_ID,
                            text=(
                                f"🔔 <b>Новый запрос на доступ</b>\n\n"
                                f"👤 Пользователь: {full_name}\n"
                                f"🆔 Username: {user_info}\n"
                                f"🔢 User ID: <code>{user_id}</code>\n\n"
                                f"Одобрить доступ?"
                            ),
                            reply_markup=builder.as_markup(),
                            parse_mode="HTML"
                        )
                        logger.info(f"📨 Запрос на доступ отправлен админу для пользователя {user_id}")

                        error_message = (
                            "⏳ <b>Запрос на доступ отправлен</b>\n\n"
                            "Ваш запрос отправлен администратору.\n"
                            "Вы получите уведомление, когда доступ будет одобрен.\n\n"
                            f"Ваш User ID: <code>{user_id}</code>"
                        )
                    else:
                        error_message = (
                            "❌ <b>Доступ запрещен</b>\n\n"
                            f"Ваш User ID: <code>{user_id}</code>\n\n"
                            "Обратитесь к администратору для получения доступа."
                        )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления админу: {e}")
                    error_message = (
                        "❌ <b>Доступ запрещен</b>\n\n"
                        f"Ваш User ID: <code>{user_id}</code>\n\n"
                        "Обратитесь к администратору для получения доступа."
                    )
            else:
                error_message = (
                    "❌ <b>Доступ запрещен</b>\n\n"
                    f"Ваш User ID: <code>{user_id}</code>\n\n"
                    "Используйте /start для запроса доступа."
                )

            # Для сообщений используем answer, для callback - answer + message
            if isinstance(event, Message):
                await event.answer(error_message, parse_mode="HTML")
            elif isinstance(event, CallbackQuery):
                await event.answer("Доступ запрещен", show_alert=True)
                await event.message.answer(error_message, parse_mode="HTML")

            return
