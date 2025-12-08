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
    DateTime, Text, JSON, ForeignKey, Index
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
    subscription_tier = Column(String(50), default='free', nullable=False)  # free, basic, premium
    filters_limit = Column(Integer, default=5, nullable=False)
    notifications_limit = Column(Integer, default=15, nullable=False)
    notifications_sent_today = Column(Integer, default=0, nullable=False)
    last_notification_reset = Column(DateTime, default=datetime.utcnow, nullable=False)
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
    is_active = Column(Boolean, default=True, nullable=False)
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
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    telegram_message_id = Column(BigInteger, nullable=True)

    # Relationships
    user = relationship("SniperUser", back_populates="notifications")
    filter = relationship("SniperFilter", back_populates="notifications")

    # Indexes
    __table_args__ = (
        Index('ix_sniper_notifications_user_sent', 'user_id', 'sent_at'),
        Index('ix_sniper_notifications_tender', 'tender_number'),
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
    _engine = create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,  # Проверка соединений перед использованием
        pool_size=20 if not is_sqlite else 1,  # SQLite не поддерживает pooling
        max_overflow=40 if not is_sqlite else 0,
        poolclass=NullPool if is_sqlite else None,  # SQLite = no pool
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
    'init_database',
    'get_session',
    'close_database',
    'DatabaseSession'
]
