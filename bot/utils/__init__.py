"""Bot utilities."""

from bot.utils.access_check import (
    check_feature_access,
    require_feature,
    get_user_tier,
    requires_tier,
    FEATURE_ACCESS,
    FEATURE_NAMES,
)


def safe_callback_data(prefix: str, *args, max_bytes: int = 64) -> str:
    """
    Создаёт callback_data, гарантируя что результат <= max_bytes (UTF-8).

    Telegram ограничивает callback_data до 64 байт.
    Кириллица = 2 байта/символ, латиница/цифры = 1 байт.
    """
    data = f"{prefix}_{'_'.join(str(a) for a in args)}"
    encoded = data.encode('utf-8')
    if len(encoded) <= max_bytes:
        return data
    # Обрезаем, сохраняя prefix
    while len(data.encode('utf-8')) > max_bytes:
        data = data[:-1]
    return data


__all__ = [
    'check_feature_access',
    'require_feature',
    'get_user_tier',
    'requires_tier',
    'FEATURE_ACCESS',
    'FEATURE_NAMES',
    'safe_callback_data',
]
