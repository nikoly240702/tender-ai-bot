"""
AI Features Access Control для Tender Sniper.

Модуль для управления доступом к AI функциям.
AI функции доступны для basic и premium пользователей (с месячными лимитами).
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from functools import wraps

logger = logging.getLogger(__name__)

# Тарифы с AI функциями
AI_ENABLED_TIERS = {'basic', 'premium'}

# Месячные лимиты AI-анализов
AI_MONTHLY_LIMITS = {
    'trial': 0,
    'basic': 10,
    'premium': 50,
    'admin': 100000,
}

# Список AI функций
AI_FEATURES = {
    'summarization': 'AI-резюме тендеров',
    'red_flags': 'Детекция красных флагов',
    'keyword_recommendations': 'Умные рекомендации ключевых слов',
    'feedback_learning': 'Обучение на основе ваших действий',
    'document_extraction': 'Извлечение данных из документации',
}


def has_ai_access(subscription_tier: str) -> bool:
    """
    Проверяет, имеет ли пользователь доступ к AI функциям.

    Args:
        subscription_tier: Тариф пользователя (trial, basic, premium)

    Returns:
        True если AI функции доступны
    """
    return subscription_tier in AI_ENABLED_TIERS


def get_ai_upgrade_message() -> str:
    """Возвращает сообщение о необходимости upgrade для AI функций."""
    features_list = "\n".join([f"• {desc}" for desc in AI_FEATURES.values()])
    return f"""
🤖 <b>AI-функции доступны на тарифах Basic и Premium</b>

Что входит в AI-пакет:
{features_list}

• Basic: 10 AI-анализов/мес
• Premium: 50 AI-анализов/мес
• AI Unlimited: безлимит (+1 490 ₽/мес)

Оформите подписку для доступа к умным функциям!
"""


async def check_ai_analysis_quota(telegram_id: int) -> Tuple[bool, int, int]:
    """
    Проверяет месячную квоту AI-анализов.

    Returns:
        (can_use, used, limit)
    """
    from tender_sniper.database import get_sniper_db
    from database import DatabaseSession, SniperUser
    from sqlalchemy import select, update

    db = await get_sniper_db()
    user = await db.get_user_by_telegram_id(telegram_id)
    if not user:
        return (False, 0, 0)

    tier = user.get('subscription_tier', 'trial')

    # AI Unlimited — безлимит
    if user.get('has_ai_unlimited'):
        ai_unlimited_expires = user.get('ai_unlimited_expires_at')
        if ai_unlimited_expires and isinstance(ai_unlimited_expires, datetime) and ai_unlimited_expires > datetime.now():
            return (True, 0, 999999)

    limit = AI_MONTHLY_LIMITS.get(tier, 0)
    if limit == 0:
        return (False, 0, 0)

    used = user.get('ai_analyses_used_month', 0)
    month_reset = user.get('ai_analyses_month_reset')

    # Сброс счётчика если новый месяц
    now = datetime.now()
    need_reset = False
    if month_reset:
        if isinstance(month_reset, str):
            try:
                month_reset = datetime.fromisoformat(month_reset)
            except:
                need_reset = True
        if isinstance(month_reset, datetime) and (now.year > month_reset.year or now.month > month_reset.month):
            need_reset = True
    else:
        need_reset = True

    if need_reset:
        used = 0
        async with DatabaseSession() as session:
            await session.execute(
                update(SniperUser)
                .where(SniperUser.telegram_id == telegram_id)
                .values(ai_analyses_used_month=0, ai_analyses_month_reset=now)
            )

    return (used < limit, used, limit)


async def increment_ai_analysis_usage(telegram_id: int) -> None:
    """Увеличивает счётчик использований AI-анализа."""
    from database import DatabaseSession, SniperUser
    from sqlalchemy import update

    async with DatabaseSession() as session:
        await session.execute(
            update(SniperUser)
            .where(SniperUser.telegram_id == telegram_id)
            .values(ai_analyses_used_month=SniperUser.ai_analyses_used_month + 1)
        )


def check_ai_feature(feature_name: str):
    """
    Декоратор для проверки доступа к конкретной AI функции.

    Использование:
        @check_ai_feature('summarization')
        async def get_summary(user_tier: str, tender_text: str):
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Ищем subscription_tier в аргументах
            user_tier = kwargs.get('subscription_tier') or kwargs.get('user_tier')

            if not user_tier:
                # Пробуем найти в позиционных аргументах (первый аргумент)
                if args:
                    user_tier = args[0] if isinstance(args[0], str) else None

            if not user_tier or not has_ai_access(user_tier):
                logger.info(f"AI feature '{feature_name}' denied for tier: {user_tier}")
                return None

            return await func(*args, **kwargs)
        return wrapper
    return decorator


class AIFeatureGate:
    """
    Класс для управления доступом к AI функциям.

    Использование:
        gate = AIFeatureGate(user_subscription_tier)
        if gate.can_use('summarization'):
            summary = await summarizer.summarize(text)
    """

    def __init__(self, subscription_tier: str):
        self.tier = subscription_tier
        self.has_access = has_ai_access(subscription_tier)

    def can_use(self, feature: str) -> bool:
        """Проверяет доступ к конкретной AI функции."""
        if feature not in AI_FEATURES:
            logger.warning(f"Unknown AI feature: {feature}")
            return False
        return self.has_access

    def get_available_features(self) -> List[str]:
        """Возвращает список доступных AI функций."""
        if self.has_access:
            return list(AI_FEATURES.keys())
        return []

    def get_upgrade_prompt(self) -> str:
        """Возвращает prompt для upgrade если нет доступа."""
        if self.has_access:
            return ""
        return get_ai_upgrade_message()


# ============================================
# Хелпер функции для использования в handlers
# ============================================

async def get_user_ai_gate(db_adapter, telegram_id: int) -> AIFeatureGate:
    """
    Получает AIFeatureGate для пользователя по telegram_id.

    Args:
        db_adapter: Адаптер базы данных
        telegram_id: Telegram ID пользователя

    Returns:
        AIFeatureGate с проверенным доступом
    """
    user = await db_adapter.get_user_by_telegram_id(telegram_id)
    if not user:
        return AIFeatureGate('trial')  # По умолчанию trial

    return AIFeatureGate(user.get('subscription_tier', 'trial'))


def format_ai_feature_locked_message(feature: str) -> str:
    """
    Форматирует сообщение о заблокированной AI функции.

    Args:
        feature: Название функции

    Returns:
        Отформатированное сообщение
    """
    feature_name = AI_FEATURES.get(feature, feature)
    return f"""
🔒 <b>Функция недоступна</b>

<b>{feature_name}</b> доступна на тарифах Basic и Premium.

• Basic (990 ₽/мес): 10 AI-анализов/мес
• Premium (2 990 ₽/мес): 50 AI-анализов/мес
• AI Unlimited (+1 490 ₽/мес): безлимит

Оформите подписку: /subscription
"""
