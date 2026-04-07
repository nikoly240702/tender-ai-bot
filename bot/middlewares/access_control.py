"""
Middleware для контроля доступа к боту.

Открытый доступ: все пользователи могут использовать бота.
Админ может блокировать отдельных пользователей и управлять тарифами.

ОПТИМИЗАЦИЯ: Кэширование пользователей для избежания запросов к БД на каждый клик.
"""

from typing import Callable, Dict, Any, Awaitable, Union
from datetime import datetime, timedelta
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from bot.config import BotConfig
from bot.middlewares.user_cache import get_cached_user, set_cached_user
from sqlalchemy import select
import asyncio
import logging

logger = logging.getLogger(__name__)

# Rate-limit обновления last_activity: не чаще раза в 30 минут на пользователя
# {telegram_id: datetime_of_last_db_update}
_last_activity_updated: Dict[int, datetime] = {}
_ACTIVITY_UPDATE_INTERVAL = timedelta(minutes=30)


async def _update_last_activity_bg(telegram_id: int):
    """Fire-and-forget обновление last_activity в БД."""
    try:
        from database import DatabaseSession, SniperUser
        from sqlalchemy import update
        async with DatabaseSession() as session:
            await session.execute(
                update(SniperUser)
                .where(SniperUser.telegram_id == telegram_id)
                .values(last_activity=datetime.utcnow())
            )
    except Exception as e:
        logger.debug(f"_update_last_activity_bg: {e}")


class AccessControlMiddleware(BaseMiddleware):
    """
    Middleware для контроля доступа к боту.

    Логика:
    1. Админ всегда имеет доступ (без БД)
    2. Проверяем кэш - если есть, не идём в БД
    3. Новые пользователи автоматически регистрируются
    4. Заблокированные пользователи не имеют доступа
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: Union[Message, CallbackQuery],
        data: Dict[str, Any]
    ) -> Any:
        """Проверяет доступ пользователя к боту."""
        user = event.from_user
        user_id = user.id

        # Определяем контекст группы
        chat = getattr(event, 'chat', None)
        if chat is None and hasattr(event, 'message'):
            chat = getattr(event.message, 'chat', None)

        is_group_context = chat is not None and chat.type in ('group', 'supergroup')
        data['is_group_context'] = is_group_context
        data['group_chat_id'] = chat.id if is_group_context else None

        # В группе lookup по chat.id (группа), в личном — по user.id
        if is_group_context:
            lookup_telegram_id = chat.id
        else:
            lookup_telegram_id = user_id

        # 1. Администратор всегда имеет доступ (БЕЗ запроса к БД!)
        if BotConfig.ADMIN_USER_ID and user_id == BotConfig.ADMIN_USER_ID:
            data['subscription_tier'] = 'premium'
            data['is_admin'] = True
            return await handler(event, data)

        # 2. Проверяем кэш (избегаем запроса к БД)
        cached = get_cached_user(lookup_telegram_id)
        if cached:
            # Пользователь в кэше - проверяем блокировку
            if cached.get('status') == 'blocked':
                return await self._handle_blocked(event, cached.get('blocked_reason'))

            # Добавляем данные из кэша
            data['subscription_tier'] = cached.get('subscription_tier', 'trial')
            data['user_id_db'] = cached.get('id')
            data['cached_user'] = cached  # Для SubscriptionMiddleware

            # Обновляем last_activity раз в 30 минут (fire-and-forget)
            now = datetime.utcnow()
            last_upd = _last_activity_updated.get(lookup_telegram_id)
            if not last_upd or now - last_upd > _ACTIVITY_UPDATE_INTERVAL:
                _last_activity_updated[lookup_telegram_id] = now
                asyncio.create_task(_update_last_activity_bg(lookup_telegram_id))

            return await handler(event, data)

        # 3. Нет в кэше - идём в БД
        from database import DatabaseSession, SniperUser

        try:
            async with DatabaseSession() as session:
                query = select(SniperUser).where(SniperUser.telegram_id == lookup_telegram_id)
                result = await session.execute(query)
                db_user = result.scalar_one_or_none()

                if not db_user:
                    if is_group_context:
                        # Группа без записи — пропускаем автосоздание (создаётся в group_chat.py)
                        data['subscription_tier'] = 'trial'
                        return await handler(event, data)

                    # Новый пользователь - автоматическая регистрация
                    logger.info(f"📝 Новый пользователь {user_id} (@{user.username})")

                    now = datetime.utcnow()
                    trial_expires = now + timedelta(days=7)
                    db_user = SniperUser(
                        telegram_id=user_id,
                        username=user.username,
                        first_name=user.first_name,
                        last_name=user.last_name,
                        status='active',
                        subscription_tier='trial',
                        filters_limit=3,
                        notifications_limit=50,
                        trial_started_at=now,
                        trial_expires_at=trial_expires,
                    )
                    session.add(db_user)
                    await session.flush()
                    logger.info(f"New user {user_id} trial expires {trial_expires}")

                    # Уведомляем админа (асинхронно, не блокируя)
                    await self._notify_admin_new_user(user, data)

                # Проверяем статус блокировки
                if db_user.status == 'blocked':
                    return await self._handle_blocked(event, db_user.blocked_reason)

                # Обновляем username если изменился (только для личных чатов)
                if not is_group_context and db_user.username != user.username:
                    db_user.username = user.username
                    db_user.first_name = user.first_name
                    db_user.last_name = user.last_name

                # Обновляем last_activity — важно для реактивационных напоминаний
                db_user.last_activity = datetime.utcnow()

                # Кэшируем пользователя
                cached_data = {
                    'id': db_user.id,
                    'telegram_id': db_user.telegram_id,
                    'status': db_user.status,
                    'subscription_tier': db_user.subscription_tier,
                    'trial_expires_at': db_user.trial_expires_at,
                    'filters_limit': db_user.filters_limit,
                    'notifications_limit': db_user.notifications_limit,
                    'notifications_enabled': db_user.notifications_enabled,
                }
                set_cached_user(lookup_telegram_id, cached_data)

                # Добавляем в data
                data['subscription_tier'] = db_user.subscription_tier
                data['user_id_db'] = db_user.id
                data['cached_user'] = cached_data

        except Exception as e:
            logger.error(f"❌ Ошибка БД в AccessControlMiddleware: {e}")
            # Fail-open: разрешаем доступ при ошибке БД

        return await handler(event, data)

    async def _handle_blocked(self, event, reason: str = None):
        """Обработка заблокированного пользователя."""
        blocked_reason = reason or "Причина не указана"
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

    async def _notify_admin_new_user(self, user, data):
        """Уведомление админа о новом пользователе."""
        if not BotConfig.ADMIN_USER_ID:
            return

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
                        f"ID: <code>{user.id}</code>\n"
                        f"Тариф: Trial"
                    ),
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.warning(f"Не удалось уведомить админа: {e}")
