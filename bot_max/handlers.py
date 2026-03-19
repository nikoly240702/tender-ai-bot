"""
Handlers for VK Max bot updates.

Full-featured bot matching the Telegram bot's functionality:
- Start / Main Menu
- Filter Creation Wizard (keywords -> budget -> regions -> confirm)
- My Filters (view, toggle, delete)
- Tender-GPT (AI assistant)
- Subscription Info
- Help
- Tender Notifications
"""

import logging
from typing import Dict, Any, Optional

from bot_max.client import MaxBotClient
from tender_sniper.database import get_sniper_db

logger = logging.getLogger(__name__)

# ── State management (in-memory, resets on restart) ────────────────
# user_id -> {"state": "wizard_keywords", "data": {...}}
_user_states: Dict[int, Dict[str, Any]] = {}

# ── Tender-GPT integration ────────────────────────────────────────

_gpt_service = None
_gpt_active_chats: set = set()  # chat_ids currently in GPT mode


async def _get_gpt_service():
    global _gpt_service
    if _gpt_service is None:
        from tender_sniper.tender_gpt.service import TenderGPTService
        _gpt_service = TenderGPTService()
    return _gpt_service


# ── Price formatting helper ────────────────────────────────────────

def _format_price(price: Optional[float]) -> str:
    """Format price for display."""
    if price is None:
        return "без ограничений"
    if price >= 1_000_000_000:
        value = price / 1_000_000_000
        return f"{int(value)} млрд" if value == int(value) else f"{value:.1f} млрд"
    elif price >= 1_000_000:
        value = price / 1_000_000
        return f"{int(value)} млн" if value == int(value) else f"{value:.1f} млн"
    elif price >= 1_000:
        return f"{price / 1_000:.0f} тыс"
    return f"{price:.0f}"


# ── Keyboard layouts ──────────────────────────────────────────────

MAIN_MENU_KEYBOARD = [
    [{"type": "callback", "text": "Tender-GPT", "payload": "gpt"}],
    [
        {"type": "callback", "text": "Создать фильтр", "payload": "new_filter"},
        {"type": "callback", "text": "Мои фильтры", "payload": "my_filters"},
    ],
    [
        {"type": "callback", "text": "Подписка", "payload": "sub"},
        {"type": "callback", "text": "Помощь", "payload": "help"},
    ],
]

BACK_KEYBOARD = [
    [{"type": "callback", "text": "Главное меню", "payload": "menu"}],
]

EXIT_GPT_KEYBOARD = [
    [{"type": "callback", "text": "Выйти из чата", "payload": "menu"}],
]

CANCEL_WIZARD_KEYBOARD = [
    [{"type": "callback", "text": "Отмена", "payload": "wiz_cancel"}],
]

# Budget presets for wizard
BUDGET_KEYBOARD = [
    [
        {"type": "callback", "text": "до 1 млн", "payload": "bud_0_1"},
        {"type": "callback", "text": "1-5 млн", "payload": "bud_1_5"},
    ],
    [
        {"type": "callback", "text": "5-20 млн", "payload": "bud_5_20"},
        {"type": "callback", "text": "Любой", "payload": "bud_any"},
    ],
    [{"type": "callback", "text": "Отмена", "payload": "wiz_cancel"}],
]

# Region presets for wizard
REGION_KEYBOARD = [
    [
        {"type": "callback", "text": "Москва", "payload": "reg_moscow"},
        {"type": "callback", "text": "Санкт-Петербург", "payload": "reg_spb"},
    ],
    [
        {"type": "callback", "text": "Вся Россия", "payload": "reg_all"},
    ],
    [{"type": "callback", "text": "Отмена", "payload": "wiz_cancel"}],
]

# ── Texts ─────────────────────────────────────────────────────────

WELCOME_TEXT = (
    "<b>Tender Sniper</b>\n\n"
    "Бот для мониторинга и анализа госзакупок.\n\n"
    "Я помогу вам находить тендеры на zakupki.gov.ru автоматически:\n"
    "1. Создайте фильтр с критериями\n"
    "2. Бот мониторит 15,000+ тендеров ежедневно\n"
    "3. Получаете уведомления о подходящих\n\n"
    "Выберите действие:"
)

