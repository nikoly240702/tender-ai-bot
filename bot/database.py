"""
Модуль для работы с базой данных SQLite.
Хранит историю поисков пользователей.
"""

import aiosqlite
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class Database:
    """Класс для работы с базой данных."""

    def __init__(self, db_path: Path):
        """
        Инициализация базы данных.

        Args:
            db_path: Путь к файлу базы данных
        """
        self.db_path = db_path
        # Создаем директорию, если не существует
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def init_db(self):
        """Создание таблиц базы данных."""
        async with aiosqlite.connect(self.db_path) as db:
            # Таблица пользователей
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TEXT NOT NULL,
                    last_activity TEXT
                )
            """)

            # Таблица истории поисков
            await db.execute("""
                CREATE TABLE IF NOT EXISTS searches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    query TEXT NOT NULL,
                    price_min INTEGER NOT NULL,
                    price_max INTEGER NOT NULL,
                    tender_count INTEGER NOT NULL,
                    result_count INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    search_data TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)

            # Индексы для быстрого поиска
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_searches_user_id
                ON searches(user_id)
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_searches_timestamp
                ON searches(timestamp DESC)
            """)

            await db.commit()
            logger.info("✅ База данных инициализирована")

    async def add_or_update_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None
    ):
        """
        Добавление или обновление информации о пользователе.

        Args:
            user_id: ID пользователя в Telegram
            username: Username пользователя
            first_name: Имя пользователя
            last_name: Фамилия пользователя
        """
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now().isoformat()

            # Проверяем, существует ли пользователь
            async with db.execute(
                "SELECT user_id FROM users WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                exists = await cursor.fetchone()

            if exists:
                # Обновляем существующего пользователя
                await db.execute("""
                    UPDATE users
                    SET username = ?, first_name = ?, last_name = ?, last_activity = ?
                    WHERE user_id = ?
                """, (username, first_name, last_name, now, user_id))
            else:
                # Добавляем нового пользователя
                await db.execute("""
                    INSERT INTO users (user_id, username, first_name, last_name, created_at, last_activity)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (user_id, username, first_name, last_name, now, now))

            await db.commit()

    async def save_search(
        self,
        user_id: int,
        query: str,
        price_min: int,
        price_max: int,
        tender_count: int,
        result_count: int,
        search_data: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Сохранение результатов поиска.

        Args:
            user_id: ID пользователя
            query: Поисковый запрос
            price_min: Минимальная цена
            price_max: Максимальная цена
            tender_count: Запрошенное количество тендеров
            result_count: Фактически найденное количество
            search_data: Полные данные результатов (опционально)

        Returns:
            ID созданной записи
        """
        async with aiosqlite.connect(self.db_path) as db:
            timestamp = datetime.now().isoformat()

            # Сериализуем данные поиска, если есть
            # Добавляем обработчик для datetime объектов
            def datetime_handler(obj):
                if isinstance(obj, datetime):
                    return obj.isoformat()
                raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

            search_data_json = json.dumps(search_data, ensure_ascii=False, default=datetime_handler) if search_data else None

            cursor = await db.execute("""
                INSERT INTO searches
                (user_id, query, price_min, price_max, tender_count, result_count, timestamp, search_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, query, price_min, price_max, tender_count, result_count, timestamp, search_data_json))

            await db.commit()
            return cursor.lastrowid

    async def get_user_searches(
        self,
        user_id: int,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Получение истории поисков пользователя.

        Args:
            user_id: ID пользователя
            limit: Максимальное количество записей

        Returns:
            Список словарей с данными поисков
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute("""
                SELECT id, query, price_min, price_max, tender_count, result_count, timestamp
                FROM searches
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (user_id, limit)) as cursor:
                rows = await cursor.fetchall()

                return [
                    {
                        'id': row['id'],
                        'query': row['query'],
                        'price_min': row['price_min'],
                        'price_max': row['price_max'],
                        'tender_count': row['tender_count'],
                        'result_count': row['result_count'],
                        'timestamp': row['timestamp']
                    }
                    for row in rows
                ]

    async def get_search_by_id(self, search_id: int) -> Optional[Dict[str, Any]]:
        """
        Получение данных конкретного поиска по ID.

        Args:
            search_id: ID поиска

        Returns:
            Словарь с данными поиска или None
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute("""
                SELECT id, user_id, query, price_min, price_max,
                       tender_count, result_count, timestamp, search_data
                FROM searches
                WHERE id = ?
            """, (search_id,)) as cursor:
                row = await cursor.fetchone()

                if not row:
                    return None

                result = {
                    'id': row['id'],
                    'user_id': row['user_id'],
                    'query': row['query'],
                    'price_min': row['price_min'],
                    'price_max': row['price_max'],
                    'tender_count': row['tender_count'],
                    'result_count': row['result_count'],
                    'timestamp': row['timestamp']
                }

                # Десериализуем данные поиска, если есть
                if row['search_data']:
                    result['search_data'] = json.loads(row['search_data'])

                return result

    async def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        """
        Получение статистики пользователя.

        Args:
            user_id: ID пользователя

        Returns:
            Словарь со статистикой
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Общее количество поисков
            async with db.execute(
                "SELECT COUNT(*) as count FROM searches WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                total_searches = row[0]

            # Общее количество найденных тендеров
            async with db.execute(
                "SELECT SUM(result_count) as total FROM searches WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                total_tenders = row[0] or 0

            # Дата первого поиска
            async with db.execute(
                "SELECT MIN(timestamp) as first_search FROM searches WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                first_search = row[0]

            # Дата последнего поиска
            async with db.execute(
                "SELECT MAX(timestamp) as last_search FROM searches WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                last_search = row[0]

            return {
                'total_searches': total_searches,
                'total_tenders_found': total_tenders,
                'first_search': first_search,
                'last_search': last_search
            }

    async def delete_old_searches(self, days: int = 30) -> int:
        """
        Удаление старых поисков.

        Args:
            days: Удалить поиски старше указанного количества дней

        Returns:
            Количество удаленных записей
        """
        async with aiosqlite.connect(self.db_path) as db:
            from datetime import timedelta
            cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()

            cursor = await db.execute(
                "DELETE FROM searches WHERE timestamp < ?",
                (cutoff_date,)
            )

            await db.commit()
            return cursor.rowcount


# Глобальный экземпляр базы данных
_db_instance: Optional[Database] = None


async def get_database(db_path: Path = None) -> Database:
    """
    Получение глобального экземпляра базы данных.

    Args:
        db_path: Путь к файлу БД (используется только при первом вызове)

    Returns:
        Экземпляр Database
    """
    global _db_instance

    if _db_instance is None:
        if db_path is None:
            from bot.config import BotConfig
            db_path = BotConfig.DB_PATH

        _db_instance = Database(db_path)
        await _db_instance.init_db()

    return _db_instance
