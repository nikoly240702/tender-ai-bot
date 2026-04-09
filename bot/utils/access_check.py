"""
Access Control Utilities.

Проверка доступа к платным функциям по тарифу пользователя.
"""

from typing import Optional, Tuple
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from tender_sniper.database.sqlalchemy_adapter import get_sniper_db


# Определяем какие функции доступны на каких тарифах
FEATURE_ACCESS = {
    # Функция: список тарифов с доступом
    'archive_search': ['pro', 'premium'],
    'excel_export': ['starter', 'pro', 'premium'],
    'reminders': ['starter', 'pro', 'premium'],
    'extended_settings': ['pro', 'premium'],
    'beta_features': ['premium'],
}

# Лимиты по тарифам (фильтры и уведомления/день)
TIER_LIMITS = {
    'trial': {'filters': 3, 'notifications': 50},
    'starter': {'filters': 5, 'notifications': 50},
    'pro': {'filters': 15, 'notifications': 9999},
    'premium': {'filters': 30, 'notifications': 9999},
}

# Названия функций для сообщений
FEATURE_NAMES = {
    'archive_search': 'Архивный поиск',
    'excel_export': 'Экспорт в Excel',
    'reminders': 'Напоминания о тендерах',
    'extended_settings': 'Расширенные настройки фильтров',
    'beta_features': 'Бета-функции',
}

# Минимальный тариф для функции
FEATURE_MIN_TIER = {
    'archive_search': 'Pro',
    'excel_export': 'Starter',
    'reminders': 'Starter',
    'extended_settings': 'Pro',
    'beta_features': 'Business',
}


def get_upgrade_keyboard(feature: str) -> InlineKeyboardMarkup:
    """Клавиатура для предложения апгрейда."""
    min_tier = FEATURE_MIN_TIER.get(feature, 'Starter')

    buttons = []
    if min_tier == 'Starter':
        buttons.append([InlineKeyboardButton(
            text="⭐ Оформить Starter — 499 ₽/мес",
            callback_data="subscription_select_starter"
        )])
    buttons.append([InlineKeyboardButton(
        text="💎 Оформить Pro — 1 490 ₽/мес",
        callback_data="subscription_select_pro"
    )])
    buttons.append([InlineKeyboardButton(
        text="📦 Все тарифы",
        callback_data="subscription_tiers"
    )])
    buttons.append([InlineKeyboardButton(
        text="« Назад",
        callback_data="sniper_menu"
    )])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def get_user_tier(telegram_id: int) -> str:
    """Получить текущий тариф пользователя."""
    db = await get_sniper_db()
    user = await db.get_user_by_telegram_id(telegram_id)
    if user:
        return user.get('subscription_tier', 'trial')
    return 'trial'


async def check_feature_access(telegram_id: int, feature: str) -> Tuple[bool, str]:
    """
    Проверить доступ пользователя к функции.

    Returns:
        Tuple[bool, str]: (имеет_доступ, текущий_тариф)
    """
    tier = await get_user_tier(telegram_id)
    allowed_tiers = FEATURE_ACCESS.get(feature, [])

    has_access = tier in allowed_tiers
    return has_access, tier


async def require_feature(
    event: CallbackQuery | Message,
    feature: str,
    show_upgrade: bool = True
) -> bool:
    """
    Проверить доступ к функции и показать сообщение об апгрейде если нет доступа.

    Args:
        event: CallbackQuery или Message
        feature: Название функции из FEATURE_ACCESS
        show_upgrade: Показывать ли предложение апгрейда

    Returns:
        bool: True если доступ есть, False если нет
    """
    telegram_id = event.from_user.id
    has_access, current_tier = await check_feature_access(telegram_id, feature)

    if has_access:
        return True

    if not show_upgrade:
        return False

    feature_name = FEATURE_NAMES.get(feature, feature)
    min_tier = FEATURE_MIN_TIER.get(feature, 'Basic')

    # Формируем сообщение
    tier_emoji = '⭐' if min_tier == 'Basic' else '💎'
    message_text = (
        f"🔒 <b>Функция недоступна</b>\n\n"
        f"<b>{feature_name}</b> доступен только на тарифе "
        f"{tier_emoji} <b>{min_tier}</b> и выше.\n\n"
        f"Ваш текущий тариф: <b>{current_tier.title()}</b>\n\n"
        f"Оформите подписку, чтобы получить доступ к этой и другим функциям!"
    )

    keyboard = get_upgrade_keyboard(feature)

    # Отправляем сообщение
    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message:
            await event.message.edit_text(
                message_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
    else:
        await event.answer(
            message_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    return False


# Декоратор для простой проверки (опционально)
def requires_tier(*allowed_tiers):
    """
    Декоратор для проверки тарифа.

    Использование:
        @requires_tier('starter', 'pro', 'premium')
        async def handler(callback: CallbackQuery):
            ...
    """
    def decorator(func):
        async def wrapper(event, *args, **kwargs):
            telegram_id = event.from_user.id
            tier = await get_user_tier(telegram_id)

            if tier not in allowed_tiers:
                # Показываем сообщение о необходимости апгрейда
                await event.answer(
                    "🔒 Эта функция доступна только на платных тарифах",
                    show_alert=True
                )
                return

            return await func(event, *args, **kwargs)
        return wrapper
    return decorator