HELP_TEXT = (
    "<b>Помощь — Tender Sniper</b>\n\n"
    "<b>Tender-GPT</b> — AI-ассистент по госзакупкам. "
    "Найдёт тендеры, проанализирует документацию, ответит на вопросы по 44-ФЗ и 223-ФЗ.\n\n"
    "<b>Создать фильтр</b> — настройте фильтр мониторинга: "
    "ключевые слова, бюджет, регион. Бот будет присылать подходящие тендеры.\n\n"
    "<b>Мои фильтры</b> — просмотр, включение/выключение и удаление фильтров.\n\n"
    "<b>Подписка</b> — информация о тарифах и текущем плане.\n\n"
    "По вопросам: @nikolai_chizhik"
)


# ── Ensure user exists in DB ─────────────────────────────────────

async def _ensure_user(user_id: int, username: str = None) -> dict:
    """Create or update user in DB, return user dict."""
    db = await get_sniper_db()
    max_username = f"max_{username}" if username else f"max_{user_id}"
    await db.create_or_update_user(
        telegram_id=user_id,
        username=max_username,
        subscription_tier='trial',
    )
    return await db.get_user_by_telegram_id(user_id)


# ── Update dispatcher ─────────────────────────────────────────────

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


# ── Handlers ──────────────────────────────────────────────────────

async def handle_bot_started(
    client: MaxBotClient,
    user_id: int,
    chat_id: int,
    username: str = None,
):
    """User pressed 'Start' — send welcome message."""
    logger.info(f"Max bot: bot_started from user {user_id} in chat {chat_id}")
    await _ensure_user(user_id, username)
    _gpt_active_chats.discard(chat_id)
    _user_states.pop(user_id, None)
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

    # GPT mode — forward message to Tender-GPT
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

    # Wizard state — handle text input
    state = _user_states.get(user_id)
    if state:
        await _handle_wizard_text(client, chat_id, user_id, username, text, state)
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

    # ── Main navigation ──
    if payload == "menu":
        _gpt_active_chats.discard(chat_id)
        _user_states.pop(user_id, None)
        await client.send_message(chat_id, WELCOME_TEXT, keyboard=MAIN_MENU_KEYBOARD)

    elif payload == "gpt":
        _gpt_active_chats.add(chat_id)
        _user_states.pop(user_id, None)
        service = await _get_gpt_service()
        greeting = await service.get_greeting()
        await client.send_message(chat_id, greeting, keyboard=EXIT_GPT_KEYBOARD)

    elif payload == "help":
        await client.send_message(chat_id, HELP_TEXT, keyboard=BACK_KEYBOARD)

    elif payload == "sub":
        await _show_subscription(client, chat_id, user_id, username)

    elif payload == "sub_tiers":
        await _show_subscription_tiers(client, chat_id)

    elif payload == "sub_trial":
        await _activate_trial(client, chat_id, user_id, username)

    # ── Filter wizard ──
    elif payload == "new_filter":
        await _wizard_start(client, chat_id, user_id, username)

    elif payload == "wiz_cancel":
        _user_states.pop(user_id, None)
        await client.send_message(
            chat_id,
            "Создание фильтра отменено.",
            keyboard=MAIN_MENU_KEYBOARD,
        )

    elif payload.startswith("bud_"):
        await _wizard_handle_budget(client, chat_id, user_id, payload)

    elif payload.startswith("reg_"):
        await _wizard_handle_region(client, chat_id, user_id, payload)

    elif payload == "wiz_confirm":
        await _wizard_confirm(client, chat_id, user_id, username)

    elif payload == "wiz_edit":
        # Restart wizard keeping nothing
        await _wizard_start(client, chat_id, user_id, username)

    # ── My Filters ──
    elif payload == "my_filters":
        await _show_filters(client, chat_id, user_id, username)

    elif payload.startswith("fv_"):
        # View single filter: fv_<filter_id>
        await _view_filter(client, chat_id, user_id, username, payload)

    elif payload.startswith("ft_"):
        # Toggle filter: ft_<filter_id>
        await _toggle_filter(client, chat_id, user_id, username, payload)

    elif payload.startswith("fd_"):
        # Delete filter (ask confirmation): fd_<filter_id>
        await _confirm_delete_filter(client, chat_id, user_id, payload)

    elif payload.startswith("fdc_"):
        # Delete filter confirmed: fdc_<filter_id>
        await _delete_filter(client, chat_id, user_id, username, payload)

    elif payload.startswith("fdn_"):
        # Delete cancelled — go back to filter view
        fid = payload.replace("fdn_", "")
        await _view_filter(client, chat_id, user_id, username, f"fv_{fid}")

    else:
        logger.debug(f"Max bot: unknown callback payload: {payload}")


