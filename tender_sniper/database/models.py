"""
Database models for Tender Sniper.

Система подписок, фильтров и уведомлений для real-time мониторинга тендеров.
"""

import aiosqlite
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
import json
import logging

logger = logging.getLogger(__name__)


def serialize_for_json(obj: Any) -> Any:
    """
    Рекурсивная сериализация объектов для JSON.
    Конвертирует datetime в ISO формат строки.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: serialize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [serialize_for_json(item) for item in obj]
    return obj


class TenderSniperDB:
    """База данных Tender Sniper."""

    def __init__(self, db_path: Path):
        """
        Инициализация базы данных.

        Args:
            db_path: Путь к файлу базы данных
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def init_db(self):
        """Создание таблиц базы данных."""
        async with aiosqlite.connect(self.db_path) as db:
            # ============================================
            # ПОЛЬЗОВАТЕЛИ И ПОДПИСКИ
            # ============================================

            # Расширенная таблица пользователей
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sniper_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id BIGINT UNIQUE NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    subscription_tier TEXT DEFAULT 'free',
                    subscription_status TEXT DEFAULT 'active',
                    subscription_start TEXT,
                    subscription_end TEXT,
                    payment_id TEXT,
                    notifications_enabled INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_activity TEXT,
                    settings TEXT
                )
            """)

            # Таблица тарифных планов
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscription_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    display_name TEXT NOT NULL,
                    price_monthly INTEGER NOT NULL,
                    price_yearly INTEGER,
                    max_filters INTEGER DEFAULT 5,
                    max_notifications_daily INTEGER DEFAULT 10,
                    ai_analysis_enabled INTEGER DEFAULT 0,
                    api_access_enabled INTEGER DEFAULT 0,
                    priority_support INTEGER DEFAULT 0,
                    description TEXT,
                    features TEXT,
                    active INTEGER DEFAULT 1
                )
            """)

            # История платежей
            await db.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    payment_provider TEXT NOT NULL,
                    payment_id TEXT UNIQUE NOT NULL,
                    amount INTEGER NOT NULL,
                    currency TEXT DEFAULT 'RUB',
                    plan_name TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    paid_at TEXT,
                    metadata TEXT,
                    FOREIGN KEY (user_id) REFERENCES sniper_users (id)
                )
            """)

            # ============================================
            # ФИЛЬТРЫ И МАТЧИНГ
            # ============================================

            # Пользовательские фильтры
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_filters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    keywords TEXT,
                    exclude_keywords TEXT,
                    price_min INTEGER,
                    price_max INTEGER,
                    regions TEXT,
                    customer_types TEXT,
                    tender_types TEXT,
                    -- Новые критерии фильтрации
                    law_type TEXT,
                    purchase_stage TEXT,
                    purchase_method TEXT,
                    okpd2_codes TEXT,
                    min_deadline_days INTEGER,
                    customer_keywords TEXT,
                    date_from TEXT,
                    date_to TEXT,
                    -- Системные поля
                    active INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    match_count INTEGER DEFAULT 0,
                    last_match_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES sniper_users (id)
                )
            """)

            # История матчингов (какой фильтр поймал какой тендер)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS filter_matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filter_id INTEGER NOT NULL,
                    tender_number TEXT NOT NULL,
                    match_score INTEGER NOT NULL,
                    matched_at TEXT NOT NULL,
                    notified INTEGER DEFAULT 0,
                    user_action TEXT,
                    FOREIGN KEY (filter_id) REFERENCES user_filters (id)
                )
            """)

            # ============================================
            # МОНИТОРИНГ ТЕНДЕРОВ
            # ============================================

            # Кэш мониторинга тендеров (чтобы не отправлять дубли)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS monitored_tenders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tender_number TEXT UNIQUE NOT NULL,
                    name TEXT,
                    customer_name TEXT,
                    nmck REAL,
                    published_date TEXT,
                    end_date TEXT,
                    url TEXT,
                    region TEXT,
                    tender_type TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_checked_at TEXT,
                    status TEXT DEFAULT 'active',
                    raw_data TEXT
                )
            """)

            # ============================================
            # УВЕДОМЛЕНИЯ
            # ============================================

            # История отправленных уведомлений
            await db.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    filter_id INTEGER,
                    tender_number TEXT NOT NULL,
                    notification_type TEXT DEFAULT 'match',
                    message TEXT,
                    sent_at TEXT NOT NULL,
                    delivered INTEGER DEFAULT 1,
                    read INTEGER DEFAULT 0,
                    clicked INTEGER DEFAULT 0,
                    user_action TEXT,
                    FOREIGN KEY (user_id) REFERENCES sniper_users (id),
                    FOREIGN KEY (filter_id) REFERENCES user_filters (id)
                )
            """)

            # Квоты на уведомления (для тарифов)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS notification_quotas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    notifications_sent INTEGER DEFAULT 0,
                    notifications_limit INTEGER NOT NULL,
                    UNIQUE(user_id, date),
                    FOREIGN KEY (user_id) REFERENCES sniper_users (id)
                )
            """)

            # ============================================
            # АНАЛИТИКА И СТАТИСТИКА
            # ============================================

            # События пользователей (для аналитики)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    event_data TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES sniper_users (id)
                )
            """)

            # ============================================
            # ИНДЕКСЫ ДЛЯ ПРОИЗВОДИТЕЛЬНОСТИ
            # ============================================

            # Индексы для пользователей
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_sniper_users_telegram_id
                ON sniper_users(telegram_id)
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_sniper_users_subscription
                ON sniper_users(subscription_tier, subscription_status)
            """)

            # Индексы для фильтров
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_filters_user_active
                ON user_filters(user_id, active)
            """)

            # Индексы для матчингов
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_filter_matches_filter
                ON filter_matches(filter_id, matched_at DESC)
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_filter_matches_tender
                ON filter_matches(tender_number)
            """)

            # Индексы для тендеров
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_monitored_tenders_number
                ON monitored_tenders(tender_number)
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_monitored_tenders_published
                ON monitored_tenders(published_date DESC)
            """)

            # Индексы для уведомлений
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_notifications_user
                ON notifications(user_id, sent_at DESC)
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_notifications_tender
                ON notifications(tender_number)
            """)

            # Индексы для платежей
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_payments_user
                ON payments(user_id, created_at DESC)
            """)

            await db.commit()
            logger.info("✅ База данных Tender Sniper инициализирована")

    # ============================================
    # МЕТОДЫ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ
    # ============================================

    async def create_or_update_user(
        self,
        telegram_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        subscription_tier: str = 'free'
    ) -> int:
        """Создание или обновление пользователя."""
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now().isoformat()

            cursor = await db.execute("""
                INSERT INTO sniper_users
                (telegram_id, username, first_name, last_name, subscription_tier, created_at, last_activity)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    last_activity = excluded.last_activity
            """, (telegram_id, username, first_name, last_name, subscription_tier, now, now))

            await db.commit()
            return cursor.lastrowid

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        """Получение пользователя по Telegram ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute("""
                SELECT * FROM sniper_users WHERE telegram_id = ?
            """, (telegram_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
                return None

    async def update_subscription(
        self,
        telegram_id: int,
        tier: str,
        payment_id: Optional[str] = None,
        months: int = 1
    ) -> bool:
        """Обновление подписки пользователя."""
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now()
            start = now.isoformat()
            end = (now + timedelta(days=30 * months)).isoformat()

            await db.execute("""
                UPDATE sniper_users
                SET subscription_tier = ?,
                    subscription_status = 'active',
                    subscription_start = ?,
                    subscription_end = ?,
                    payment_id = ?
                WHERE telegram_id = ?
            """, (tier, start, end, payment_id, telegram_id))

            await db.commit()
            return True

    # ============================================
    # МЕТОДЫ ДЛЯ ФИЛЬТРОВ
    # ============================================

    async def create_filter(
        self,
        user_id: int,
        name: str,
        keywords: Optional[List[str]] = None,
        exclude_keywords: Optional[List[str]] = None,
        price_min: Optional[int] = None,
        price_max: Optional[int] = None,
        regions: Optional[List[str]] = None,
        customer_types: Optional[List[str]] = None,
        tender_types: Optional[List[str]] = None,
        # Новые параметры
        law_type: Optional[str] = None,
        purchase_stage: Optional[str] = None,
        purchase_method: Optional[str] = None,
        okpd2_codes: Optional[List[str]] = None,
        min_deadline_days: Optional[int] = None,
        customer_keywords: Optional[List[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None
    ) -> int:
        """Создание фильтра."""
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now().isoformat()

            cursor = await db.execute("""
                INSERT INTO user_filters
                (user_id, name, keywords, exclude_keywords, price_min, price_max,
                 regions, customer_types, tender_types,
                 law_type, purchase_stage, purchase_method, okpd2_codes,
                 min_deadline_days, customer_keywords, date_from, date_to,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, name,
                json.dumps(keywords or [], ensure_ascii=False),
                json.dumps(exclude_keywords or [], ensure_ascii=False),
                price_min, price_max,
                json.dumps(regions or [], ensure_ascii=False),
                json.dumps(customer_types or [], ensure_ascii=False),
                json.dumps(tender_types or [], ensure_ascii=False),
                law_type,
                purchase_stage,
                purchase_method,
                json.dumps(okpd2_codes or [], ensure_ascii=False),
                min_deadline_days,
                json.dumps(customer_keywords or [], ensure_ascii=False),
                date_from, date_to,
                now, now
            ))

            await db.commit()
            return cursor.lastrowid

    async def get_active_filters(self, user_id: int) -> List[Dict[str, Any]]:
        """Получение активных фильтров пользователя."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute("""
                SELECT * FROM user_filters
                WHERE user_id = ? AND active = 1
                ORDER BY created_at DESC
            """, (user_id,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_all_active_filters(self) -> List[Dict[str, Any]]:
        """Получение всех активных фильтров всех пользователей."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute("""
                SELECT f.*, u.telegram_id, u.subscription_tier, u.notifications_enabled
                FROM user_filters f
                JOIN sniper_users u ON f.user_id = u.id
                WHERE f.active = 1 AND u.notifications_enabled = 1
                  AND u.subscription_status = 'active'
            """) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    # ============================================
    # МЕТОДЫ ДЛЯ ТЕНДЕРОВ
    # ============================================

    async def add_or_update_tender(
        self,
        tender_number: str,
        name: Optional[str] = None,
        customer_name: Optional[str] = None,
        nmck: Optional[float] = None,
        published_date: Optional[str] = None,
        end_date: Optional[str] = None,
        url: Optional[str] = None,
        region: Optional[str] = None,
        tender_type: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None
    ) -> int:
        """Добавление или обновление тендера в мониторинг."""
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now().isoformat()

            cursor = await db.execute("""
                INSERT INTO monitored_tenders
                (tender_number, name, customer_name, nmck, published_date, end_date,
                 url, region, tender_type, first_seen_at, last_checked_at, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tender_number) DO UPDATE SET
                    name = excluded.name,
                    customer_name = excluded.customer_name,
                    nmck = excluded.nmck,
                    published_date = excluded.published_date,
                    end_date = excluded.end_date,
                    url = excluded.url,
                    region = excluded.region,
                    tender_type = excluded.tender_type,
                    last_checked_at = excluded.last_checked_at,
                    raw_data = excluded.raw_data
            """, (
                tender_number, name, customer_name, nmck, published_date, end_date,
                url, region, tender_type, now, now,
                json.dumps(serialize_for_json(raw_data), ensure_ascii=False) if raw_data else None
            ))

            await db.commit()
            return cursor.lastrowid

    async def is_tender_notified(self, tender_number: str, user_id: int) -> bool:
        """Проверка, был ли уже отправлен notification для этого тендера."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT id FROM notifications
                WHERE tender_number = ? AND user_id = ?
            """, (tender_number, user_id)) as cursor:
                row = await cursor.fetchone()
                return row is not None

    # ============================================
    # МЕТОДЫ ДЛЯ УВЕДОМЛЕНИЙ
    # ============================================

    async def save_notification(
        self,
        user_id: int,
        tender_number: str,
        filter_id: Optional[int] = None,
        notification_type: str = 'match',
        message: Optional[str] = None
    ) -> int:
        """Сохранение отправленного уведомления."""
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now().isoformat()

            cursor = await db.execute("""
                INSERT INTO notifications
                (user_id, filter_id, tender_number, notification_type, message, sent_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, filter_id, tender_number, notification_type, message, now))

            await db.commit()
            return cursor.lastrowid

    async def check_notification_quota(self, user_id: int, limit: int) -> bool:
        """Проверка квоты на уведомления."""
        async with aiosqlite.connect(self.db_path) as db:
            today = datetime.now().date().isoformat()

            # Получаем или создаем запись квоты
            async with db.execute("""
                SELECT notifications_sent FROM notification_quotas
                WHERE user_id = ? AND date = ?
            """, (user_id, today)) as cursor:
                row = await cursor.fetchone()

                if not row:
                    # Создаем новую запись
                    await db.execute("""
                        INSERT INTO notification_quotas (user_id, date, notifications_sent, notifications_limit)
                        VALUES (?, ?, 0, ?)
                    """, (user_id, today, limit))
                    await db.commit()
                    return True

                sent = row[0]
                return sent < limit

    async def increment_notification_quota(self, user_id: int):
        """Увеличение счетчика отправленных уведомлений."""
        async with aiosqlite.connect(self.db_path) as db:
            today = datetime.now().date().isoformat()

            await db.execute("""
                UPDATE notification_quotas
                SET notifications_sent = notifications_sent + 1
                WHERE user_id = ? AND date = ?
            """, (user_id, today))

            await db.commit()

    # ============================================
    # МЕТОДЫ ДЛЯ СТАТИСТИКИ
    # ============================================

    async def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        """Получение статистики пользователя."""
        async with aiosqlite.connect(self.db_path) as db:
            # Количество фильтров
            async with db.execute("""
                SELECT COUNT(*) FROM user_filters WHERE user_id = ? AND active = 1
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()
                active_filters = row[0]

            # Количество матчей
            async with db.execute("""
                SELECT COUNT(*) FROM filter_matches fm
                JOIN user_filters uf ON fm.filter_id = uf.id
                WHERE uf.user_id = ?
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()
                total_matches = row[0]

            # Количество уведомлений за сегодня
            today = datetime.now().date().isoformat()
            async with db.execute("""
                SELECT notifications_sent, notifications_limit
                FROM notification_quotas
                WHERE user_id = ? AND date = ?
            """, (user_id, today)) as cursor:
                row = await cursor.fetchone()
                if row:
                    notifications_today = row[0]
                    notifications_limit = row[1]
                else:
                    notifications_today = 0
                    notifications_limit = 10  # Default for free tier

            return {
                'active_filters': active_filters,
                'total_matches': total_matches,
                'notifications_today': notifications_today,
                'notifications_limit': notifications_limit
            }


# Глобальный экземпляр базы данных
_sniper_db_instance: Optional[TenderSniperDB] = None


async def get_sniper_db(db_path: Path = None) -> TenderSniperDB:
    """
    Получение глобального экземпляра базы данных Tender Sniper.

    Args:
        db_path: Путь к файлу БД (используется только при первом вызове)

    Returns:
        Экземпляр TenderSniperDB
    """
    global _sniper_db_instance

    if _sniper_db_instance is None:
        if db_path is None:
            db_path = Path(__file__).parent / 'sniper.db'

        _sniper_db_instance = TenderSniperDB(db_path)
        await _sniper_db_instance.init_db()

    return _sniper_db_instance
