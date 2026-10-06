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


# Публичный адрес веб-кабинета. Домен должен совпадать с тем, что
# прописан боту в @BotFather: виджет входа Telegram авторизует только
# на нём, на любом другом кнопка входа молча не срабатывает.
CABINET_URL_DEFAULT = 'https://cabinet.tendersniper.ru'

# Реальное имя бота. BOT_USERNAME в окружении никогда не задавался (прод
# проверен 06.10.2026), поэтому три места в коде — реферальные ссылки,
# возврат после оплаты YooKassa, страница логина кабинета — тихо
# откатывались на зашитый дефолт 'TenderSniperBot', бота с таким именем
# не существует.
BOT_USERNAME_DEFAULT = 'TenderAI111_bot'


def cabinet_login_url() -> str:
    """Ссылка на страницу входа в кабинет для кнопок бота."""
    base = (os.getenv('CABINET_URL') or CABINET_URL_DEFAULT).rstrip('/')
    return f"{base}/cabinet/login"


def bot_username() -> str:
    """Имя бота для ссылок t.me/... — реферальных, возврата после оплаты."""
    return os.getenv('BOT_USERNAME') or BOT_USERNAME_DEFAULT


def is_admin_telegram_id(telegram_id) -> bool:
    """Владелец сервиса — для фич, временно скрытых от обычных пользователей
    (Tender-GPT, AI-анализ карточки — обе ненадёжны на 06.10.2026, см.
    tender_sniper/tender_gpt/service.py и cabinet/pipeline_service.py).

    Почти дублирует cabinet/auth.py::is_admin_telegram_id — своя копия
    здесь, потому что bot/ и tender_sniper/ не зависят от cabinet/, а эта
    проверка нужна именно оттуда (TenderGPTService обслуживает и бота, и
    кабинет, и MAX).
    """
    raw = os.getenv('ADMIN_USER_ID') or os.getenv('ADMIN_TELEGRAM_ID') or ''
    try:
        return bool(raw.strip()) and int(raw) == int(telegram_id)
    except (TypeError, ValueError):
        return False


class BotConfig:
    """Конфигурация бота."""

    # Telegram Bot Token
    BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')

    # OpenAI API Key (используется из основной системы)
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')

    # Администратор бота (может блокировать пользователей и управлять тарифами)
    # Формат: единственный Telegram User ID
    # Пример: ADMIN_USER_ID=123456789
    ADMIN_USER_ID_STR = os.getenv('ADMIN_USER_ID', '')
    ADMIN_USER_ID = int(ADMIN_USER_ID_STR) if ADMIN_USER_ID_STR.strip() else None

    # ОТКРЫТЫЙ ДОСТУП: все пользователи могут использовать бота
    # Админ может блокировать отдельных пользователей через /block команду
    # и управлять тарифами через /set_tier команду

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

    # Telegram Mini App (WebApp) URL
    WEBAPP_BASE_URL = os.getenv('WEBAPP_BASE_URL', 'https://tender-ai-bot-production.up.railway.app')

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
