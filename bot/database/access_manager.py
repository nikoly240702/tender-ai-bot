"""
Модуль для управления доступом пользователей к боту.
"""

import sqlite3
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import logging

from bot.config import BotConfig

logger = logging.getLogger(__name__)


class AccessManager:
    """Управляет доступом пользователей к боту через базу данных."""

    def __init__(self):
        """Инициализация менеджера доступа."""
        self.db_path = BotConfig.DB_PATH
        self._init_database()

    def _init_database(self):
        """Создает таблицу для хранения пользователей, если её нет."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS allowed_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                added_at TEXT NOT NULL,
                added_by INTEGER,
                notes TEXT
            )
        """)

        conn.commit()
        conn.close()

        logger.info("✅ База данных пользователей инициализирована")

    def add_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        added_by: Optional[int] = None,
        notes: Optional[str] = None
    ) -> bool:
        """
        Добавляет пользователя в белый список.

        Args:
            user_id: Telegram User ID
            username: Username пользователя (без @)
            first_name: Имя пользователя
            last_name: Фамилия пользователя
            added_by: User ID админа, который добавил
            notes: Заметки о пользователе

        Returns:
            True если добавлен успешно, False если уже существует
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Проверяем, не существует ли уже
            cursor.execute("SELECT user_id FROM allowed_users WHERE user_id = ?", (user_id,))
            if cursor.fetchone():
                conn.close()
                return False

            # Добавляем пользователя
            cursor.execute("""
                INSERT INTO allowed_users
                (user_id, username, first_name, last_name, added_at, added_by, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                username,
                first_name,
                last_name,
                datetime.now().isoformat(),
                added_by,
                notes
            ))

            conn.commit()
            conn.close()

            logger.info(f"✅ Пользователь {user_id} (@{username}) добавлен в белый список")
            return True

        except Exception as e:
            logger.error(f"❌ Ошибка добавления пользователя {user_id}: {e}")
            return False

    def remove_user(self, user_id: int) -> bool:
        """
        Удаляет пользователя из белого списка.

        Args:
            user_id: Telegram User ID

        Returns:
            True если удален успешно, False если не найден
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("DELETE FROM allowed_users WHERE user_id = ?", (user_id,))
            deleted = cursor.rowcount > 0

            conn.commit()
            conn.close()

            if deleted:
                logger.info(f"✅ Пользователь {user_id} удален из белого списка")
            else:
                logger.warning(f"⚠️ Пользователь {user_id} не найден в белом списке")

            return deleted

        except Exception as e:
            logger.error(f"❌ Ошибка удаления пользователя {user_id}: {e}")
            return False

    def is_user_allowed(self, user_id: int) -> bool:
        """
        Проверяет, есть ли пользователь в белом списке.

        Args:
            user_id: Telegram User ID

        Returns:
            True если доступ разрешен
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("SELECT user_id FROM allowed_users WHERE user_id = ?", (user_id,))
            result = cursor.fetchone() is not None

            conn.close()
            return result

        except Exception as e:
            logger.error(f"❌ Ошибка проверки пользователя {user_id}: {e}")
            return False

    def get_all_users(self) -> List[Dict]:
        """
        Получает список всех пользователей с доступом.

        Returns:
            Список словарей с информацией о пользователях
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT user_id, username, first_name, last_name, added_at, added_by, notes
                FROM allowed_users
                ORDER BY added_at DESC
            """)

            users = [dict(row) for row in cursor.fetchall()]

            conn.close()
            return users

        except Exception as e:
            logger.error(f"❌ Ошибка получения списка пользователей: {e}")
            return []

    def get_user_info(self, user_id: int) -> Optional[Dict]:
        """
        Получает информацию о конкретном пользователе.

        Args:
            user_id: Telegram User ID

        Returns:
            Словарь с информацией или None если не найден
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT user_id, username, first_name, last_name, added_at, added_by, notes
                FROM allowed_users
                WHERE user_id = ?
            """, (user_id,))

            row = cursor.fetchone()
            user_info = dict(row) if row else None

            conn.close()
            return user_info

        except Exception as e:
            logger.error(f"❌ Ошибка получения информации о пользователе {user_id}: {e}")
            return None

    def update_user_info(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None
    ) -> bool:
        """
        Обновляет информацию о пользователе.

        Args:
            user_id: Telegram User ID
            username: Новый username
            first_name: Новое имя
            last_name: Новая фамилия

        Returns:
            True если обновлено успешно
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            updates = []
            params = []

            if username is not None:
                updates.append("username = ?")
                params.append(username)

            if first_name is not None:
                updates.append("first_name = ?")
                params.append(first_name)

            if last_name is not None:
                updates.append("last_name = ?")
                params.append(last_name)

            if not updates:
                conn.close()
                return False

            params.append(user_id)
            query = f"UPDATE allowed_users SET {', '.join(updates)} WHERE user_id = ?"

            cursor.execute(query, params)
            updated = cursor.rowcount > 0

            conn.commit()
            conn.close()

            return updated

        except Exception as e:
            logger.error(f"❌ Ошибка обновления информации о пользователе {user_id}: {e}")
            return False

    def sync_from_env(self):
        """
        Синхронизирует пользователей из переменной окружения ALLOWED_USERS в базу данных.
        Используется при первом запуске для миграции.
        """
        if BotConfig.ALLOWED_USERS:
            for user_id in BotConfig.ALLOWED_USERS:
                if not self.is_user_allowed(user_id):
                    self.add_user(
                        user_id=user_id,
                        notes="Импортировано из ALLOWED_USERS"
                    )
                    logger.info(f"Синхронизирован пользователь {user_id} из переменной окружения")

    def get_users_count(self) -> int:
        """
        Получает количество пользователей с доступом.

        Returns:
            Количество пользователей
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM allowed_users")
            count = cursor.fetchone()[0]

            conn.close()
            return count

        except Exception as e:
            logger.error(f"❌ Ошибка получения количества пользователей: {e}")
            return 0
