"""
Middleware для контроля доступа к боту.

Открытый доступ: все пользователи могут использовать бота.
Админ может блокировать отдельных пользователей и управлять тарифами.
"""

from typing import Callable, Dict, Any, Awaitable, Union
from datetime import datetime
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from bot.config import BotConfig
from sqlalchemy import select
import logging

logger = logging.getLogger(__name__)


class AccessControlMiddleware(BaseMiddleware):
    """
    Middleware для контроля доступа к боту.

    Логика:
    1. Админ всегда имеет доступ
    2. Новые пользователи автоматически регистрируются (Free тариф)
    3. Заблокированные пользователи не имеют доступа
    4. Все остальные имеют доступ согласно своему тарифу
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: Union[Message, CallbackQuery],
        data: Dict[str, Any]
    ) -> Any:
        """
        Проверяет доступ пользователя к боту.

        Args:
            handler: Следующий обработчик
            event: Событие (сообщение или callback)
            data: Данные middleware

        Returns:
            Результат выполнения handler или None если доступ запрещен
        """
        user = event.from_user
        user_id = user.id

        # Администратор всегда имеет доступ
        if BotConfig.ADMIN_USER_ID and user_id == BotConfig.ADMIN_USER_ID:
            logger.debug(f"✅ Доступ разрешен для администратора {user_id}")
            return await handler(event, data)

        # Импортируем здесь чтобы избежать циклических импортов
        from database import DatabaseSession, SniperUser

        try:
            async with DatabaseSession() as session:
                # Ищем пользователя в БД
                query = select(SniperUser).where(SniperUser.telegram_id == user_id)
                result = await session.execute(query)
                db_user = result.scalar_one_or_none()

                if not db_user:
                    # Новый пользователь - автоматическая регистрация
                    logger.info(f"📝 Новый пользователь {user_id} (@{user.username}) - автоматическая регистрация")

                    db_user = SniperUser(
                        telegram_id=user_id,
                        username=user.username,
                        first_name=user.first_name,
                        last_name=user.last_name,
                        status='active',
                        subscription_tier='trial',
                        filters_limit=3,
                        notifications_limit=20
                    )
                    session.add(db_user)
                    await session.flush()  # Чтобы получить ID

                    # Уведомляем админа о новом пользователе
                    if BotConfig.ADMIN_USER_ID:
                        try:
                            bot = data.get('bot')
                            if bot:
                                full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Без имени"
                                user_info = f"@{user.username}" if user.username else "без username"

                                await bot.send_message(
                                    chat_id=BotConfig.ADMIN_USER_ID,
                                    text=(
                                        f"👤 <b>Новый пользователь</b>\n\n"
                                        f"Имя: {full_name}\n"
                                        f"Username: {user_info}\n"
                                        f"ID: <code>{user_id}</code>\n"
                                        f"Тариф: Free"
                                    ),
                                    parse_mode="HTML"
                                )
                        except Exception as e:
                            logger.warning(f"Не удалось уведомить админа о новом пользователе: {e}")

                # Проверяем статус блокировки
                if db_user.status == 'blocked':
                    logger.warning(f"🚫 Доступ запрещен для заблокированного пользователя {user_id}")

                    blocked_reason = db_user.blocked_reason or "Причина не указана"

                    error_message = (
                        f"🚫 <b>Ваш аккаунт заблокирован</b>\n\n"
                        f"Причина: {blocked_reason}\n\n"
                        f"Для разблокировки обратитесь к администратору."
                    )

                    if isinstance(event, Message):
                        await event.answer(error_message, parse_mode="HTML")
                    elif isinstance(event, CallbackQuery):
                        await event.answer("Ваш аккаунт заблокирован", show_alert=True)

                    return None

                # Обновляем информацию о пользователе (username может измениться)
                db_user.username = user.username
                db_user.first_name = user.first_name
                db_user.last_name = user.last_name
                db_user.last_activity = datetime.utcnow()

                # Добавляем информацию о пользователе в data для использования в handlers
                data['db_user'] = db_user
                data['subscription_tier'] = db_user.subscription_tier
                data['user_id_db'] = db_user.id  # ID в базе данных

        except Exception as e:
            logger.error(f"❌ Ошибка работы с БД в middleware: {e}")
            # При ошибке БД разрешаем доступ (fail-open)
            pass

        logger.debug(f"✅ Доступ разрешен для пользователя {user_id}")
        return await handler(event, data)
