"""
Structured Logging для Production.

Использует JSON-форматированные логи для парсинга в ELK, DataDog, CloudWatch и т.д.
"""

import logging
import json
import sys
import os
from datetime import datetime
from typing import Any, Dict, Optional
from pathlib import Path


class StructuredFormatter(logging.Formatter):
    """
    JSON formatter для структурированных логов.

    Output format:
    {
        "timestamp": "2024-11-24T12:34:56.789Z",
        "level": "INFO",
        "logger": "bot.main",
        "message": "Bot started",
        "extra": {...}
    }
    """

    def format(self, record: logging.LogRecord) -> str:
        """Форматирование лога в JSON."""
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Добавляем exception info если есть
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Добавляем stack trace если есть
        if record.stack_info:
            log_data["stack_info"] = self.formatStack(record.stack_info)

        # Добавляем extra fields
        extra_fields = {}
        for key, value in record.__dict__.items():
            if key not in [
                'name', 'msg', 'args', 'created', 'filename', 'funcName',
                'levelname', 'levelno', 'lineno', 'module', 'msecs',
                'message', 'pathname', 'process', 'processName', 'relativeCreated',
                'thread', 'threadName', 'exc_info', 'exc_text', 'stack_info'
            ]:
                extra_fields[key] = value

        if extra_fields:
            log_data["extra"] = extra_fields

        # Добавляем source location
        log_data["source"] = {
            "file": record.pathname,
            "line": record.lineno,
            "function": record.funcName
        }

        return json.dumps(log_data, ensure_ascii=False, default=str)


class HumanReadableFormatter(logging.Formatter):
    """
    Human-readable formatter для локальной разработки.

    Output format:
    2024-11-24 12:34:56 INFO bot.main: Bot started
    """

    def __init__(self):
        super().__init__(
            fmt='%(asctime)s %(levelname)-8s %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )


def setup_logging(
    level: str = "INFO",
    use_json: bool = True,
    log_file: Optional[Path] = None
) -> None:
    """
    Настройка structured logging для всего приложения.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
        use_json: Использовать JSON формат (True для production)
        log_file: Путь к файлу логов (опционально)

    Example:
        # Production
        setup_logging(level="INFO", use_json=True)

        # Development
        setup_logging(level="DEBUG", use_json=False, log_file=Path("bot.log"))
    """
    # Определяем formatter
    if use_json:
        formatter = StructuredFormatter()
    else:
        formatter = HumanReadableFormatter()

    # Настраиваем root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # Удаляем существующие handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler (если указан)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Настраиваем уровни для сторонних библиотек (уменьшаем verbosity)
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.getLogger("alembic").setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """
    Получить logger с дополнительными методами для structured logging.

    Args:
        name: Имя logger'а (обычно __name__)

    Returns:
        logging.Logger instance

    Example:
        logger = get_logger(__name__)
        logger.info("User action", extra={"user_id": 123, "action": "search"})
    """
    return logging.getLogger(name)


class LoggerAdapter(logging.LoggerAdapter):
    """
    Adapter для автоматического добавления context в логи.

    Example:
        adapter = LoggerAdapter(logger, {"user_id": 123, "session_id": "abc"})
        adapter.info("Action performed")
        # Output: {..., "message": "Action performed", "extra": {"user_id": 123, "session_id": "abc"}}
    """

    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple:
        """Добавление context в extra fields."""
        extra = kwargs.get("extra", {})
        extra.update(self.extra)
        kwargs["extra"] = extra
        return msg, kwargs


# Автоматическая настройка при импорте
def auto_setup_logging():
    """
    Автоматическая настройка логирования на основе переменных окружения.

    Environment variables:
        LOG_LEVEL: DEBUG, INFO, WARNING, ERROR (default: INFO)
        LOG_FORMAT: json или human (default: json в production, human в development)
        LOG_FILE: Путь к файлу логов (опционально)
    """
    log_level = os.getenv("LOG_LEVEL", "INFO")
    log_format = os.getenv("LOG_FORMAT", "json")
    log_file_path = os.getenv("LOG_FILE")

    use_json = log_format.lower() == "json"
    log_file = Path(log_file_path) if log_file_path else None

    setup_logging(level=log_level, use_json=use_json, log_file=log_file)


__all__ = [
    'setup_logging',
    'get_logger',
    'LoggerAdapter',
    'StructuredFormatter',
    'HumanReadableFormatter',
    'auto_setup_logging'
]
