"""
Модуль для работы с базой данных SQLite.
Хранит историю поисков пользователей.
"""

import aiosqlite
import json
import hashlib
from datetime import datetime, timedelta
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

            # Таблица кэшированных анализов тендеров (V2.0)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tender_analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tender_number TEXT UNIQUE NOT NULL,
                    documentation_hash TEXT NOT NULL,
                    analysis_result TEXT NOT NULL,
                    score INTEGER,
                    recommendation TEXT,
                    nmck REAL,
                    created_at TEXT NOT NULL,
                    ttl_days INTEGER DEFAULT 14,
                    expires_at TEXT NOT NULL
                )
            """)

            # Индексы для кэша анализов
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_tender_hash
                ON tender_analyses(documentation_hash)
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_tender_score
                ON tender_analyses(score DESC)
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_tender_expires
                ON tender_analyses(expires_at)
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
            cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()

            cursor = await db.execute(
                "DELETE FROM searches WHERE timestamp < ?",
                (cutoff_date,)
            )

            await db.commit()
            return cursor.rowcount

    # ============================================================
    # МЕТОДЫ ДЛЯ КЭШИРОВАНИЯ АНАЛИЗОВ ТЕНДЕРОВ (V2.0)
    # ============================================================

    @staticmethod
    def compute_documentation_hash(documentation: List[Dict[str, Any]]) -> str:
        """
        Вычисление MD5 хэша от документации тендера.

        Args:
            documentation: Список документов с полями filename, content

        Returns:
            MD5 хэш в виде строки
        """
        # Сортируем документы по имени для стабильного хэша
        sorted_docs = sorted(documentation, key=lambda d: d.get('filename', ''))

        # Создаем строку из имен файлов и их контента
        content_str = ""
        for doc in sorted_docs:
            filename = doc.get('filename', '')
            content = doc.get('content', '')[:10000]  # Берем первые 10K символов
            content_str += f"{filename}|{content}\n"

        # Вычисляем MD5
        return hashlib.md5(content_str.encode('utf-8')).hexdigest()

    async def get_cached_analysis(
        self,
        tender_number: str,
        doc_hash: str
    ) -> Optional[Dict[str, Any]]:
        """
        Получение закэшированного анализа тендера.

        Args:
            tender_number: Номер тендера (regNumber)
            doc_hash: MD5 хэш документации

        Returns:
            Словарь с результатами анализа или None если кэш невалиден
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            now = datetime.now().isoformat()

            async with db.execute("""
                SELECT id, tender_number, documentation_hash, analysis_result,
                       score, recommendation, nmck, created_at, expires_at
                FROM tender_analyses
                WHERE tender_number = ? AND expires_at > ?
            """, (tender_number, now)) as cursor:
                row = await cursor.fetchone()

                if not row:
                    logger.info(f"❌ CACHE MISS: {tender_number} (не найден или истек)")
                    return None

                # Проверяем хэш документации
                if row['documentation_hash'] != doc_hash:
                    logger.info(f"❌ CACHE MISS: {tender_number} (документация изменилась)")
                    # Удаляем устаревший кэш
                    await db.execute(
                        "DELETE FROM tender_analyses WHERE id = ?",
                        (row['id'],)
                    )
                    await db.commit()
                    return None

                # Кэш валиден!
                logger.info(f"✅ CACHE HIT: {tender_number} (score={row['score']}, "
                           f"expires={row['expires_at']})")

                result = {
                    'tender_number': row['tender_number'],
                    'analysis_result': json.loads(row['analysis_result']),
                    'score': row['score'],
                    'recommendation': row['recommendation'],
                    'nmck': row['nmck'],
                    'created_at': row['created_at'],
                    'expires_at': row['expires_at'],
                    'from_cache': True
                }

                return result

    async def save_analysis(
        self,
        tender_number: str,
        doc_hash: str,
        analysis_result: Dict[str, Any],
        score: Optional[int] = None,
        recommendation: Optional[str] = None,
        nmck: Optional[float] = None,
        ttl_days: int = 14
    ) -> int:
        """
        Сохранение результатов анализа в кэш.

        Args:
            tender_number: Номер тендера
            doc_hash: MD5 хэш документации
            analysis_result: Полные результаты анализа (будет сериализован в JSON)
            score: Итоговый балл (0-100)
            recommendation: Рекомендация (participate/consider/skip)
            nmck: Начальная максимальная цена контракта
            ttl_days: Время жизни кэша в днях (по умолчанию 14)

        Returns:
            ID созданной записи
        """
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now()
            created_at = now.isoformat()
            expires_at = (now + timedelta(days=ttl_days)).isoformat()

            # Сериализуем результаты анализа
            analysis_json = json.dumps(analysis_result, ensure_ascii=False)

            # UPSERT: обновляем если существует, иначе вставляем
            cursor = await db.execute("""
                INSERT INTO tender_analyses
                (tender_number, documentation_hash, analysis_result, score,
                 recommendation, nmck, created_at, ttl_days, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tender_number) DO UPDATE SET
                    documentation_hash = excluded.documentation_hash,
                    analysis_result = excluded.analysis_result,
                    score = excluded.score,
                    recommendation = excluded.recommendation,
                    nmck = excluded.nmck,
                    created_at = excluded.created_at,
                    ttl_days = excluded.ttl_days,
                    expires_at = excluded.expires_at
            """, (tender_number, doc_hash, analysis_json, score,
                  recommendation, nmck, created_at, ttl_days, expires_at))

            await db.commit()

            logger.info(f"💾 CACHE SAVED: {tender_number} (score={score}, TTL={ttl_days} days)")
            return cursor.lastrowid

    async def cleanup_expired_cache(self) -> int:
        """
        Очистка истекших записей кэша.

        Returns:
            Количество удаленных записей
        """
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now().isoformat()

            cursor = await db.execute(
                "DELETE FROM tender_analyses WHERE expires_at < ?",
                (now,)
            )

            await db.commit()
            count = cursor.rowcount

            if count > 0:
                logger.info(f"🗑️ Очищено {count} истекших записей кэша")

            return count

    async def get_cache_stats(self) -> Dict[str, Any]:
        """
        Получение статистики по кэшу анализов.

        Returns:
            Словарь со статистикой
        """
        async with aiosqlite.connect(self.db_path) as db:
            now = datetime.now().isoformat()

            # Всего записей в кэше
            async with db.execute(
                "SELECT COUNT(*) FROM tender_analyses"
            ) as cursor:
                row = await cursor.fetchone()
                total = row[0]

            # Валидных (не истекших) записей
            async with db.execute(
                "SELECT COUNT(*) FROM tender_analyses WHERE expires_at > ?",
                (now,)
            ) as cursor:
                row = await cursor.fetchone()
                valid = row[0]

            # Средний балл закэшированных анализов
            async with db.execute(
                "SELECT AVG(score) FROM tender_analyses WHERE expires_at > ? AND score IS NOT NULL",
                (now,)
            ) as cursor:
                row = await cursor.fetchone()
                avg_score = round(row[0], 1) if row[0] else 0

            # Распределение рекомендаций
            recommendations = {}
            async with db.execute(
                "SELECT recommendation, COUNT(*) as count FROM tender_analyses WHERE expires_at > ? GROUP BY recommendation",
                (now,)
            ) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    recommendations[row[0] or 'unknown'] = row[1]

            return {
                'total_cached': total,
                'valid_cached': valid,
                'expired_cached': total - valid,
                'average_score': avg_score,
                'recommendations': recommendations
            }


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
