"""
Модуль мониторинга и error tracking с использованием Sentry.

Интеграция Sentry для отслеживания ошибок, производительности и логов.
"""

import os
import logging
from typing import Optional, Dict, Any
from functools import wraps

logger = logging.getLogger(__name__)

# Глобальная переменная для проверки инициализации
_sentry_initialized = False


def init_sentry(
    dsn: Optional[str] = None,
    environment: str = "production",
    traces_sample_rate: float = 0.1,
    profiles_sample_rate: float = 0.1
) -> bool:
    """
    Инициализация Sentry для мониторинга ошибок.

    Args:
        dsn: Sentry DSN (если None, берется из переменной окружения)
        environment: Окружение (production/staging/development)
        traces_sample_rate: Доля трассировки запросов (0.0-1.0)
        profiles_sample_rate: Доля профилирования запросов (0.0-1.0)

    Returns:
        True если успешно инициализирован, False иначе
    """
    global _sentry_initialized

    if _sentry_initialized:
        logger.warning("Sentry уже инициализирован")
        return True

    # Получаем DSN из параметра или переменной окружения
    sentry_dsn = dsn or os.getenv('SENTRY_DSN')

    if not sentry_dsn:
        logger.warning("Sentry DSN не указан - мониторинг отключен")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.aiohttp import AioHttpIntegration

        # Настройка интеграции логирования
        logging_integration = LoggingIntegration(
            level=logging.INFO,        # Capture info и выше
            event_level=logging.ERROR  # Отправлять события для error и выше
        )

        sentry_sdk.init(
            dsn=sentry_dsn,
            environment=environment,
            traces_sample_rate=traces_sample_rate,
            profiles_sample_rate=profiles_sample_rate,
            integrations=[
                logging_integration,
                AioHttpIntegration(),
            ],
            # Дополнительные настройки
            attach_stacktrace=True,
            send_default_pii=False,  # Не отправлять персональные данные
            max_breadcrumbs=50,
            before_send=_before_send_filter,
        )

        _sentry_initialized = True
        logger.info(f"✅ Sentry инициализирован (environment={environment})")
        return True

    except ImportError:
        logger.error("❌ Sentry SDK не установлен. Установите: pip install sentry-sdk")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации Sentry: {e}")
        return False


