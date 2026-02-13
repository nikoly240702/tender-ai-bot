"""
Core database module с SQLAlchemy для PostgreSQL.

Unified database layer для всего приложения.
"""

import os
import logging
from typing import Optional
from datetime import datetime

from sqlalchemy import (
    Column, Integer, BigInteger, String, Float, Boolean,
    DateTime, Text, JSON, ForeignKey, Index, UniqueConstraint
)
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

# Base для всех моделей
Base = declarative_base()

# Глобальные переменные для engine и session factory
_engine = None
_async_session_factory = None


# ============================================
# МОДЕЛИ БД
# ============================================

class User(Base):
    """Модель пользователя (bot access control)."""
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_activity = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AccessRequest(Base):
    """Модель запроса доступа к боту."""
    __tablename__ = 'access_requests'

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    status = Column(String(50), default='pending', nullable=False)  # pending, approved, rejected
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SniperUser(Base):
    """Модель пользователя Tender Sniper."""
    __tablename__ = 'sniper_users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255), nullable=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)

    # Статус доступа (active/blocked)
    status = Column(String(50), default='active', nullable=False)
    blocked_reason = Column(Text, nullable=True)
    blocked_at = Column(DateTime, nullable=True)
    blocked_by = Column(BigInteger, nullable=True)  # Telegram ID админа

    # Тарифный план
    subscription_tier = Column(String(50), default='trial', nullable=False)  # trial, basic, premium
    filters_limit = Column(Integer, default=5, nullable=False)
    notifications_limit = Column(Integer, default=15, nullable=False)
    notifications_sent_today = Column(Integer, default=0, nullable=False)
    notifications_enabled = Column(Boolean, default=True, nullable=False)  # Автомониторинг вкл/выкл
    last_notification_reset = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Trial period
    trial_started_at = Column(DateTime, nullable=True)
    trial_expires_at = Column(DateTime, nullable=True)

    # AI analysis monthly quota
    ai_analyses_used_month = Column(Integer, default=0, nullable=False)
    ai_analyses_month_reset = Column(DateTime, nullable=True)
    has_ai_unlimited = Column(Boolean, default=False, nullable=False)
    ai_unlimited_expires_at = Column(DateTime, nullable=True)

    # Referral program
    referral_code = Column(String(20), unique=True, nullable=True, index=True)
    referred_by = Column(Integer, nullable=True)  # user_id who referred
    referral_bonus_days = Column(Integer, default=0)  # Accumulated bonus days

    # Flexible data storage (JSON)
    data = Column(JSON, default=dict)  # For follow-ups, reactivation tracking, etc.

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_activity = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    filters = relationship("SniperFilter", back_populates="user", cascade="all, delete-orphan")
    notifications = relationship("SniperNotification", back_populates="user", cascade="all, delete-orphan")


