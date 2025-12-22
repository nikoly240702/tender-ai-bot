"""
Retry Logic with Exponential Backoff.

Декоратор для повторных попыток при таймаутах и сетевых ошибках.
Используется для RSS запросов к zakupki.gov.ru.

Feature flag: rss_retry (config/features.yaml)
"""

import asyncio
import logging
from typing import TypeVar, Callable, Tuple
from functools import wraps

logger = logging.getLogger(__name__)

T = TypeVar('T')


def retry_with_backoff(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: Tuple = (Exception,),
    on_retry: Callable = None
):
    """
    Retry decorator with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay between retries (seconds)
        backoff_factor: Multiplier for delay after each retry
        exceptions: Tuple of exception types to catch
        on_retry: Optional callback function(attempt, exception, delay) called before retry

    Example:
        @retry_with_backoff(max_attempts=3, initial_delay=2, backoff_factor=2)
        async def fetch_rss():
            ...

    Delays:
        Attempt 1: 0s (immediate)
        Attempt 2: 1s (initial_delay)
        Attempt 3: 2s (initial_delay * backoff_factor)
        Attempt 4: 4s (initial_delay * backoff_factor^2)
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            delay = initial_delay
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt == max_attempts:
                        logger.error(
                            f"❌ {func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise

                    logger.warning(
                        f"⚠️ {func.__name__} attempt {attempt}/{max_attempts} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )

                    # Call on_retry callback if provided
                    if on_retry:
                        try:
                            on_retry(attempt, e, delay)
                        except Exception as callback_error:
                            logger.warning(f"on_retry callback failed: {callback_error}")

                    await asyncio.sleep(delay)
                    delay *= backoff_factor

            # Should never reach here
            raise last_exception

        return wrapper
    return decorator


class RetryConfig:
    """Configuration for retry behavior."""

    # Default settings
    DEFAULT_MAX_ATTEMPTS = 3
    DEFAULT_INITIAL_DELAY = 2.0
    DEFAULT_BACKOFF_FACTOR = 2.0

    # RSS-specific settings
    RSS_MAX_ATTEMPTS = 3
    RSS_INITIAL_DELAY = 2.0
    RSS_BACKOFF_FACTOR = 2.0
    RSS_EXCEPTIONS = (
        asyncio.TimeoutError,
        ConnectionError,
        OSError,
    )

    @classmethod
    def get_rss_retry_decorator(cls):
        """Get pre-configured retry decorator for RSS requests."""
        return retry_with_backoff(
            max_attempts=cls.RSS_MAX_ATTEMPTS,
            initial_delay=cls.RSS_INITIAL_DELAY,
            backoff_factor=cls.RSS_BACKOFF_FACTOR,
            exceptions=cls.RSS_EXCEPTIONS
        )


# Pre-configured decorators
rss_retry = RetryConfig.get_rss_retry_decorator()


# Convenience function for manual retry
async def retry_async(
    func: Callable,
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: Tuple = (Exception,)
) -> T:
    """
    Retry an async function with exponential backoff.

    Usage:
        result = await retry_async(
            lambda: fetch_data(url),
            max_attempts=3,
            exceptions=(TimeoutError, ConnectionError)
        )
    """
    delay = initial_delay
    last_exception = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await func()
        except exceptions as e:
            last_exception = e

            if attempt == max_attempts:
                logger.error(f"❌ Function failed after {max_attempts} attempts: {e}")
                raise

            logger.warning(
                f"⚠️ Attempt {attempt}/{max_attempts} failed: {e}. "
                f"Retrying in {delay:.1f}s..."
            )

            await asyncio.sleep(delay)
            delay *= backoff_factor

    raise last_exception
