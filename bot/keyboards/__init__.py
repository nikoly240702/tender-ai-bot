"""
Клавиатуры для Telegram бота.

ВНИМАНИЕ: Основные клавиатуры теперь создаются inline в соответствующих handlers.
Здесь остались только базовые и админские клавиатуры.

Удалённые функции (рефакторинг 2024-12-19):
- get_tender_type_keyboard - перенесено в sniper_search.py
- get_price_range_keyboard - перенесено в sniper_search.py
- get_tender_count_keyboard - перенесено в sniper_search.py
- get_region_keyboard - перенесено в sniper_search.py
- get_federal_districts_keyboard - перенесено в sniper_search.py
- get_region_type_keyboard - перенесено в sniper_search.py
- get_cancel_keyboard - не используется
- get_inline_cancel_keyboard - не используется
- get_tender_actions_keyboard - не используется
- get_results_navigation_keyboard - не используется
- get_tender_item_keyboard - не используется
- get_tenders_list_keyboard - не используется
- get_confirmation_keyboard - не используется
- FEDERAL_DISTRICTS - дубликат tender_sniper/regions.py
"""

from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Главное меню бота - Tender Sniper."""
    builder = ReplyKeyboardBuilder()

    builder.row(
        KeyboardButton(text="🎯 Tender Sniper")
    )

    return builder.as_markup(resize_keyboard=True)


def get_admin_panel_keyboard() -> InlineKeyboardMarkup:
    """Главное меню админ-панели."""
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text="👥 Список пользователей", callback_data="admin_list_users")
    )
    builder.row(
        InlineKeyboardButton(text="➕ Добавить пользователя", callback_data="admin_add_user")
    )
    builder.row(
        InlineKeyboardButton(text="➖ Удалить пользователя", callback_data="admin_remove_user")
    )

    return builder.as_markup()


def get_user_management_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура управления конкретным пользователем.

    Args:
        user_id: Telegram User ID пользователя
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text="❌ Удалить доступ", callback_data=f"admin_remove_{user_id}")
    )
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")
    )

    return builder.as_markup()


# Экспорт всех функций
__all__ = [
    'get_main_menu_keyboard',
    'get_admin_panel_keyboard',
    'get_user_management_keyboard',
]