class SniperFilter(Base):
    """Модель фильтра для мониторинга тендеров."""
    __tablename__ = 'sniper_filters'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    keywords = Column(JSON, nullable=False)  # List[str]
    exclude_keywords = Column(JSON, default=list)  # List[str]
    price_min = Column(Float, nullable=True)
    price_max = Column(Float, nullable=True)
    regions = Column(JSON, default=list)  # List[str]
    customer_types = Column(JSON, default=list)  # List[str]
    tender_types = Column(JSON, default=list)  # List[str]
    law_type = Column(String(50), nullable=True)  # 44-FZ, 223-FZ
    purchase_stage = Column(String(100), nullable=True)
    purchase_method = Column(String(100), nullable=True)
    okpd2_codes = Column(JSON, default=list)  # List[str]
    min_deadline_days = Column(Integer, nullable=True)
    customer_keywords = Column(JSON, default=list)  # List[str]
    exact_match = Column(Boolean, default=False, nullable=False)  # Точный поиск (без AI расширения)

    # AI Семантика
    ai_intent = Column(Text, nullable=True)  # Детальное описание намерения фильтра для AI проверки
    expanded_keywords = Column(JSON, default=list)  # AI-расширенные ключевые слова (синонимы, связанные термины)

    # 🧪 БЕТА: Фаза 2 - Расширенные фильтры
    purchase_number = Column(String(100), nullable=True)  # Поиск по номеру закупки
    customer_inn = Column(JSON, default=list)  # List[str] - ИНН заказчиков
    excluded_customer_inns = Column(JSON, default=list)  # List[str] - Черный список ИНН
    excluded_customer_keywords = Column(JSON, default=list)  # List[str] - Черный список ключевых слов заказчика
    execution_regions = Column(JSON, default=list)  # List[str] - Регионы исполнения
    publication_days = Column(Integer, nullable=True)  # Дней с публикации (3, 7, 14, 30)
    primary_keywords = Column(JSON, default=list)  # List[str] - Главные ключевые слова (вес 2x)
    secondary_keywords = Column(JSON, default=list)  # List[str] - Дополнительные ключевые слова (вес 1x)
    search_in = Column(JSON, default=list)  # List[str] - Где искать: ['title', 'description', 'documents', 'customer_name']

    is_active = Column(Boolean, default=True, nullable=False)
    error_count = Column(Integer, default=0, nullable=False)  # Счетчик последовательных ошибок мониторинга
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("SniperUser", back_populates="filters")
    notifications = relationship("SniperNotification", back_populates="filter")

    # Indexes
    __table_args__ = (
        Index('ix_sniper_filters_user_active', 'user_id', 'is_active'),
    )


class SniperNotification(Base):
    """Модель уведомления о найденном тендере."""
    __tablename__ = 'sniper_notifications'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True)
    filter_id = Column(Integer, ForeignKey('sniper_filters.id', ondelete='SET NULL'), nullable=True, index=True)
    filter_name = Column(String(255), nullable=True)
    tender_number = Column(String(100), nullable=False, index=True)
    tender_name = Column(Text, nullable=False)
    tender_price = Column(Float, nullable=True)
    tender_url = Column(String(500), nullable=True)
    tender_region = Column(String(255), nullable=True)
    tender_customer = Column(Text, nullable=True)
    score = Column(Integer, default=0, nullable=False)
    matched_keywords = Column(JSON, default=list)  # List[str]
    published_date = Column(DateTime, nullable=True)
    submission_deadline = Column(DateTime, nullable=True)  # Срок подачи заявки
    tender_source = Column(String(50), default='automonitoring', nullable=False)  # instant_search или automonitoring
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    telegram_message_id = Column(BigInteger, nullable=True)
    sheets_exported = Column(Boolean, default=False, nullable=False)  # Экспортирован ли в Google Sheets
    sheets_exported_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("SniperUser", back_populates="notifications")
    filter = relationship("SniperFilter", back_populates="notifications")

    # Indexes + Constraints
    __table_args__ = (
        Index('ix_sniper_notifications_user_sent', 'user_id', 'sent_at'),
        Index('ix_sniper_notifications_tender', 'tender_number'),
        # Составной индекс для is_tender_notified() - ускоряет проверку дубликатов
        Index('ix_sniper_notifications_user_tender', 'user_id', 'tender_number'),
        # Unique constraint — предотвращает дубли уведомлений
        UniqueConstraint('user_id', 'filter_id', 'tender_number', name='uq_notification_user_filter_tender'),
    )


class FilterDraft(Base):
    """🧪 БЕТА: Черновик фильтра для восстановления прогресса при ошибках."""
    __tablename__ = 'filter_drafts'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False)
    telegram_id = Column(BigInteger, nullable=False, index=True)
    draft_data = Column(JSON, nullable=False)  # FSM state data
    current_step = Column(String(100), nullable=True)  # Текущий шаг wizard
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("SniperUser")

    # Indexes - unique per user
    __table_args__ = (
        Index('ix_filter_drafts_user_unique', 'user_id', unique=True),
    )


class TenderCache(Base):
    """Кеш обработанных тендеров (для дедупликации)."""
    __tablename__ = 'tender_cache'

    id = Column(Integer, primary_key=True, autoincrement=True)
    tender_number = Column(String(100), unique=True, nullable=False, index=True)
    tender_hash = Column(String(64), nullable=False)  # MD5 hash
    first_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    times_matched = Column(Integer, default=1, nullable=False)


