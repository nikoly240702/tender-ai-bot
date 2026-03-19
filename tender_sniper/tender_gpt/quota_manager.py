"""
Quota manager for Tender-GPT.

Checks and tracks per-user monthly message limits by subscription tier.
"""

import logging
from datetime import datetime
from typing import Dict, Any, Optional

from sqlalchemy import select, update
from database import SniperUser as SniperUserModel, DatabaseSession

logger = logging.getLogger(__name__)

# Monthly message limits by tier
TIER_LIMITS = {
    'trial': 10,
    'basic': 50,
    'premium': 200,
}


class QuotaManager:
    """Manages Tender-GPT message quotas."""

    def get_limit(self, tier: str) -> int:
        """Get message limit for tier."""
        return TIER_LIMITS.get(tier, TIER_LIMITS['trial'])

    async def check_quota(self, telegram_id: int) -> Dict[str, Any]:
        """
        Check if user has remaining GPT messages.

        Returns:
            {
                'allowed': bool,
                'used': int,
                'limit': int,
                'remaining': int,
                'tier': str,
            }
        """
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperUserModel).where(
                    SniperUserModel.telegram_id == telegram_id
                )
            )
            user = result.scalar_one_or_none()

            if not user:
                return {
                    'allowed': False,
                    'used': 0,
                    'limit': 0,
                    'remaining': 0,
                    'tier': 'unknown',
                }

            tier = user.subscription_tier or 'trial'
            limit = self.get_limit(tier)

            # Check if AI unlimited addon is active
            if user.has_ai_unlimited:
                if user.ai_unlimited_expires_at and user.ai_unlimited_expires_at > datetime.utcnow():
                    return {
                        'allowed': True,
                        'used': user.gpt_messages_used_month or 0,
                        'limit': 999999,
                        'remaining': 999999,
                        'tier': tier,
                    }

            # Reset counter if new month
            used = user.gpt_messages_used_month or 0
            reset_at = user.gpt_messages_month_reset

            if reset_at:
                if reset_at.month != datetime.utcnow().month or reset_at.year != datetime.utcnow().year:
                    # New month — reset
                    used = 0
                    user.gpt_messages_used_month = 0
                    user.gpt_messages_month_reset = datetime.utcnow()
            else:
                # First time — set reset date
                user.gpt_messages_month_reset = datetime.utcnow()

            remaining = max(0, limit - used)

            return {
                'allowed': used < limit,
                'used': used,
                'limit': limit,
                'remaining': remaining,
                'tier': tier,
            }

    async def increment(self, telegram_id: int):
        """Increment GPT message counter for user."""
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperUserModel).where(
                    SniperUserModel.telegram_id == telegram_id
                )
            )
            user = result.scalar_one_or_none()
            if not user:
                return

            # Reset if new month
            reset_at = user.gpt_messages_month_reset
            if reset_at and (reset_at.month != datetime.utcnow().month or reset_at.year != datetime.utcnow().year):
                user.gpt_messages_used_month = 1
                user.gpt_messages_month_reset = datetime.utcnow()
            else:
                user.gpt_messages_used_month = (user.gpt_messages_used_month or 0) + 1
                if not user.gpt_messages_month_reset:
                    user.gpt_messages_month_reset = datetime.utcnow()


QUOTA_EXCEEDED_MESSAGE = (
    "Вы исчерпали лимит сообщений Tender-GPT на этот месяц.\n\n"
    "Ваш тариф: <b>{tier}</b> ({limit} сообщений/мес)\n"
    "Использовано: {used}/{limit}\n\n"
    "Для увеличения лимита перейдите на старший тариф "
    "или подключите аддон AI Unlimited."
)
