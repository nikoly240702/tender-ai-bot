"""
Конфигурация Telegram бота.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем переменные окружения из .env (только для локального запуска)
# В Railway переменные окружения уже установлены в системе
env_path = Path(__file__).parent.parent / '.env'
if env_path.exists():
    load_dotenv(env_path)


class BotConfig:
    """Конфигурация бота."""

    # Telegram Bot Token
    BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')

    # OpenAI API Key (используется из основной системы)
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')

    # Система приватности (белый список пользователей)
    # Формат: список Telegram User ID через запятую
    # Пример: ALLOWED_USERS=123456789,987654321
    # Если не задано - бот доступен всем
    ALLOWED_USERS_STR = os.getenv('ALLOWED_USERS', '')
    ALLOWED_USERS = set(int(uid.strip()) for uid in ALLOWED_USERS_STR.split(',') if uid.strip()) if ALLOWED_USERS_STR else None

    # Администратор бота (может управлять доступом)
    # Формат: единственный Telegram User ID
    # Пример: ADMIN_USER_ID=123456789
    ADMIN_USER_ID_STR = os.getenv('ADMIN_USER_ID', '')
    ADMIN_USER_ID = int(ADMIN_USER_ID_STR) if ADMIN_USER_ID_STR.strip() else None

    # Настройки базы данных
    DB_PATH = Path(__file__).parent / 'database' / 'bot.db'

    # Настройки поиска (значения по умолчанию)
    DEFAULT_MAX_TENDERS = 5
    DEFAULT_PRICE_MIN = 100000
    DEFAULT_PRICE_MAX = 10000000

    # Ограничения
    MAX_SEARCH_HISTORY = 10  # Максимальное количество сохраненных поисков

    # Предустановленные диапазоны цен
    PRICE_RANGES = {
        'до_500к': (0, 500000),
        '500к_1млн': (500000, 1000000),
        '1млн_3млн': (1000000, 3000000),
        '3млн_плюс': (3000000, 50000000)
    }

    # Лимиты анализа (количество тендеров для AI-анализа)
    MAX_ANALYSIS_PER_SEARCH = 5

    @classmethod
    def validate(cls):
        """Проверяет, что все необходимые настройки заданы."""
        errors = []

        if not cls.BOT_TOKEN:
            errors.append("TELEGRAM_BOT_TOKEN не задан")

        if not cls.OPENAI_API_KEY:
            errors.append("OPENAI_API_KEY не задан")

        if errors:
            raise ValueError("Ошибки конфигурации:\n" + "\n".join(f"  - {e}" for e in errors))

        return True


# Создаем директорию для БД, если её нет
BotConfig.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
