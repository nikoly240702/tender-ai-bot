"""Tests for AI access gate."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from bot.utils.ai_access import can_use_ai, AI_LIMITS


def make_user(**kwargs):
    defaults = {
        'subscription_tier': 'trial',
        'has_ai_unlimited': False,
        'ai_unlimited_expires_at': None,
        'ai_analyses_used_month': 0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_trial_no_ai():
    user = make_user(subscription_tier='trial')
    allowed, reason = can_use_ai(user)
    assert allowed is False
    assert reason is not None


def test_starter_no_ai():
    user = make_user(subscription_tier='starter')
    allowed, reason = can_use_ai(user)
    assert allowed is False


def test_expired_no_ai():
    user = make_user(subscription_tier='expired')
    allowed, _ = can_use_ai(user)
    assert allowed is False


def test_pro_within_limit():
    user = make_user(subscription_tier='pro', ai_analyses_used_month=100)
    allowed, _ = can_use_ai(user)
    assert allowed is True


def test_pro_at_limit_blocked():
    user = make_user(subscription_tier='pro', ai_analyses_used_month=AI_LIMITS['pro'])
    allowed, reason = can_use_ai(user)
    assert allowed is False
    assert reason is not None


def test_pro_over_limit_blocked():
    user = make_user(subscription_tier='pro', ai_analyses_used_month=AI_LIMITS['pro'] + 50)
    allowed, _ = can_use_ai(user)
    assert allowed is False


def test_premium_high_usage_still_allowed():
    user = make_user(subscription_tier='premium', ai_analyses_used_month=99999)
    allowed, _ = can_use_ai(user)
    assert allowed is True


def test_addon_overrides_tier():
    """AI unlimited addon — allow even on trial when active."""
    user = make_user(
        subscription_tier='trial',
        has_ai_unlimited=True,
        ai_unlimited_expires_at=datetime.utcnow() + timedelta(days=30),
    )
    allowed, _ = can_use_ai(user)
    assert allowed is True


def test_addon_expired_falls_back_to_tier():
    """Expired addon → check normal tier rules."""
    user = make_user(
        subscription_tier='trial',
        has_ai_unlimited=True,
        ai_unlimited_expires_at=datetime.utcnow() - timedelta(days=1),
    )
    allowed, _ = can_use_ai(user)
    assert allowed is False


def test_addon_no_expiry_date_falls_back():
    """has_ai_unlimited=True but ai_unlimited_expires_at=None → no addon access."""
    user = make_user(
        subscription_tier='trial',
        has_ai_unlimited=True,
        ai_unlimited_expires_at=None,
    )
    allowed, _ = can_use_ai(user)
    assert allowed is False


def test_user_missing_attributes_safe():
    """Object missing some optional fields should not crash."""
    user = SimpleNamespace(subscription_tier='pro')
    # missing ai_analyses_used_month, has_ai_unlimited, etc
    allowed, _ = can_use_ai(user)
    assert allowed is True  # 0 used, under limit
