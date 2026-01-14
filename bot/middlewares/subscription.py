"""
Subscription Check Middleware.

Проверяет подписку пользователя и блокирует действия при истёкшем триале.
"""

import logging
from datetime import datetime
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

from sqlalchemy import select, update
from database import SniperUser, DatabaseSession

logger = logging.getLogger(__name__)


class SubscriptionMiddleware(BaseMiddleware):
    """
    Middleware для проверки подписки пользователя.

    Проверяет:
    - Не заблокирован ли пользователь
    - Не истёк ли триал период
    - Обновляет last_activity

    Пропускает:
    - /start, /help, /menu, /subscription команды
    - Callback связанные с подпиской и меню
    """

    # Команды, которые работают без подписки
    FREE_COMMANDS = {'/start', '/help', '/menu', '/subscription', '/sniper'}

    # Callback prefixes, которые работают без подписки
    # (позволяют видеть меню и оформить подписку)
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

        # Проверяем статус пользователя
        try:
            async with DatabaseSession() as session:
                user = await session.scalar(
                    select(SniperUser).where(SniperUser.telegram_id == user_id)
                )

                if not user:
                    # Новый пользователь - пропускаем
                    return await handler(event, data)

                # Проверяем блокировку
                if user.status == 'blocked':
                    message = (
                        f"❌ <b>Ваш аккаунт заблокирован</b>\n\n"
                        f"Причина: {user.blocked_reason or 'Не указана'}\n\n"
                        f"Обратитесь в поддержку для разблокировки."
                    )

                    if isinstance(event, Message):
                        await event.answer(message, parse_mode="HTML")
                    elif isinstance(event, CallbackQuery):
                        await event.answer("Ваш аккаунт заблокирован", show_alert=True)
                    return

                # Для бесплатных действий пропускаем дальнейшие проверки
                if is_free_action:
                    # Обновляем last_activity
                    await session.execute(
                        update(SniperUser)
                        .where(SniperUser.id == user.id)
                        .values(last_activity=datetime.now())
                    )
                    return await handler(event, data)

                # Проверяем истечение подписки для ВСЕХ типов (trial, basic, premium)
                subscription_expired = False
                now = datetime.now()

                if user.subscription_tier in ['trial', 'basic', 'premium']:
                    if user.trial_expires_at:
                        if now > user.trial_expires_at:
                            subscription_expired = True
                    else:
                        # Нет даты окончания - считаем истекшей
                        subscription_expired = True
                elif user.subscription_tier == 'free':
                    # Free пользователи без подписки - блокируем
                    subscription_expired = True

                if subscription_expired:
                    tier_name = {
                        'trial': 'пробный период',
                        'basic': 'подписка Basic',
                        'premium': 'подписка Premium',
                        'free': 'подписка'
                    }.get(user.subscription_tier, 'подписка')

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

                # Обновляем last_activity
                await session.execute(
                    update(SniperUser)
                    .where(SniperUser.id == user.id)
                    .values(last_activity=datetime.now())
                )

        except Exception as e:
            logger.error(f"SubscriptionMiddleware error: {e}", exc_info=True)
            # При ошибке пропускаем (не блокируем пользователя)

        return await handler(event, data)