# ══════════════════════════════════════════════════════════════════
# GPT CHAT
# ══════════════════════════════════════════════════════════════════

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
            keyboard=BACK_KEYBOARD,
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

        remaining = quota.get('remaining', 0)
        limit = quota.get('limit', 0)
        if limit < 999999 and 0 < remaining <= 5:
            response_text += f"\n\n<i>Осталось сообщений: {remaining}/{limit}</i>"

        await client.send_message(chat_id, response_text, keyboard=EXIT_GPT_KEYBOARD)

        if not quota.get('allowed', True) and result.get('session_id') is None:
            _gpt_active_chats.discard(chat_id)

    except Exception as e:
        logger.error(f"Max bot GPT error for user {user_id}: {e}", exc_info=True)
        await client.send_message(
            chat_id,
            "Произошла ошибка. Попробуйте ещё раз.",
            keyboard=EXIT_GPT_KEYBOARD,
        )


# ══════════════════════════════════════════════════════════════════
# FILTER CREATION WIZARD
# ══════════════════════════════════════════════════════════════════

async def _wizard_start(client: MaxBotClient, chat_id: int, user_id: int, username: str):
    """Start filter creation wizard — Step 1: keywords."""
    await _ensure_user(user_id, username)

    _user_states[user_id] = {
        "state": "wizard_keywords",
        "data": {},
        "chat_id": chat_id,
    }

    await client.send_message(
        chat_id,
        (
            "<b>Создание фильтра (шаг 1/3)</b>\n\n"
            "Введите ключевые слова для поиска тендеров.\n\n"
            "Вы можете указать несколько слов через запятую, например:\n"
            "<i>компьютер, ноутбук, сервер</i>\n\n"
            "Или напишите фразу целиком:\n"
            "<i>поставка компьютерного оборудования</i>"
        ),
        keyboard=CANCEL_WIZARD_KEYBOARD,
    )


async def _handle_wizard_text(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
    text: str,
    state: Dict[str, Any],
):
    """Handle text input during wizard."""
    current = state.get("state")

    if current == "wizard_keywords":
        # Parse keywords from user input
        keywords = [kw.strip() for kw in text.replace(";", ",").split(",") if kw.strip()]
        if not keywords:
            await client.send_message(
                chat_id,
                "Введите хотя бы одно ключевое слово:",
                keyboard=CANCEL_WIZARD_KEYBOARD,
            )
            return

        # Limit to 15 keywords
        keywords = keywords[:15]
        state["data"]["keywords"] = keywords

        # Move to step 2: budget
        state["state"] = "wizard_budget"
        kw_text = ", ".join(keywords)

        await client.send_message(
            chat_id,
            (
                f"<b>Создание фильтра (шаг 2/3)</b>\n\n"
                f"Ключевые слова: <b>{kw_text}</b>\n\n"
                f"Выберите бюджет закупки:"
            ),
            keyboard=BUDGET_KEYBOARD,
        )

    elif current == "wizard_region_input":
        # User typed a region name
        region_name = text.strip()
        if not region_name:
            await client.send_message(
                chat_id,
                "Введите название региона или выберите из списка:",
                keyboard=REGION_KEYBOARD,
            )
            return

        state["data"]["regions"] = [region_name]
        state["state"] = "wizard_confirm"
        await _wizard_show_confirm(client, chat_id, user_id, state)


async def _wizard_handle_budget(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    payload: str,
):
    """Handle budget selection callback."""
    state = _user_states.get(user_id)
    if not state or state.get("state") != "wizard_budget":
        await client.send_message(chat_id, "Сессия истекла. Начните заново.", keyboard=MAIN_MENU_KEYBOARD)
        _user_states.pop(user_id, None)
        return

    budget_map = {
        "bud_0_1":  (None, 1_000_000),
        "bud_1_5":  (1_000_000, 5_000_000),
        "bud_5_20": (5_000_000, 20_000_000),
        "bud_any":  (None, None),
    }

    price_min, price_max = budget_map.get(payload, (None, None))
    state["data"]["price_min"] = price_min
    state["data"]["price_max"] = price_max

    # Move to step 3: region
    state["state"] = "wizard_region"

    # Format budget text
    if price_min and price_max:
        bud_text = f"{_format_price(price_min)} - {_format_price(price_max)} руб."
    elif price_max:
        bud_text = f"до {_format_price(price_max)} руб."
    elif price_min:
        bud_text = f"от {_format_price(price_min)} руб."
    else:
        bud_text = "любой"

    kw_text = ", ".join(state["data"].get("keywords", []))

    await client.send_message(
        chat_id,
        (
            f"<b>Создание фильтра (шаг 3/3)</b>\n\n"
            f"Ключевые слова: <b>{kw_text}</b>\n"
            f"Бюджет: <b>{bud_text}</b>\n\n"
            f"Выберите регион или введите название:"
        ),
        keyboard=REGION_KEYBOARD,
    )


