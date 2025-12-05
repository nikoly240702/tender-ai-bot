"""
Environment Variables Validator.

Проверяет наличие и валидность всех необходимых переменных окружения перед запуском.
"""

import os
import sys
import logging
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class EnvValidator:
    """Валидатор переменных окружения."""

    # Обязательные переменные
    REQUIRED_VARS = {
        'BOT_TOKEN': 'Telegram Bot Token from @BotFather',
    }

    # Рекомендуемые переменные (warning если отсутствуют)
    RECOMMENDED_VARS = {
        'DATABASE_URL': 'PostgreSQL connection string (postgres://user:pass@host:port/db)',
        'SENTRY_DSN': 'Sentry DSN для error tracking',
        'ALLOWED_USERS': 'Comma-separated list of allowed Telegram user IDs'
    }

    # Опциональные переменные
    OPTIONAL_VARS = {
        'PROXY_URL': 'SOCKS5 proxy URL (socks5://user:pass@host:port)',
        'ANTHROPIC_API_KEY': 'API ключ Claude для AI features',
        'LOG_LEVEL': 'Logging level (DEBUG, INFO, WARNING, ERROR)',
        'HEALTH_CHECK_PORT': 'Port for health check endpoint (default: 8080)'
    }

    @staticmethod
    def validate_bot_token(token: str) -> Tuple[bool, Optional[str]]:
        """
        Валидация Telegram Bot Token.

        Returns:
            (is_valid, error_message)
        """
        if not token:
            return False, "BOT_TOKEN is empty"

        if ':' not in token:
            return False, "BOT_TOKEN has invalid format (should contain ':')"

        parts = token.split(':')
        if len(parts) != 2:
            return False, "BOT_TOKEN has invalid format"

        bot_id, hash_part = parts

        if not bot_id.isdigit():
            return False, "BOT_TOKEN bot ID part should be numeric"

        if len(hash_part) < 30:
            return False, "BOT_TOKEN hash part seems too short"

        return True, None

    @staticmethod
    def validate_database_url(url: str) -> Tuple[bool, Optional[str]]:
        """
        Валидация DATABASE_URL.

        Returns:
            (is_valid, error_message)
        """
        if not url:
            return True, None  # Optional, can use SQLite fallback

        try:
            parsed = urlparse(url)

            if parsed.scheme not in ['postgresql', 'postgres', 'sqlite']:
                return False, f"Unsupported database scheme: {parsed.scheme}"

            if parsed.scheme in ['postgresql', 'postgres']:
                if not parsed.hostname:
                    return False, "PostgreSQL URL missing hostname"

                if not parsed.username:
                    return False, "PostgreSQL URL missing username"

            return True, None

        except Exception as e:
            return False, f"Invalid DATABASE_URL format: {e}"

    @staticmethod
    def validate_sentry_dsn(dsn: str) -> Tuple[bool, Optional[str]]:
        """
        Валидация Sentry DSN.

        Returns:
            (is_valid, error_message)
        """
        if not dsn:
            return True, None  # Optional

        try:
            parsed = urlparse(dsn)

            if parsed.scheme not in ['http', 'https']:
                return False, "Sentry DSN should use http or https"

            if not parsed.hostname:
                return False, "Sentry DSN missing hostname"

            # Sentry DSN format: https://public_key@host/project_id
            if '@' not in dsn:
                return False, "Sentry DSN should contain @ separator"

            return True, None

        except Exception as e:
            return False, f"Invalid SENTRY_DSN format: {e}"

    @classmethod
    def validate_all(cls, strict: bool = False) -> Dict[str, any]:
        """
        Валидация всех переменных окружения.

        Args:
            strict: Если True, warnings тоже считаются ошибками

        Returns:
            Dict с результатами валидации:
            {
                'valid': bool,
                'errors': List[str],
                'warnings': List[str],
                'info': Dict[str, str]
            }
        """
        errors = []
        warnings = []
        info = {}

        # Проверяем обязательные переменные
        for var_name, description in cls.REQUIRED_VARS.items():
            value = os.getenv(var_name)

            if not value:
                errors.append(f"❌ Missing required: {var_name} - {description}")
            else:
                # Специальная валидация для BOT_TOKEN
                if var_name == 'BOT_TOKEN':
                    is_valid, error_msg = cls.validate_bot_token(value)
                    if not is_valid:
                        errors.append(f"❌ Invalid {var_name}: {error_msg}")
                    else:
                        info[var_name] = "✅ Valid"
                else:
                    info[var_name] = "✅ Present"

        # Проверяем рекомендуемые переменные
        for var_name, description in cls.RECOMMENDED_VARS.items():
            value = os.getenv(var_name)

            if not value:
                message = f"⚠️  Missing recommended: {var_name} - {description}"
                if strict:
                    errors.append(message)
                else:
                    warnings.append(message)
            else:
                # Специальная валидация
                if var_name == 'DATABASE_URL':
                    is_valid, error_msg = cls.validate_database_url(value)
                    if not is_valid:
                        errors.append(f"❌ Invalid {var_name}: {error_msg}")
                    else:
                        db_type = 'PostgreSQL' if 'postgres' in value else 'SQLite'
                        info[var_name] = f"✅ {db_type}"

                elif var_name == 'SENTRY_DSN':
                    is_valid, error_msg = cls.validate_sentry_dsn(value)
                    if not is_valid:
                        warnings.append(f"⚠️  Invalid {var_name}: {error_msg}")
                    else:
                        info[var_name] = "✅ Valid"

                else:
                    info[var_name] = "✅ Present"

        # Проверяем опциональные переменные
        for var_name, description in cls.OPTIONAL_VARS.items():
            value = os.getenv(var_name)
            if value:
                info[var_name] = "✅ Present"

        valid = len(errors) == 0

        return {
            'valid': valid,
            'errors': errors,
            'warnings': warnings,
            'info': info
        }

    @classmethod
    def validate_and_exit_if_invalid(cls, strict: bool = False):
        """
        Валидация с автоматическим выходом при ошибках.

        Args:
            strict: Если True, warnings тоже приводят к выходу
        """
        result = cls.validate_all(strict=strict)

        # Выводим информацию
        logger.info("=" * 60)
        logger.info("Environment Variables Validation")
        logger.info("=" * 60)

        for key, value in result['info'].items():
            logger.info(f"  {key}: {value}")

        # Выводим warnings
        if result['warnings']:
            logger.info("\nWarnings:")
            for warning in result['warnings']:
                logger.warning(f"  {warning}")

        # Выводим errors
        if result['errors']:
            logger.error("\nErrors:")
            for error in result['errors']:
                logger.error(f"  {error}")

        logger.info("=" * 60)

        # Выход при ошибках
        if not result['valid']:
            logger.error("❌ Environment validation failed! Fix errors above.")
            sys.exit(1)

        logger.info("✅ Environment validation passed!")


__all__ = ['EnvValidator']