class TenderFavorite(Base):
    """Избранные тендеры пользователя."""
    __tablename__ = 'tender_favorites'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True)
    tender_number = Column(String(100), nullable=False, index=True)
    tender_name = Column(Text, nullable=True)
    tender_price = Column(Float, nullable=True)
    tender_url = Column(String(500), nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    notes = Column(Text, nullable=True)  # Заметки пользователя

    # Indexes
    __table_args__ = (
        Index('ix_tender_favorites_user_tender', 'user_id', 'tender_number', unique=True),
    )


class HiddenTender(Base):
    """Скрытые тендеры (пользователь не хочет их видеть)."""
    __tablename__ = 'hidden_tenders'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True)
    tender_number = Column(String(100), nullable=False, index=True)
    hidden_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    reason = Column(String(255), nullable=True)  # Причина скрытия (опционально)

    # Indexes
    __table_args__ = (
        Index('ix_hidden_tenders_user_tender', 'user_id', 'tender_number', unique=True),
    )


class AIFeedback(Base):
    """
    Feedback для обучения AI семантики.

    Записывается когда:
    - Пользователь скрывает тендер (negative feedback)
    - Пользователь добавляет в избранное (positive feedback)
    - Пользователь переходит по ссылке (implicit positive)
    """
    __tablename__ = 'ai_feedback'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True)
    filter_id = Column(Integer, ForeignKey('sniper_filters.id', ondelete='SET NULL'), nullable=True, index=True)

    # Данные тендера
    tender_number = Column(String(100), nullable=False, index=True)
    tender_name = Column(Text, nullable=False)

    # Контекст фильтра на момент события
    filter_keywords = Column(JSON, nullable=True)  # Ключевые слова фильтра
    filter_intent = Column(Text, nullable=True)  # AI intent фильтра

    # AI решение на момент отправки
    ai_decision = Column(Boolean, nullable=True)  # True = AI сказал релевантен
    ai_confidence = Column(Integer, nullable=True)  # Уверенность AI (0-100)
    ai_reason = Column(Text, nullable=True)  # Причина от AI

    # Feedback пользователя
    feedback_type = Column(String(50), nullable=False)  # 'hidden', 'favorited', 'clicked', 'applied'
    feedback_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Дополнительно
    subscription_tier = Column(String(50), nullable=True)  # Тариф на момент события

    # Indexes
    __table_args__ = (
        Index('ix_ai_feedback_filter', 'filter_id'),
        Index('ix_ai_feedback_type', 'feedback_type'),
        Index('ix_ai_feedback_date', 'feedback_at'),
    )


class TenderReminder(Base):
    """Напоминания о тендерах."""
    __tablename__ = 'tender_reminders'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True)
    tender_number = Column(String(100), nullable=False, index=True)
    tender_name = Column(Text, nullable=True)
    tender_url = Column(String(500), nullable=True)
    reminder_time = Column(DateTime, nullable=False)  # Когда напомнить
    days_before_deadline = Column(Integer, nullable=True)  # За сколько дней до дедлайна
    sent = Column(Boolean, default=False, nullable=False)  # Отправлено ли напоминание
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Indexes
    __table_args__ = (
        Index('ix_tender_reminders_user_time', 'user_id', 'reminder_time'),
        Index('ix_tender_reminders_sent', 'sent', 'reminder_time'),
    )


class UserProfile(Base):
    """Профиль пользователя для персонализации."""
    __tablename__ = 'user_profiles'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), unique=True, nullable=False, index=True)

    # Профиль компании
    specialization = Column(String(500), nullable=True)  # IT оборудование, строительство и т.д.
    regions = Column(JSON, default=list)  # List[str] - регионы работы
    amount_min = Column(Float, nullable=True)  # Минимальная сумма контракта
    amount_max = Column(Float, nullable=True)  # Максимальная сумма контракта

    # Дополнительные параметры
    licenses = Column(JSON, default=list)  # List[str] - наличие лицензий и допусков
    experience_years = Column(Integer, nullable=True)  # Опыт работы в годах
    preferred_law_types = Column(JSON, default=list)  # List[str] - предпочтения по законам (44-ФЗ, 223-ФЗ)

    # Метаданные
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ============================================
# NEW MODELS (Phase 2.1)
# ============================================