async def _wizard_handle_region(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    payload: str,
):
    """Handle region selection callback."""
    state = _user_states.get(user_id)
    if not state or state.get("state") != "wizard_region":
        await client.send_message(chat_id, "Сессия истекла. Начните заново.", keyboard=MAIN_MENU_KEYBOARD)
        _user_states.pop(user_id, None)
        return

    region_map = {
        "reg_moscow": ["Москва"],
        "reg_spb": ["Санкт-Петербург"],
        "reg_all": [],
    }

    regions = region_map.get(payload)
    if regions is None:
        return

    state["data"]["regions"] = regions
    state["state"] = "wizard_confirm"
    await _wizard_show_confirm(client, chat_id, user_id, state)


async def _wizard_show_confirm(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    state: Dict[str, Any],
):
    """Show confirmation step for wizard."""
    data = state["data"]
    kw_text = ", ".join(data.get("keywords", []))

    price_min = data.get("price_min")
    price_max = data.get("price_max")
    if price_min and price_max:
        bud_text = f"{_format_price(price_min)} - {_format_price(price_max)} руб."
    elif price_max:
        bud_text = f"до {_format_price(price_max)} руб."
    elif price_min:
        bud_text = f"от {_format_price(price_min)} руб."
    else:
        bud_text = "любой"

    regions = data.get("regions", [])
    reg_text = ", ".join(regions) if regions else "Вся Россия"

    confirm_keyboard = [
        [
            {"type": "callback", "text": "Создать фильтр", "payload": "wiz_confirm"},
        ],
        [
            {"type": "callback", "text": "Изменить", "payload": "wiz_edit"},
            {"type": "callback", "text": "Отмена", "payload": "wiz_cancel"},
        ],
    ]

    await client.send_message(
        chat_id,
        (
            "<b>Подтверждение фильтра</b>\n\n"
            f"Ключевые слова: <b>{kw_text}</b>\n"
            f"Бюджет: <b>{bud_text}</b>\n"
            f"Регион: <b>{reg_text}</b>\n\n"
            "Всё верно? Нажмите «Создать фильтр» для сохранения."
        ),
        keyboard=confirm_keyboard,
    )


async def _wizard_confirm(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
):
    """Finalize wizard — create filter in DB."""
    state = _user_states.pop(user_id, None)
    if not state:
        await client.send_message(chat_id, "Сессия истекла. Начните заново.", keyboard=MAIN_MENU_KEYBOARD)
        return

    data = state["data"]
    keywords = data.get("keywords", [])

    if not keywords:
        await client.send_message(chat_id, "Ошибка: ключевые слова не указаны.", keyboard=MAIN_MENU_KEYBOARD)
        return

    try:
        user = await _ensure_user(user_id, username)
        if not user:
            await client.send_message(chat_id, "Ошибка: пользователь не найден.", keyboard=MAIN_MENU_KEYBOARD)
            return

        db = await get_sniper_db()

        # Check filter limit
        existing_filters = await db.get_user_filters(user['id'], active_only=False)
        max_filters = user.get('filters_limit', 3)
        if len(existing_filters) >= max_filters:
            await client.send_message(
                chat_id,
                (
                    f"<b>Достигнут лимит фильтров</b>\n\n"
                    f"У вас {len(existing_filters)} из {max_filters} фильтров.\n"
                    f"Удалите ненужные фильтры или улучшите тариф."
                ),
                keyboard=BACK_KEYBOARD,
            )
            return

        # Build filter name from first keywords
        filter_name = ", ".join(keywords[:3])
        if len(keywords) > 3:
            filter_name += f" +{len(keywords) - 3}"

        filter_id = await db.create_filter(
            user_id=user['id'],
            name=filter_name[:255],
            keywords=keywords,
            price_min=data.get("price_min"),
            price_max=data.get("price_max"),
            regions=data.get("regions") or None,
            is_active=True,
        )

        logger.info(f"Max bot: created filter {filter_id} for user {user_id}")

        # Try to generate AI intent
        try:
            from tender_sniper.ai_relevance_checker import generate_intent
            ai_intent = await generate_intent(
                filter_name=filter_name,
                keywords=keywords,
                exclude_keywords=[],
            )
            if ai_intent:
                await db.update_filter_intent(filter_id, ai_intent)
        except Exception as e:
            logger.warning(f"Max bot: failed to generate AI intent for filter {filter_id}: {e}")

        # Format confirmation
        price_min = data.get("price_min")
        price_max = data.get("price_max")
        if price_min and price_max:
            bud_text = f"{_format_price(price_min)} - {_format_price(price_max)} руб."
        elif price_max:
            bud_text = f"до {_format_price(price_max)} руб."
        elif price_min:
            bud_text = f"от {_format_price(price_min)} руб."
        else:
            bud_text = "любой"

        regions = data.get("regions", [])
        reg_text = ", ".join(regions) if regions else "Вся Россия"

        result_keyboard = [
            [{"type": "callback", "text": "Мои фильтры", "payload": "my_filters"}],
            [{"type": "callback", "text": "Создать ещё", "payload": "new_filter"}],
            [{"type": "callback", "text": "Главное меню", "payload": "menu"}],
        ]

        await client.send_message(
            chat_id,
            (
                f"<b>Фильтр создан!</b>\n\n"
                f"ID: #{filter_id}\n"
                f"Ключевые слова: <b>{', '.join(keywords)}</b>\n"
                f"Бюджет: <b>{bud_text}</b>\n"
                f"Регион: <b>{reg_text}</b>\n\n"
                f"Бот начал мониторинг. Вы получите уведомление, "
                f"как только появится подходящий тендер."
            ),
            keyboard=result_keyboard,
        )

    except Exception as e:
        logger.error(f"Max bot: error creating filter for user {user_id}: {e}", exc_info=True)
        await client.send_message(
            chat_id,
            f"Произошла ошибка при создании фильтра. Попробуйте позже.",
            keyboard=MAIN_MENU_KEYBOARD,
        )


