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
        error_str = str(exc_value).lower()
        error_type = exc_type.__name__ if exc_type else ''

        # Игнорируем KeyboardInterrupt
        if isinstance(exc_value, KeyboardInterrupt):
            return None

        # Некритичные сетевые ошибки Telegram (происходят периодически)
        non_critical_patterns = [
            'timeout',
            'request timeout',
            'timed out',
            'connection reset',
            'connection refused',
            'network is unreachable',
            'temporary failure',
            'retry',
            'flood',  # FloodWait от Telegram
            'too many requests',
            'bad gateway',
            'service unavailable',
            'getaddrinfo failed',
            'ssl: unexpected eof',
        ]

        # Проверяем текст ошибки
        if any(pattern in error_str for pattern in non_critical_patterns):
            logger.debug(f"Sentry: игнорируем некритичную ошибку: {error_str[:100]}")
            return None

        # Проверяем тип ошибки
        non_critical_types = [
            'TelegramNetworkError',
            'TimeoutError',
            'ConnectionError',
            'NetworkError',
            'RetryAfter',
            'FloodWait',
        ]
        if error_type in non_critical_types:
            logger.debug(f"Sentry: игнорируем {error_type}")
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
# TELEGRAM УВЕДОМЛЕНИЯ ОБ ОШИБКАХ
# ============================================

_telegram_error_bot = None
_admin_chat_id = None


def init_telegram_error_alerts(bot_token: str = None, admin_chat_id: int = None):
    """
    Инициализация Telegram уведомлений об ошибках.

    Args:
        bot_token: Токен бота (если None, берется из TELEGRAM_BOT_TOKEN)
        admin_chat_id: ID чата админа (если None, берется из ADMIN_TELEGRAM_ID)
    """
    global _telegram_error_bot, _admin_chat_id

    token = bot_token or os.getenv('TELEGRAM_BOT_TOKEN')
    _admin_chat_id = admin_chat_id or int(os.getenv('ADMIN_TELEGRAM_ID', '0'))

    if not token or not _admin_chat_id:
        logger.warning("Telegram error alerts не настроены (нет токена или admin_chat_id)")
        return False

    try:
        import httpx
        _telegram_error_bot = token
        logger.info(f"✅ Telegram error alerts настроены (admin: {_admin_chat_id})")
        return True
    except ImportError:
        logger.warning("httpx не установлен для Telegram alerts")
        return False


async def send_error_to_telegram(
    error: Exception,
    context: str = "",
    user_id: int = None,
    extra_info: Dict[str, Any] = None
):
    """
    Отправляет уведомление об ошибке в Telegram админу.

    Args:
        error: Исключение
        context: Контекст где произошла ошибка
        user_id: ID пользователя (если применимо)
        extra_info: Дополнительная информация
    """
    global _telegram_error_bot, _admin_chat_id

    if not _telegram_error_bot or not _admin_chat_id:
        return

    try:
        import httpx
        import traceback
        from datetime import datetime

        # Формируем сообщение
        error_type = type(error).__name__
        error_msg = str(error)[:500]  # Ограничиваем длину

        # Получаем краткий traceback
        tb_lines = traceback.format_exception(type(error), error, error.__traceback__)
        short_tb = ''.join(tb_lines[-3:])[:1000]  # Последние 3 строки

        message = f"""🚨 <b>ОШИБКА В БОТЕ</b>

<b>Тип:</b> <code>{error_type}</code>
<b>Время:</b> {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}
<b>Контекст:</b> {context or 'Не указан'}
"""

        if user_id:
            message += f"<b>User ID:</b> {user_id}\n"

        message += f"""
<b>Ошибка:</b>
<code>{error_msg}</code>

<b>Traceback:</b>
<pre>{short_tb}</pre>
"""

        if extra_info:
            info_str = '\n'.join(f"• {k}: {v}" for k, v in extra_info.items())
            message += f"\n<b>Доп. инфо:</b>\n{info_str}"

        # Отправляем
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{_telegram_error_bot}/sendMessage",
                json={
                    "chat_id": _admin_chat_id,
                    "text": message[:4000],  # Лимит Telegram
                    "parse_mode": "HTML",
                    "disable_notification": False
                },
                timeout=10
            )

        logger.info(f"📤 Ошибка отправлена в Telegram админу")

    except Exception as e:
        logger.error(f"❌ Не удалось отправить ошибку в Telegram: {e}")


async def send_ops_alert(title: str, lines: Dict[str, Any] = None, detail: str = ""):
    """Эксплуатационный алерт админу — не про исключение, а про состояние.

    send_error_to_telegram рассчитан на Exception с traceback. Здесь другой
    случай: ничего не упало, но система работает вхолостую (например, парсер
    вернул ноль тендеров несколько циклов подряд). Такие отказы тихие: в коде
    это валидный пустой результат, а не ошибка, и без явного алерта они
    остаются незамеченными часами.
    """
    global _telegram_error_bot, _admin_chat_id

    if not _telegram_error_bot or not _admin_chat_id:
        logger.warning(f"⚠️ Ops-алерт не отправлен (нет токена/admin_chat_id): {title}")
        return

    try:
        import httpx
        from datetime import datetime

        message = (f"⚠️ <b>{title}</b>\n\n"
                   f"<b>Время:</b> {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n")
        if lines:
            message += '\n'.join(f"• {k}: <b>{v}</b>" for k, v in lines.items()) + "\n"
        if detail:
            message += f"\n{detail}"

        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{_telegram_error_bot}/sendMessage",
                json={
                    "chat_id": _admin_chat_id,
                    "text": message[:4000],
                    "parse_mode": "HTML",
                    "disable_notification": False,
                },
                timeout=10,
            )
        logger.info(f"📤 Ops-алерт отправлен админу: {title}")
    except Exception as e:
        logger.error(f"❌ Не удалось отправить ops-алерт: {e}")


def send_error_to_telegram_sync(
    error: Exception,
    context: str = "",
    user_id: int = None,
    extra_info: Dict[str, Any] = None
):
    """
    Синхронная версия отправки ошибки в Telegram.
    """
    global _telegram_error_bot, _admin_chat_id

    if not _telegram_error_bot or not _admin_chat_id:
        return

    try:
        import httpx
        import traceback
        from datetime import datetime

        error_type = type(error).__name__
        error_msg = str(error)[:500]

        tb_lines = traceback.format_exception(type(error), error, error.__traceback__)
        short_tb = ''.join(tb_lines[-3:])[:1000]

        message = f"""🚨 <b>ОШИБКА В БОТЕ</b>

<b>Тип:</b> <code>{error_type}</code>
<b>Время:</b> {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}
<b>Контекст:</b> {context or 'Не указан'}
"""

        if user_id:
            message += f"<b>User ID:</b> {user_id}\n"

        message += f"""
<b>Ошибка:</b>
<code>{error_msg}</code>

<b>Traceback:</b>
<pre>{short_tb}</pre>
"""

        if extra_info:
            info_str = '\n'.join(f"• {k}: {v}" for k, v in extra_info.items())
            message += f"\n<b>Доп. инфо:</b>\n{info_str}"

        with httpx.Client() as client:
            client.post(
                f"https://api.telegram.org/bot{_telegram_error_bot}/sendMessage",
                json={
                    "chat_id": _admin_chat_id,
                    "text": message[:4000],
                    "parse_mode": "HTML",
                    "disable_notification": False
                },
                timeout=10
            )

    except Exception as e:
        logger.error(f"❌ Не удалось отправить ошибку в Telegram: {e}")


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