class SearchHistory(Base):
    """История поисков пользователя."""
    __tablename__ = 'search_history'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True)
    filter_id = Column(Integer, ForeignKey('sniper_filters.id', ondelete='SET NULL'), nullable=True)

    # Search details
    search_type = Column(String(50), nullable=False)  # instant_search, archive_search
    keywords = Column(JSON, nullable=False)  # List[str]
    results_count = Column(Integer, default=0)

    # Execution
    executed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    duration_ms = Column(Integer, nullable=True)  # Search duration in milliseconds

    # Relationships
    user = relationship("SniperUser")
    filter = relationship("SniperFilter")

    __table_args__ = (
        Index('ix_search_history_user_time', 'user_id', 'executed_at'),
    )


class UserFeedback(Base):
    """Feedback пользователей на тендеры."""
    __tablename__ = 'user_feedback'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True)
    filter_id = Column(Integer, ForeignKey('sniper_filters.id', ondelete='SET NULL'), nullable=True, index=True)
    tender_number = Column(String(100), nullable=False, index=True)

    # Feedback type: interesting, hidden, irrelevant
    feedback_type = Column(String(50), nullable=False)

    # Context for ML
    tender_name = Column(Text, nullable=True)
    matched_keywords = Column(JSON, default=list)  # List[str]
    original_score = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("SniperUser")
    filter = relationship("SniperFilter")

    __table_args__ = (
        Index('ix_user_feedback_user_type', 'user_id', 'feedback_type'),
    )


class Subscription(Base):
    """Подписки пользователей."""
    __tablename__ = 'subscriptions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), unique=True, nullable=False, index=True)

    # Subscription tier: trial, basic, premium
    tier = Column(String(50), nullable=False, default='trial')
    status = Column(String(50), nullable=False, default='active')  # active, expired, cancelled

    # Dates
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    cancelled_at = Column(DateTime, nullable=True)

    # Limits
    max_filters = Column(Integer, default=3)
    max_notifications_per_day = Column(Integer, default=50)

    # Payment
    last_payment_id = Column(String(255), nullable=True)
    last_payment_at = Column(DateTime, nullable=True)
    next_billing_date = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("SniperUser")

    def is_active(self) -> bool:
        """Check if subscription is active."""
        return self.status == 'active' and self.expires_at > datetime.utcnow()

    def is_trial(self) -> bool:
        """Check if trial subscription."""
        return self.tier == 'trial'

    def days_remaining(self) -> int:
        """Days until expiration."""
        if not self.is_active():
            return 0
        delta = self.expires_at - datetime.utcnow()
        return max(0, delta.days)


class SatisfactionSurvey(Base):
    """Опросы удовлетворённости (CSAT)."""
    __tablename__ = 'satisfaction_surveys'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True)

    rating = Column(Integer, nullable=True)  # 1-5 stars
    comment = Column(Text, nullable=True)

    # Context
    trigger = Column(String(100), nullable=True)  # after_10_notifications, weekly, manual
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("SniperUser")


class ViewedTender(Base):
    """Просмотренные тендеры."""
    __tablename__ = 'viewed_tenders'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True)
    tender_number = Column(String(100), nullable=False, index=True)
    viewed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('ix_viewed_tenders_user_tender', 'user_id', 'tender_number', unique=True),
    )


class QuickFilterTemplate(Base):
    """Шаблоны готовых фильтров (для кастомных пользовательских шаблонов)."""
    __tablename__ = 'quick_filter_templates'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=True, index=True)  # NULL = system template

    name = Column(String(255), nullable=False)
    icon = Column(String(10), nullable=True)
    description = Column(Text, nullable=True)
    industry = Column(String(100), nullable=True)

    # Filter settings
    keywords = Column(JSON, nullable=False)  # List[str]
    exclude_keywords = Column(JSON, default=list)
    price_min = Column(Float, nullable=True)
    price_max = Column(Float, nullable=True)
    regions = Column(JSON, default=list)

    # Metadata
    is_public = Column(Boolean, default=False)  # Доступен всем
    usage_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("SniperUser")