# ══════════════════════════════════════════════════════════════════
# MY FILTERS
# ══════════════════════════════════════════════════════════════════

async def _show_filters(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str = None,
):
    """Show user's filters list."""
    user = await _ensure_user(user_id, username)
    if not user:
        await client.send_message(chat_id, "Пользователь не найден.", keyboard=BACK_KEYBOARD)
        return

    db = await get_sniper_db()
    filters = await db.get_user_filters(user['id'], active_only=False)

    if not filters:
        no_filters_keyboard = [
            [{"type": "callback", "text": "Создать фильтр", "payload": "new_filter"}],
            [{"type": "callback", "text": "Главное меню", "payload": "menu"}],
        ]
        await client.send_message(
            chat_id,
            (
                "<b>Мои фильтры</b>\n\n"
                "У вас пока нет фильтров.\n"
                "Создайте первый фильтр, чтобы получать уведомления о тендерах."
            ),
            keyboard=no_filters_keyboard,
        )
        return

    lines = ["<b>Мои фильтры</b>\n"]

    # Build keyboard with filter buttons
    keyboard = []
    for i, f in enumerate(filters, 1):
        fid = f.get('id')
        status = "ON" if f.get('is_active') else "OFF"
        name = f.get('name', 'Без названия')
        keywords = ", ".join(f.get('keywords', [])[:3]) if f.get('keywords') else "-"

        lines.append(f"{i}. [{status}] <b>{name}</b>\n   Ключевые слова: {keywords}")

        # Each filter gets a button to view details
        keyboard.append([
            {"type": "callback", "text": f"{i}. {name[:30]}", "payload": f"fv_{fid}"},
        ])

    keyboard.append([{"type": "callback", "text": "Создать фильтр", "payload": "new_filter"}])
    keyboard.append([{"type": "callback", "text": "Главное меню", "payload": "menu"}])

    await client.send_message(chat_id, "\n".join(lines), keyboard=keyboard)


