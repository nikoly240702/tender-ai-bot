"""
Database models and migrations for Tender Sniper.

Example usage:
    from tender_sniper.database import get_sniper_db

    # Get database instance
    db = await get_sniper_db()

    # Create user
    user_id = await db.create_or_update_user(
        telegram_id=123456789,
        username='user',
        subscription_tier='basic'
    )

    # Create filter
    filter_id = await db.create_filter(
        user_id=user_id,
        name='IT оборудование',
        keywords=['компьютеры', 'ноутбуки'],
        price_min=100_000,
        price_max=5_000_000
    )
"""

# Используем SQLAlchemy adapter для PostgreSQL
from .sqlalchemy_adapter import TenderSniperDB, get_sniper_db, serialize_for_json

# Оставляем для обратной совместимости
try:
    from .init_plans import init_subscription_plans, get_plan_limits
except ImportError:
    # Fallback для старой версии
    async def init_subscription_plans():
        pass

    def get_plan_limits(tier):
        return {'filters': 5, 'notifications': 15}

__all__ = [
    'TenderSniperDB',
    'get_sniper_db',
    'serialize_for_json',
    'init_subscription_plans',
    'get_plan_limits'
]