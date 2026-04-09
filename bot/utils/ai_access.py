"""AI access gate. Single source of truth for who can use AI features.

Replaces scattered checks like `subscription_tier == 'premium'` across the
codebase. Use this function in any code path that gates AI analysis,
AI document extraction, or AI relevance checks.

Usage:
    from bot.utils.ai_access import can_use_ai

    allowed, reason = can_use_ai(user)
    if not allowed:
        await message.answer(reason)
        return
"""
from datetime import datetime
from typing import Tuple, Optional


# Monthly AI analysis limits per tier (uses sniper_users.ai_analyses_used_month)
AI_LIMITS = {
    'pro': 500,        # Pro: 500 AI analyses per month (~16/day average)
    'premium': 999999, # Premium (UI: "Business"): effectively unlimited
}


def can_use_ai(user) -> Tuple[bool, Optional[str]]:
    """
    Check if a user is allowed to use AI features.

    Args:
        user: object with the following attributes (any object — DB model,
              SimpleNamespace, dict-like — works as long as getattr works):
              - subscription_tier (str)
              - has_ai_unlimited (bool, optional, default False)
              - ai_unlimited_expires_at (datetime or None, optional)
              - ai_analyses_used_month (int, optional, default 0)

    Returns:
        Tuple of (allowed, reason_if_denied):
        - (True, None) if access is granted
        - (False, str) if denied — str explains why
    """
    # AI Unlimited addon overrides tier (if active and not expired)
    if getattr(user, 'has_ai_unlimited', False):
        expires = getattr(user, 'ai_unlimited_expires_at', None)
        if expires and expires > datetime.utcnow():
            return True, None
        # Addon flag set but no/expired date — fall through to tier check

    tier = getattr(user, 'subscription_tier', 'trial')

    if tier not in AI_LIMITS:
        return False, "AI-анализ доступен на тарифах Pro и Business. Можно также докупить AI-аддон."

    used = getattr(user, 'ai_analyses_used_month', 0) or 0
    limit = AI_LIMITS[tier]

    if used >= limit:
        return False, f"Месячный лимит AI-анализов исчерпан ({used}/{limit}). Лимит сбросится в начале следующего месяца."

    return True, None
