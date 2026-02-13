"""
Subscription Check Middleware.

Проверяет подписку пользователя и блокирует действия при истёкшем триале.

ОПТИМИЗАЦИЯ: Использует кэш из AccessControlMiddleware, не делает запросов к БД.
"""

import os
import logging
from datetime import datetime
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

logger = logging.getLogger(__name__)

# Админы с вечным Premium (не проверяем подписку)
ADMIN_USER_IDS = {
    298437198,  # @nikolai_chizhik - владелец
}

# Дополнительные админы из ENV
_extra_admins = os.getenv('ADMIN_USER_ID', '')
if _extra_admins:
    for admin_id in _extra_admins.split(','):
        try:
            ADMIN_USER_IDS.add(int(admin_id.strip()))
        except ValueError:
            pass


class SubscriptionMiddleware(BaseMiddleware):
    """
    Middleware для проверки подписки пользователя.

    ОПТИМИЗАЦИЯ: Использует cached_user из AccessControlMiddleware,
    НЕ делает запросов к БД!

    Проверяет:
    - Не истёк ли триал период (из кэша)

    Пропускает:
    - /start, /help, /menu, /subscription команды
    - Callback связанные с подпиской и меню
    """

    # Команды, которые работают без подписки
    FREE_COMMANDS = {'/start', '/help', '/menu', '/subscription', '/sniper'}

    # Callback prefixes, которые работают без подписки
    FREE_CALLBACK_PREFIXES = [
        'sniper_menu',           # Главное меню Sniper
        'sniper_subscription',   # Страница подписки
        'sniper_plans',          # Тарифы
        'sniper_help',           # Помощь
        'subscription_',         # Все действия с подпиской
        'main_menu',             # Главное меню бота
        'start_onboarding',      # Онбординг
        'get_referral_link',     # Реферальная ссылка
    ]

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        """Проверка подписки перед обработкой."""

        # Если админ - пропускаем все проверки
        if data.get('is_admin'):
            return await handler(event, data)

        # Определяем user_id и проверяем бесплатные действия
        user_id = None
        is_free_action = False

        if isinstance(event, Message):
            user_id = event.from_user.id
            text = event.text or ''

            # Проверяем команды
            for cmd in self.FREE_COMMANDS:
                if text.startswith(cmd):
                    is_free_action = True
                    break

        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
            callback_data = event.data or ''

            # Проверяем callback prefixes
            for prefix in self.FREE_CALLBACK_PREFIXES:
                if callback_data.startswith(prefix):
                    is_free_action = True
                    break

        if not user_id:
            return await handler(event, data)

        # Админы с вечным Premium - пропускаем
        if user_id in ADMIN_USER_IDS:
            return await handler(event, data)

        # Для бесплатных действий пропускаем проверки
        if is_free_action:
            return await handler(event, data)

        # Используем КЭШИРОВАННЫЕ данные из AccessControlMiddleware
        # НЕ делаем запрос к БД!
        cached_user = data.get('cached_user')

        if cached_user:
            tier = cached_user.get('subscription_tier', 'trial')

            # Уже даунгрейднутый — блокируем
            if tier == 'expired':
                expired_message = (
                    "⚠️ <b>Ваш пробный период закончился</b>\n\n"
                    "Для продолжения работы оформите подписку.\n\n"
                    "Нажмите /subscription для выбора тарифа."
                )
                if isinstance(event, Message):
                    await event.answer(expired_message, parse_mode="HTML")
                elif isinstance(event, CallbackQuery):
                    await event.message.answer(expired_message, parse_mode="HTML")
                    await event.answer()
                return

            # Проверяем истечение подписки
            trial_expires_at = cached_user.get('trial_expires_at')

            if trial_expires_at:
                # trial_expires_at может быть datetime или строкой
                if isinstance(trial_expires_at, str):
                    try:
                        trial_expires_at = datetime.fromisoformat(trial_expires_at)
                    except:
                        trial_expires_at = None

                if trial_expires_at and datetime.now() > trial_expires_at:
                    tier_name = {
                        'trial': 'пробный период',
                        'basic': 'подписка Basic',
                        'premium': 'подписка Premium',
                    }.get(tier, 'подписка')

                    # === LAZY DOWNGRADE: обновляем tier в БД ===
                    if tier == 'trial':
                        try:
                            from database import DatabaseSession, SniperUser
                            from sqlalchemy import update as sa_update
                            async with DatabaseSession() as session:
                                await session.execute(
                                    sa_update(SniperUser)
                                    .where(SniperUser.telegram_id == user_id)
                                    .values(subscription_tier='expired')
                                )
                            logger.info(f"Trial expired for user {user_id} — downgraded to 'expired'")
                            # Обновляем кэш чтобы не делать повторный запрос
                            cached_user['subscription_tier'] = 'expired'
                        except Exception as e:
                            logger.error(f"Failed to downgrade user {user_id}: {e}")

                    message = (
                        f"⚠️ <b>Ваш {tier_name} закончился</b>\n\n"
                        "Для продолжения работы оформите или продлите подписку.\n\n"
                        "Нажмите /subscription для выбора тарифа."
                    )

                    if isinstance(event, Message):
                        await event.answer(message, parse_mode="HTML")
                    elif isinstance(event, CallbackQuery):
                        await event.message.answer(message, parse_mode="HTML")
                        await event.answer()
                    return

            elif tier == 'trial':
                # Нет даты окончания для триала - считаем истекшей
                # Lazy downgrade
                try:
                    from database import DatabaseSession, SniperUser
                    from sqlalchemy import update as sa_update
                    async with DatabaseSession() as session:
                        await session.execute(
                            sa_update(SniperUser)
                            .where(SniperUser.telegram_id == user_id)
                            .values(subscription_tier='expired')
                        )
                    logger.info(f"Trial without expiry for user {user_id} — downgraded to 'expired'")
                    cached_user['subscription_tier'] = 'expired'
                except Exception as e:
                    logger.error(f"Failed to downgrade user {user_id}: {e}")

                message = (
                    "⚠️ <b>Ваш пробный период закончился</b>\n\n"
                    "Для продолжения работы оформите подписку.\n\n"
                    "Нажмите /subscription для выбора тарифа."
                )

                if isinstance(event, Message):
                    await event.answer(message, parse_mode="HTML")
                elif isinstance(event, CallbackQuery):
                    await event.message.answer(message, parse_mode="HTML")
                    await event.answer()
                return

        return await handler(event, data)
