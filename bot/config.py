"""
Конфигурация Telegram бота.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Загружаем переменные окружения
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)


class BotConfig:
    """Конфигурация бота."""

    # Telegram Bot Token
    BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')

    # OpenAI API Key (используется из основной системы)
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')

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
            errors.append("TELEGRAM_BOT_TOKEN не задан в .env")

        if not cls.OPENAI_API_KEY:
            errors.append("OPENAI_API_KEY не задан в .env")

        if errors:
            raise ValueError("Ошибки конфигурации:\n" + "\n".join(f"  - {e}" for e in errors))

        return True


# Создаем директорию для БД, если её нет
BotConfig.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
