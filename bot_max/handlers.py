"""
Handlers for VK Max bot updates.

Supports: bot_started, message_created, message_callback.
"""

import logging
from typing import Dict, Any

from bot_max.client import MaxBotClient
from tender_sniper.database import get_sniper_db

logger = logging.getLogger(__name__)

# ── Tender-GPT integration ──────────────────────────────────────────

_gpt_service = None
_gpt_active_chats: set = set()  # chat_ids currently in GPT mode


async def _get_gpt_service():
    global _gpt_service
    if _gpt_service is None:
        from tender_sniper.tender_gpt.service import TenderGPTService
        _gpt_service = TenderGPTService()
    return _gpt_service


# ── Keyboard layouts ────────────────────────────────────────────────

MAIN_MENU_KEYBOARD = [
    [{"type": "callback", "text": "Tender-GPT", "payload": "tender_gpt_start"}],
    [{"type": "callback", "text": "Мои фильтры", "payload": "my_filters"}],
    [{"type": "callback", "text": "Помощь", "payload": "help"}],
]

BACK_TO_MENU_KEYBOARD = [
    [{"type": "callback", "text": "Главное меню", "payload": "main_menu"}],
]

EXIT_GPT_KEYBOARD = [
    [{"type": "callback", "text": "Выйти из чата", "payload": "main_menu"}],
]

# ── Texts ───────────────────────────────────────────────────────────

WELCOME_TEXT = (
    "<b>Tender Sniper</b>\n\n"
    "Бот для мониторинга и анализа госзакупок.\n"
    "Выберите действие:"
)

HELP_TEXT = (
    "<b>Помощь</b>\n\n"
    "<b>Tender-GPT</b> — AI-ассистент по госзакупкам. "
    "Найдёт тендеры, проанализирует документацию, ответит на вопросы по 44-ФЗ и 223-ФЗ.\n\n"
    "<b>Мои фильтры</b> — просмотр настроенных фильтров мониторинга.\n\n"
    "Для полного функционала используйте Telegram-бота @TenderAI111_bot."
)


# ── Ensure user exists in DB ────────────────────────────────────────

async def _ensure_user(user_id: int, username: str = None) -> dict:
    """Create or update user in DB, return user dict."""
    db = await get_sniper_db()
    # Use the same telegram_id field; prefix username to identify Max users
    max_username = f"max_{username}" if username else f"max_{user_id}"
    await db.create_or_update_user(
        telegram_id=user_id,
        username=max_username,
        subscription_tier='trial',
    )
    return await db.get_user_by_telegram_id(user_id)


# ── Update dispatcher ───────────────────────────────────────────────

async def dispatch_update(client: MaxBotClient, update: Dict[str, Any]):
    """Route a single update to the appropriate handler."""
    update_type = update.get("update_type")

    try:
        if update_type == "bot_started":
            user = update.get("user", {})
            chat_id = update.get("chat_id")
            user_id = user.get("user_id")
            username = user.get("username")
            if chat_id and user_id:
                await handle_bot_started(client, user_id, chat_id, username)

        elif update_type == "message_created":
            await handle_message(client, update)

        elif update_type == "message_callback":
            await handle_callback(client, update)

        else:
            logger.debug(f"Max bot: unhandled update_type={update_type}")

    except Exception as e:
        logger.error(f"Max bot handler error ({update_type}): {e}", exc_info=True)


# ── Handlers ────────────────────────────────────────────────────────

async def handle_bot_started(
    client: MaxBotClient,
    user_id: int,
    chat_id: int,
    username: str = None,
):
    """User pressed 'Start' — send welcome message."""
    logger.info(f"Max bot: bot_started from user {user_id} in chat {chat_id}")
    await _ensure_user(user_id, username)
    # Exit GPT mode if it was active
    _gpt_active_chats.discard(chat_id)
    await client.send_message(chat_id, WELCOME_TEXT, keyboard=MAIN_MENU_KEYBOARD)


async def handle_message(client: MaxBotClient, update: Dict[str, Any]):
    """Handle an incoming text message."""
    message = update.get("message", {})
    body = message.get("body", {})
    text = body.get("text", "").strip()
    sender = message.get("sender", {})
    user_id = sender.get("user_id")
    username = sender.get("username")
    chat_id = message.get("recipient", {}).get("chat_id")

    if not text or not chat_id or not user_id:
        return

    logger.info(f"Max bot: message from user {user_id}: {text[:80]}")

    # GPT mode
    if chat_id in _gpt_active_chats:
        await _handle_gpt_message(client, chat_id, user_id, username, text)
        return

    # Quick entry to GPT via text
    if text.lower() in ("tender-gpt", "tendergpt", "gpt"):
        _gpt_active_chats.add(chat_id)
        service = await _get_gpt_service()
        greeting = await service.get_greeting()
        await client.send_message(chat_id, greeting, keyboard=EXIT_GPT_KEYBOARD)
        logger.info(f"Max bot: user {user_id} entered GPT mode")
        return

    # Default: show main menu
    await client.send_message(chat_id, WELCOME_TEXT, keyboard=MAIN_MENU_KEYBOARD)


