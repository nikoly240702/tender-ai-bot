"""Tier priority logic for upgrade-only multi-account linking.

When a user pays, the webhook looks up all sniper_users with the same email
and applies the upgrade to each. To prevent accidental downgrades (e.g., user
has Pro on Telegram, then pays Starter on Max), we use tier priority to ensure
we ONLY upgrade — never downgrade.
"""
from datetime import datetime
from typing import Optional


# Tier priority ordering: higher number = stronger tier
TIER_PRIORITY = {
    'expired': 0,
    'trial': 1,
    'starter': 2,
    'basic': 2,    # legacy alias, treated as starter-level
    'pro': 3,
    'premium': 4,  # displayed as "Business" in UI
}


def tier_priority(tier: str) -> int:
    """Return numeric priority of a tier. Unknown tiers get 0 (lowest)."""
    return TIER_PRIORITY.get(tier, 0)


def should_upgrade(
    current_tier: str,
    current_expires: Optional[datetime],
    new_tier: str,
    new_expires: Optional[datetime]
) -> bool:
    """
    Decide whether to apply (new_tier, new_expires) to a user currently on
    (current_tier, current_expires).

    Rules:
    - If new tier priority > current: upgrade
    - If lower priority: never downgrade (return False)
    - If equal priority: upgrade only if new expiry is later
    """
    cur_p = tier_priority(current_tier)
    new_p = tier_priority(new_tier)

    if new_p > cur_p:
        return True
    if new_p < cur_p:
        return False

    # Same priority — compare expirations
    if new_expires is None:
        return False
    if current_expires is None:
        return True
    return new_expires > current_expires