def _before_send_filter(event: Dict[str, Any], hint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Фильтр событий перед отправкой в Sentry.

    Позволяет:
    - Удалять чувствительные данные
    - Игнорировать определенные типы ошибок
    - Добавлять дополнительный контекст

    Args:
        event: Событие Sentry
        hint: Дополнительная информация

    Returns:
        Модифицированное событие или None (чтобы не отправлять)
    """
    # Игнорируем некоторые типы ошибок
    if 'exc_info' in hint:
        exc_type, exc_value, tb = hint['exc_info']

        # Игнорируем KeyboardInterrupt
        if isinstance(exc_value, KeyboardInterrupt):
            return None

        # Игнорируем таймауты Telegram (не критично)
        if 'Timeout' in str(exc_value):
            return None

    # Удаляем чувствительные данные из breadcrumbs
    if 'breadcrumbs' in event:
        for breadcrumb in event['breadcrumbs']:
            if 'data' in breadcrumb:
                # Удаляем токены, пароли и т.д.
                for key in list(breadcrumb['data'].keys()):
                    if any(sensitive in key.lower() for sensitive in ['token', 'password', 'secret', 'key']):
                        breadcrumb['data'][key] = '[FILTERED]'

    return event


def capture_exception(
    error: Exception,
    level: str = "error",
    extra: Optional[Dict[str, Any]] = None,
    tags: Optional[Dict[str, str]] = None
) -> Optional[str]:
    """
    Отправка исключения в Sentry с дополнительным контекстом.

    Args:
        error: Исключение
        level: Уровень важности (error/warning/info)
        extra: Дополнительные данные
        tags: Теги для фильтрации

    Returns:
        Event ID от Sentry или None
    """
    if not _sentry_initialized:
        logger.error(f"Sentry не инициализирован: {error}")
        return None

    try:
        import sentry_sdk

        # Добавляем контекст
        if extra:
            sentry_sdk.set_context("extra_data", extra)

        if tags:
            for key, value in tags.items():
                sentry_sdk.set_tag(key, value)

        # Отправляем исключение
        event_id = sentry_sdk.capture_exception(error, level=level)
        logger.info(f"📤 Отправлено в Sentry: {event_id}")
        return event_id

    except Exception as e:
        logger.error(f"❌ Ошибка отправки в Sentry: {e}")
        return None


def capture_message(
    message: str,
    level: str = "info",
    extra: Optional[Dict[str, Any]] = None,
    tags: Optional[Dict[str, str]] = None
) -> Optional[str]:
    """
    Отправка сообщения в Sentry.

    Args:
        message: Текст сообщения
        level: Уровень важности
        extra: Дополнительные данные
        tags: Теги

    Returns:
        Event ID от Sentry или None
    """
    if not _sentry_initialized:
        return None

    try:
        import sentry_sdk

        if extra:
            sentry_sdk.set_context("extra_data", extra)

        if tags:
            for key, value in tags.items():
                sentry_sdk.set_tag(key, value)

        event_id = sentry_sdk.capture_message(message, level=level)
        return event_id

    except Exception as e:
        logger.error(f"❌ Ошибка отправки сообщения в Sentry: {e}")
        return None


def set_user_context(user_id: int, username: Optional[str] = None, **kwargs):
    """
    Установка контекста пользователя для Sentry.

    Args:
        user_id: ID пользователя
        username: Имя пользователя
        **kwargs: Дополнительные поля
    """
    if not _sentry_initialized:
        return

    try:
        import sentry_sdk

        user_data = {
            "id": str(user_id),
            **kwargs
        }

        if username:
            user_data["username"] = username

        sentry_sdk.set_user(user_data)

    except Exception as e:
        logger.error(f"❌ Ошибка установки user context: {e}")


def clear_user_context():
    """Очистка контекста пользователя."""
    if not _sentry_initialized:
        return

    try:
        import sentry_sdk
        sentry_sdk.set_user(None)
    except Exception as e:
        logger.error(f"❌ Ошибка очистки user context: {e}")


def add_breadcrumb(
    message: str,
    category: str = "default",
    level: str = "info",
    data: Optional[Dict[str, Any]] = None
):
    """
    Добавление breadcrumb для отслеживания последовательности действий.

    Args:
        message: Сообщение
        category: Категория (auth, http, db, ui и т.д.)
        level: Уровень важности
        data: Дополнительные данные
    """
    if not _sentry_initialized:
        return

    try:
        import sentry_sdk

        sentry_sdk.add_breadcrumb(
            message=message,
            category=category,
            level=level,
            data=data or {}
        )

    except Exception as e:
        logger.error(f"❌ Ошибка добавления breadcrumb: {e}")


def monitor_performance(operation_name: str):
    """
    Декоратор для мониторинга производительности функций.

    Usage:
        @monitor_performance("search_tenders")
        async def search_tenders(...):
            ...
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            if not _sentry_initialized:
                return await func(*args, **kwargs)

            try:
                import sentry_sdk

                with sentry_sdk.start_transaction(op=operation_name, name=func.__name__):
                    return await func(*args, **kwargs)

            except Exception as e:
                capture_exception(
                    e,
                    extra={
                        "function": func.__name__,
                        "operation": operation_name
                    }
                )
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            if not _sentry_initialized:
                return func(*args, **kwargs)

            try:
                import sentry_sdk

                with sentry_sdk.start_transaction(op=operation_name, name=func.__name__):
                    return func(*args, **kwargs)

            except Exception as e:
                capture_exception(
                    e,
                    extra={
                        "function": func.__name__,
                        "operation": operation_name
                    }
                )
                raise

        # Определяем, асинхронная функция или нет
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


def flush_events(timeout: int = 2):
    """
    Принудительная отправка всех накопленных событий в Sentry.

    Полезно перед завершением программы.

    Args:
        timeout: Таймаут в секундах
    """
    if not _sentry_initialized:
        return

    try:
        import sentry_sdk

        logger.info("📤 Отправка накопленных событий в Sentry...")
        sentry_sdk.flush(timeout=timeout)
        logger.info("✅ События отправлены")

    except Exception as e:
        logger.error(f"❌ Ошибка отправки событий: {e}")


# ============================================
# ПРИМЕР ИСПОЛЬЗОВАНИЯ
# ============================================

if __name__ == '__main__':
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Инициализация (DSN нужно получить из Sentry.io)
    # init_sentry(
    #     dsn="https://your-dsn@sentry.io/project-id",
    #     environment="development"
    # )

    print("✅ Модуль мониторинга загружен")
    print("ℹ️  Для использования установите: pip install sentry-sdk")
    print("ℹ️  Получите DSN на: https://sentry.io/")