async def _view_filter(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
    payload: str,
):
    """View single filter details with toggle/delete buttons."""
    try:
        filter_id = int(payload.replace("fv_", ""))
    except ValueError:
        return

    db = await get_sniper_db()
    filter_data = await db.get_filter_by_id(filter_id)

    if not filter_data:
        await client.send_message(chat_id, "Фильтр не найден.", keyboard=BACK_KEYBOARD)
        return

    # Verify ownership
    user = await _ensure_user(user_id, username)
    if not user or filter_data.get('user_id') != user['id']:
        await client.send_message(chat_id, "Фильтр не найден.", keyboard=BACK_KEYBOARD)
        return

    name = filter_data.get('name', 'Без названия')
    keywords = filter_data.get('keywords', [])
    is_active = filter_data.get('is_active', False)
    price_min = filter_data.get('price_min')
    price_max = filter_data.get('price_max')
    regions = filter_data.get('regions', [])

    status_text = "Активен" if is_active else "Приостановлен"
    status_icon = "ON" if is_active else "OFF"
    kw_text = ", ".join(keywords) if keywords else "—"

    if price_min and price_max:
        bud_text = f"{_format_price(price_min)} - {_format_price(price_max)} руб."
    elif price_max:
        bud_text = f"до {_format_price(price_max)} руб."
    elif price_min:
        bud_text = f"от {_format_price(price_min)} руб."
    else:
        bud_text = "без ограничений"

    reg_text = ", ".join(regions) if regions else "Вся Россия"

    toggle_text = "Приостановить" if is_active else "Возобновить"

    keyboard = [
        [{"type": "callback", "text": toggle_text, "payload": f"ft_{filter_id}"}],
        [{"type": "callback", "text": "Удалить", "payload": f"fd_{filter_id}"}],
        [{"type": "callback", "text": "Назад к фильтрам", "payload": "my_filters"}],
        [{"type": "callback", "text": "Главное меню", "payload": "menu"}],
    ]

    await client.send_message(
        chat_id,
        (
            f"<b>Фильтр #{filter_id}</b>\n\n"
            f"Название: <b>{name}</b>\n"
            f"Статус: <b>[{status_icon}] {status_text}</b>\n"
            f"Ключевые слова: <b>{kw_text}</b>\n"
            f"Бюджет: <b>{bud_text}</b>\n"
            f"Регион: <b>{reg_text}</b>"
        ),
        keyboard=keyboard,
    )


async def _toggle_filter(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
    payload: str,
):
    """Toggle filter active status."""
    try:
        filter_id = int(payload.replace("ft_", ""))
    except ValueError:
        return

    try:
        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await client.send_message(chat_id, "Фильтр не найден.", keyboard=BACK_KEYBOARD)
            return

        # Verify ownership
        user = await _ensure_user(user_id, username)
        if not user or filter_data.get('user_id') != user['id']:
            await client.send_message(chat_id, "Фильтр не найден.", keyboard=BACK_KEYBOARD)
            return

        new_status = not filter_data.get('is_active', True)
        await db.update_filter(filter_id=filter_id, is_active=new_status)

        status_text = "возобновлён" if new_status else "приостановлен"
        logger.info(f"Max bot: filter {filter_id} toggled to {new_status} by user {user_id}")

        await client.send_message(
            chat_id,
            f"Фильтр <b>#{filter_id}</b> {status_text}.",
            keyboard=[
                [{"type": "callback", "text": "Назад к фильтру", "payload": f"fv_{filter_id}"}],
                [{"type": "callback", "text": "Мои фильтры", "payload": "my_filters"}],
            ],
        )

    except Exception as e:
        logger.error(f"Max bot: error toggling filter {filter_id}: {e}", exc_info=True)
        await client.send_message(chat_id, "Произошла ошибка.", keyboard=BACK_KEYBOARD)


async def _confirm_delete_filter(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    payload: str,
):
    """Ask for confirmation before deleting a filter."""
    try:
        filter_id = int(payload.replace("fd_", ""))
    except ValueError:
        return

    confirm_keyboard = [
        [{"type": "callback", "text": "Да, удалить", "payload": f"fdc_{filter_id}"}],
        [{"type": "callback", "text": "Нет, отмена", "payload": f"fdn_{filter_id}"}],
    ]

    await client.send_message(
        chat_id,
        f"Вы уверены, что хотите удалить фильтр <b>#{filter_id}</b>?\n\nЭто действие нельзя отменить.",
        keyboard=confirm_keyboard,
    )


async def _delete_filter(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
    payload: str,
):
    """Delete a filter after confirmation."""
    try:
        filter_id = int(payload.replace("fdc_", ""))
    except ValueError:
        return

    try:
        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await client.send_message(chat_id, "Фильтр не найден.", keyboard=BACK_KEYBOARD)
            return

        # Verify ownership
        user = await _ensure_user(user_id, username)
        if not user or filter_data.get('user_id') != user['id']:
            await client.send_message(chat_id, "Фильтр не найден.", keyboard=BACK_KEYBOARD)
            return

        await db.delete_filter(filter_id)
        logger.info(f"Max bot: filter {filter_id} deleted by user {user_id}")

        await client.send_message(
            chat_id,
            f"Фильтр <b>#{filter_id}</b> удалён.",
            keyboard=[
                [{"type": "callback", "text": "Мои фильтры", "payload": "my_filters"}],
                [{"type": "callback", "text": "Главное меню", "payload": "menu"}],
            ],
        )

    except Exception as e:
        logger.error(f"Max bot: error deleting filter {filter_id}: {e}", exc_info=True)
        await client.send_message(chat_id, "Произошла ошибка.", keyboard=BACK_KEYBOARD)