async def handle_callback(client: MaxBotClient, update: Dict[str, Any]):
    """Handle inline keyboard button press."""
    callback = update.get("callback", {})
    callback_id = callback.get("callback_id")
    payload = callback.get("payload")
    user = callback.get("user", {})
    user_id = user.get("user_id")
    username = user.get("username")
    message = update.get("message", {})
    chat_id = message.get("recipient", {}).get("chat_id")

    if not callback_id or not chat_id:
        return

    logger.info(f"Max bot: callback '{payload}' from user {user_id}")

    # Acknowledge callback
    try:
        await client.answer_callback(callback_id)
    except Exception as e:
        logger.warning(f"Max bot: failed to answer callback: {e}")

    if payload == "tender_gpt_start":
        _gpt_active_chats.add(chat_id)
        service = await _get_gpt_service()
        greeting = await service.get_greeting()
        await client.send_message(chat_id, greeting, keyboard=EXIT_GPT_KEYBOARD)
        logger.info(f"Max bot: user {user_id} entered GPT mode (callback)")

    elif payload == "main_menu":
        _gpt_active_chats.discard(chat_id)
        await client.send_message(chat_id, WELCOME_TEXT, keyboard=MAIN_MENU_KEYBOARD)

    elif payload == "my_filters":
        await _show_filters(client, chat_id, user_id, username)

    elif payload == "help":
        await client.send_message(chat_id, HELP_TEXT, keyboard=BACK_TO_MENU_KEYBOARD)

    else:
        logger.debug(f"Max bot: unknown callback payload: {payload}")


# ── GPT chat ────────────────────────────────────────────────────────

async def _handle_gpt_message(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
    text: str,
):
    """Forward message to Tender-GPT and send response."""
    service = await _get_gpt_service()
    user = await _ensure_user(user_id, username)

    if not user:
        await client.send_message(
            chat_id,
            "Пользователь не найден. Нажмите Start для регистрации.",
            keyboard=BACK_TO_MENU_KEYBOARD,
        )
        _gpt_active_chats.discard(chat_id)
        return

    try:
        result = await service.chat(
            telegram_id=user_id,
            user_id=user['id'],
            user_message=text,
        )

        response_text = result['response']
        quota = result.get('quota', {})

        # Quota warning
        remaining = quota.get('remaining', 0)
        limit = quota.get('limit', 0)
        if limit < 999999 and 0 < remaining <= 5:
            response_text += f"\n\n<i>Осталось сообщений: {remaining}/{limit}</i>"

        await client.send_message(chat_id, response_text, keyboard=EXIT_GPT_KEYBOARD)

        # Quota exhausted — exit GPT mode
        if not quota.get('allowed', True) and result.get('session_id') is None:
            _gpt_active_chats.discard(chat_id)

    except Exception as e:
        logger.error(f"Max bot GPT error for user {user_id}: {e}", exc_info=True)
        await client.send_message(
            chat_id,
            "Произошла ошибка. Попробуйте ещё раз.",
            keyboard=EXIT_GPT_KEYBOARD,
        )


# ── Filters display ────────────────────────────────────────────────

async def _show_filters(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str = None,
):
    """Show user's active filters."""
    user = await _ensure_user(user_id, username)
    if not user:
        await client.send_message(
            chat_id,
            "Пользователь не найден.",
            keyboard=BACK_TO_MENU_KEYBOARD,
        )
        return

    db = await get_sniper_db()
    filters = await db.get_user_filters(user['id'], active_only=False)

    if not filters:
        await client.send_message(
            chat_id,
            "У вас пока нет фильтров.\n\nСоздайте фильтры в Telegram-боте @TenderAI111_bot.",
            keyboard=BACK_TO_MENU_KEYBOARD,
        )
        return

    lines = ["<b>Ваши фильтры:</b>\n"]
    for i, f in enumerate(filters, 1):
        status = "ON" if f.get('is_active') else "OFF"
        name = f.get('name', 'Без названия')
        keywords = ", ".join(f.get('keywords', [])[:3]) if f.get('keywords') else "-"
        lines.append(f"{i}. [{status}] <b>{name}</b>\n   Ключевые слова: {keywords}")

    lines.append("\nУправление фильтрами — в Telegram-боте @TenderAI111_bot.")

    await client.send_message(
        chat_id,
        "\n".join(lines),
        keyboard=BACK_TO_MENU_KEYBOARD,
    )