# ============================================
# PHASE 3: MONETIZATION & ADMIN MODELS
# ============================================

class BroadcastMessage(Base):
    """История рассылок сообщений."""
    __tablename__ = 'broadcast_messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_text = Column(Text, nullable=False)
    target_tier = Column(String(50), default='all')  # all, trial, basic, premium
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    total_recipients = Column(Integer, default=0)
    successful = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    created_by = Column(String(100), nullable=True)  # admin username


class Promocode(Base):
    """Промокоды для подписок."""
    __tablename__ = 'promocodes'

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    tier = Column(String(50), nullable=False)  # basic, premium
    days = Column(Integer, nullable=False)  # Дней подписки
    max_uses = Column(Integer, nullable=True)  # NULL = unlimited
    current_uses = Column(Integer, default=0)
    expires_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String(100), nullable=True)


class Payment(Base):
    """История платежей YooKassa."""
    __tablename__ = 'payments'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True)
    yookassa_payment_id = Column(String(100), unique=True, nullable=False, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String(10), default='RUB')
    tier = Column(String(50), nullable=False)  # basic, premium
    status = Column(String(50), default='pending')  # pending, succeeded, canceled
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("SniperUser")


class Referral(Base):
    """Реферальные связи."""
    __tablename__ = 'referrals'

    id = Column(Integer, primary_key=True, autoincrement=True)
    referrer_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True)
    referred_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True)
    bonus_given = Column(Boolean, default=False)
    bonus_days = Column(Integer, default=7)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    referrer = relationship("SniperUser", foreign_keys=[referrer_id])
    referred = relationship("SniperUser", foreign_keys=[referred_id])

    __table_args__ = (
        Index('ix_referrals_referrer', 'referrer_id'),
        Index('ix_referrals_referred', 'referred_id', unique=True),  # User can only be referred once
    )