# ══════════════════════════════════════════════════════════════════
# SUBSCRIPTION
# ══════════════════════════════════════════════════════════════════

SUBSCRIPTION_TIERS = {
    'trial': {
        'name': 'Пробный период',
        'price': 0,
        'days': 14,
        'max_filters': 3,
        'max_notifications_per_day': 20,
        'features': [
            '3 фильтра мониторинга',
            '20 уведомлений/день',
            'Мгновенный поиск',
        ]
    },
    'basic': {
        'name': 'Basic',
        'price': 1490,
        'max_filters': 5,
        'max_notifications_per_day': 100,
        'features': [
            '5 фильтров мониторинга',
            '100 уведомлений/день',
            'AI-анализ (10/мес)',
            'Telegram-поддержка',
        ]
    },
    'premium': {
        'name': 'Premium',
        'price': 2990,
        'max_filters': 20,
        'max_notifications_per_day': 9999,
        'features': [
            '20 фильтров мониторинга',
            'Безлимит уведомлений',
            'AI-анализ (50/мес)',
            'Приоритетная поддержка',
        ]
    }
}


async def _show_subscription(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str = None,
):
    """Show current subscription status."""
    user = await _ensure_user(user_id, username)
    if not user:
        await client.send_message(chat_id, "Пользователь не найден.", keyboard=BACK_KEYBOARD)
        return

    db = await get_sniper_db()
    user_full = await db.get_user_subscription_info(user_id)

    tier = user_full.get('subscription_tier', 'trial') if user_full else 'trial'
    expires_at = user_full.get('trial_expires_at') if user_full else None
    filters_limit = user_full.get('filters_limit', 3) if user_full else 3
    notifications_limit = user_full.get('notifications_limit', 20) if user_full else 20

    # Calculate days remaining
    days_remaining = 0
    if expires_at:
        from datetime import datetime
        if isinstance(expires_at, str):
            try:
                expires_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            except Exception:
                expires_dt = datetime.now()
        else:
            expires_dt = expires_at
        if expires_dt.tzinfo:
            expires_dt = expires_dt.replace(tzinfo=None)
        delta = expires_dt - datetime.now()
        days_remaining = max(0, delta.days)

    is_active = tier in ['basic', 'premium'] or (tier == 'trial' and days_remaining > 0)
    is_trial = tier == 'trial'

    tier_info = SUBSCRIPTION_TIERS.get(tier, SUBSCRIPTION_TIERS['trial'])

    if is_active:
        expires_display = '—'
        if expires_at:
            if isinstance(expires_at, str):
                expires_display = expires_at[:10]
            else:
                expires_display = expires_at.strftime('%d.%m.%Y')

        text = (
            f"<b>Ваша подписка</b>\n\n"
            f"Тариф: <b>{tier_info['name']}</b>\n"
            f"Действует до: <b>{expires_display}</b>\n"
            f"Осталось дней: <b>{days_remaining}</b>\n\n"
            f"<b>Лимиты:</b>\n"
            f"- Фильтров: {filters_limit}\n"
            f"- Уведомлений/день: {notifications_limit}\n\n"
            f"<b>Возможности:</b>\n"
        )
        for feature in tier_info['features']:
            text += f"- {feature}\n"

        if is_trial:
            text += f"\n<i>Пробный период закончится через {days_remaining} дней.</i>"
    else:
        text = (
            "<b>Подписка</b>\n\n"
            "У вас нет активной подписки.\n\n"
            "Активируйте пробный период на 14 дней бесплатно или выберите тариф."
        )

    keyboard = []
    if not is_active:
        keyboard.append([{"type": "callback", "text": "Активировать Trial (14 дней)", "payload": "sub_trial"}])
    keyboard.append([{"type": "callback", "text": "Посмотреть тарифы", "payload": "sub_tiers"}])
    keyboard.append([{"type": "callback", "text": "Главное меню", "payload": "menu"}])

    await client.send_message(chat_id, text, keyboard=keyboard)


