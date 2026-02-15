"""
Модуль обработчиков команд и сообщений бота.

ПРИМЕЧАНИЕ: search и history перенесены в _archive/ (рефакторинг 2024-12-19)
"""

from . import (
    start,
    admin,
    sniper,
    sniper_search,
    admin_sniper,
    onboarding,
    inline_search,
    all_tenders,
    tender_actions,
    user_management,
    menu_priority,
    referral,
    subscriptions,
    sniper_wizard_new,
    group_chat
)

__all__ = [
    'start',
    'admin',
    'sniper',
    'sniper_search',
    'admin_sniper',
    'onboarding',
    'inline_search',
    'all_tenders',
    'tender_actions',
    'user_management',
    'menu_priority',
    'referral',
    'subscriptions',
    'sniper_wizard_new',
    'group_chat'
]