class UserEvent(Base):
    """
    События пользователей для аналитики.

    Типы событий:
    - registration: Регистрация пользователя
    - broadcast_delivered: Рассылка доставлена
    - broadcast_clicked: Клик по кнопке в рассылке
    - subscription_viewed: Просмотр тарифов
    - subscription_purchased: Покупка подписки
    - filter_created: Создание фильтра
    - filter_deleted: Удаление фильтра
    - search_performed: Выполнен поиск
    - bot_blocked: Бот заблокирован
    - bot_unblocked: Бот разблокирован
    - referral_link_generated: Сгенерирована реферальная ссылка
    - referral_used: Использована реферальная ссылка
    """
    __tablename__ = 'user_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=True, index=True)
    telegram_id = Column(BigInteger, nullable=True, index=True)  # На случай если user_id ещё нет
    event_type = Column(String(50), nullable=False, index=True)
    event_data = Column(JSON, nullable=True)  # Дополнительные данные о событии
    broadcast_id = Column(Integer, ForeignKey('broadcast_messages.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationship
    user = relationship("SniperUser", foreign_keys=[user_id])
    broadcast = relationship("BroadcastMessage", foreign_keys=[broadcast_id])

    __table_args__ = (
        Index('ix_user_events_type_date', 'event_type', 'created_at'),
        Index('ix_user_events_user_type', 'user_id', 'event_type'),
    )


class GoogleSheetsConfig(Base):
    """Конфигурация Google Sheets интеграции для пользователя."""
    __tablename__ = 'google_sheets_config'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('sniper_users.id', ondelete='CASCADE'), nullable=False, index=True)
    spreadsheet_id = Column(String(255), nullable=False)
    sheet_name = Column(String(255), default='Тендеры')
    columns = Column(JSON, nullable=False)  # ['link', 'name', 'customer', ...]
    ai_enrichment = Column(Boolean, default=False)  # Premium: обогащение из документации
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("SniperUser")

    # Unique per user
    __table_args__ = (
        Index('ix_google_sheets_config_user', 'user_id', unique=True),
    )


# ============================================
# DATABASE ENGINE & SESSION
# ============================================

def get_database_url() -> str:
    """
    Получение DATABASE_URL из окружения.

    Поддерживает:
    - PostgreSQL: postgresql+asyncpg://user:pass@host:port/dbname
    - SQLite (fallback): sqlite+aiosqlite:///path/to/db.sqlite

    Returns:
        Database URL string
    """
    db_url = os.getenv('DATABASE_URL')

    if not db_url:
        # Fallback на SQLite для локальной разработки
        logger.warning("DATABASE_URL не задан, используется SQLite fallback")
        return "sqlite+aiosqlite:///tender_bot.db"

    # Railway/Heroku дают postgres://, нужно заменить на postgresql+asyncpg://
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql+asyncpg://', 1)
    elif db_url.startswith('postgresql://'):
        db_url = db_url.replace('postgresql://', 'postgresql+asyncpg://', 1)

    return db_url


async def init_database(echo: bool = False):
    """
    Инициализация database engine и создание таблиц.

    Args:
        echo: Включить SQL логирование
    """
    global _engine, _async_session_factory

    if _engine is not None:
        logger.info("Database уже инициализирована")
        return

    database_url = get_database_url()
    is_sqlite = 'sqlite' in database_url

    logger.info(f"Инициализация database: {database_url.split('@')[-1] if '@' in database_url else 'SQLite'}")

    # Создаем engine
    logger.info("   Создание SQLAlchemy engine...")

    if is_sqlite:
        # SQLite не поддерживает pooling - используем NullPool без pool_size/max_overflow
        _engine = create_async_engine(
            database_url,
            echo=echo,
            poolclass=NullPool,
        )
    else:
        # PostgreSQL - полноценный пул соединений
        _engine = create_async_engine(
            database_url,
            echo=echo,
            pool_pre_ping=True,
            pool_size=20,
            max_overflow=40,
            pool_recycle=1800,      # Переподключение каждые 30 минут
        )
    logger.info("   ✅ Engine создан")

    # Создаем session factory
    logger.info("   Создание session factory...")
    _async_session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False
    )
    logger.info("   ✅ Session factory создан")

    # Создаем таблицы (если их нет)
    logger.info("   Подключение к PostgreSQL для создания таблиц...")
    async with _engine.begin() as conn:
        logger.info("   Соединение установлено, выполнение CREATE TABLE...")
        await conn.run_sync(Base.metadata.create_all)
        logger.info("   ✅ Таблицы созданы/проверены")

    logger.info("✅ Database инициализирована")


async def get_session() -> AsyncSession:
    """
    Получение database session.

    Returns:
        AsyncSession instance
    """
    if _async_session_factory is None:
        await init_database()

    return _async_session_factory()


async def close_database():
    """Закрытие database connections."""
    global _engine

    if _engine is not None:
        await _engine.dispose()
        logger.info("✅ Database connections закрыты")
        _engine = None


# ============================================
# CONTEXT MANAGER
# ============================================

class DatabaseSession:
    """Context manager для database sessions."""

    def __init__(self):
        self.session: Optional[AsyncSession] = None

    async def __aenter__(self) -> AsyncSession:
        self.session = await get_session()
        return self.session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            if exc_type is not None:
                await self.session.rollback()
            else:
                await self.session.commit()
            await self.session.close()


# ============================================
# ЭКСПОРТ
# ============================================

__all__ = [
    'Base',
    'User',
    'AccessRequest',
    'SniperUser',
    'SniperFilter',
    'SniperNotification',
    'TenderCache',
    'FilterDraft',
    'TenderFavorite',
    'HiddenTender',
    'TenderReminder',
    'UserProfile',
    # Phase 2.1 models
    'SearchHistory',
    'UserFeedback',
    'Subscription',
    'SatisfactionSurvey',
    'ViewedTender',
    'QuickFilterTemplate',
    # Phase 3 - Monetization
    'BroadcastMessage',
    'Promocode',
    'Payment',
    'Referral',
    # Google Sheets Integration
    'GoogleSheetsConfig',
    # Functions
    'init_database',
    'get_session',
    'close_database',
    'DatabaseSession'
]