async def _show_subscription_tiers(client: MaxBotClient, chat_id: int):
    """Show available subscription tiers."""
    text = "<b>Тарифные планы</b>\n\n"

    for tier_id, tier_info in SUBSCRIPTION_TIERS.items():
        if tier_id == 'trial':
            continue
        price_text = f"{tier_info['price']} руб./мес"
        text += f"<b>{tier_info['name']}</b> — {price_text}\n"
        for feature in tier_info['features']:
            text += f"  - {feature}\n"
        text += "\n"

    text += (
        "<i>Оплата подписки доступна через Telegram-бота @TenderAI111_bot.\n"
        "Подписка действует для обоих ботов (Telegram и Max).</i>"
    )

    keyboard = [
        [{"type": "callback", "text": "Назад", "payload": "sub"}],
        [{"type": "callback", "text": "Главное меню", "payload": "menu"}],
    ]

    await client.send_message(chat_id, text, keyboard=keyboard)


async def _activate_trial(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str = None,
):
    """Activate trial subscription."""
    user = await _ensure_user(user_id, username)
    if not user:
        await client.send_message(chat_id, "Пользователь не найден.", keyboard=BACK_KEYBOARD)
        return

    db = await get_sniper_db()
    existing_sub = await db.get_subscription(user['id'])

    if existing_sub:
        await client.send_message(
            chat_id,
            (
                "<b>Пробный период уже был активирован</b>\n\n"
                "Вы можете оформить платную подписку через Telegram-бота @TenderAI111_bot."
            ),
            keyboard=BACK_KEYBOARD,
        )
        return

    trial_config = SUBSCRIPTION_TIERS['trial']
    await db.create_subscription(
        user_id=user['id'],
        tier='trial',
        days=trial_config['days'],
        max_filters=trial_config['max_filters'],
        max_notifications_per_day=trial_config['max_notifications_per_day'],
    )

    features_text = "\n".join([f"- {f}" for f in trial_config['features']])

    await client.send_message(
        chat_id,
        (
            f"<b>Пробный период активирован!</b>\n\n"
            f"Тариф: {trial_config['name']}\n"
            f"Срок: {trial_config['days']} дней\n\n"
            f"<b>Доступные возможности:</b>\n"
            f"{features_text}\n\n"
            f"Теперь вы можете создавать фильтры и получать уведомления!"
        ),
        keyboard=[
            [{"type": "callback", "text": "Создать фильтр", "payload": "new_filter"}],
            [{"type": "callback", "text": "Главное меню", "payload": "menu"}],
        ],
    )

    logger.info(f"Max bot: trial activated for user {user_id}")


# ══════════════════════════════════════════════════════════════════
# TENDER NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════

async def send_max_notification(
    client: MaxBotClient,
    chat_id: int,
    tender: Dict[str, Any],
    match_info: Optional[Dict[str, Any]] = None,
):
    """
    Send a tender notification to a Max user.

    Called by the notification system when a tender matches a user's filter.

    Args:
        client: MaxBotClient instance
        chat_id: Max chat ID for the user
        tender: Tender data dict with keys: name, price, customer, region, deadline, law_type, url, etc.
        match_info: Optional match details (filter_name, relevance_score, matched_keywords)
    """
    try:
        name = tender.get('name', 'Без названия')
        price = tender.get('price')
        customer = tender.get('customer', '—')
        region = tender.get('region', '—')
        deadline = tender.get('deadline', '—')
        law_type = tender.get('law_type', '')
        url = tender.get('url', '')

        # Format price
        if price:
            price_text = f"{_format_price(price)} руб."
        else:
            price_text = "не указана"

        # Header with match info
        text = "<b>Новый тендер</b>\n\n"

        if match_info:
            filter_name = match_info.get('filter_name', '')
            relevance = match_info.get('relevance_score')
            if filter_name:
                text += f"Фильтр: <b>{filter_name}</b>\n"
            if relevance is not None:
                text += f"Релевантность: <b>{relevance}%</b>\n"
            text += "\n"

        text += (
            f"<b>{name[:200]}</b>\n\n"
            f"Цена: <b>{price_text}</b>\n"
            f"Заказчик: {customer[:100]}\n"
            f"Регион: {region}\n"
            f"Подача до: {deadline}\n"
        )

        if law_type:
            text += f"Закон: {law_type}\n"

        keyboard = []
        if url:
            keyboard.append([{"type": "callback", "text": "Подробнее", "payload": "menu"}])
            # Note: Max inline keyboard doesn't support URL buttons the same way.
            # Append link in text instead.
            text += f"\n<a href=\"{url}\">Открыть на zakupki.gov.ru</a>"

        keyboard.append([{"type": "callback", "text": "Мои фильтры", "payload": "my_filters"}])

        await client.send_message(chat_id, text, keyboard=keyboard)

        logger.info(f"Max bot: notification sent to chat {chat_id}: {name[:50]}")

    except Exception as e:
        logger.error(f"Max bot: error sending notification to chat {chat_id}: {e}", exc_info=True)
