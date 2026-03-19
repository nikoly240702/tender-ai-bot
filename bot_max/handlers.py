"""
Handlers for VK Max bot updates.

Full-featured bot matching the Telegram bot's functionality:
- Start / Main Menu
- Filter Creation Wizard (9 steps)
- My Filters (view, toggle, delete)
- Tender-GPT (AI assistant)
- Subscription Info
- Help
- Tender Notifications
"""

import logging
from typing import Dict, Any, Optional, List

from bot_max.client import MaxBotClient
from tender_sniper.database import get_sniper_db

logger = logging.getLogger(__name__)

# ── State management (in-memory, resets on restart) ────────────────
# user_id -> {"state": "wiz_...", "data": {...}, "chat_id": int}
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
        return f"{int(value)} млрд ₽" if value == int(value) else f"{value:.1f} млрд ₽"
    elif price >= 1_000_000:
        value = price / 1_000_000
        return f"{int(value)} млн ₽" if value == int(value) else f"{value:.1f} млн ₽"
    elif price >= 1_000:
        return f"{price / 1_000:.0f} тыс ₽"
    return f"{price:.0f} ₽"


def _format_budget_text(data: dict) -> str:
    """Format budget range for display."""
    price_min = data.get("price_min")
    price_max = data.get("price_max")
    if price_min and price_max:
        return f"{_format_price(price_min)} — {_format_price(price_max)}"
    elif price_max:
        return f"до {_format_price(price_max)}"
    elif price_min:
        return f"от {_format_price(price_min)}"
    return "без ограничений"


# ══════════════════════════════════════════════════════════════════
# TENDER TYPES / LAWS / BUDGET PRESETS
# ══════════════════════════════════════════════════════════════════

TENDER_TYPES = {
    "goods":    {"icon": "📦", "name": "Товары",  "value": "товары"},
    "services": {"icon": "🔧", "name": "Услуги",  "value": "услуги"},
    "works":    {"icon": "🏗", "name": "Работы",  "value": "работы"},
    "any":      {"icon": "📋", "name": "Любые",   "value": None},
}

LAW_TYPES = {
    "44fz":  {"icon": "📜", "name": "44-ФЗ (госзакупки)",     "value": "44"},
    "223fz": {"icon": "📜", "name": "223-ФЗ (корпоративные)", "value": "223"},
    "any":   {"icon": "📋", "name": "Любой закон",            "value": None},
}

BUDGET_PRESETS = [
    {"label": "до 500 тыс",       "min": None,        "max": 500_000,      "payload": "bud_0_500k"},
    {"label": "500 тыс — 1 млн",  "min": 500_000,     "max": 1_000_000,    "payload": "bud_500k_1m"},
    {"label": "1 — 5 млн",        "min": 1_000_000,   "max": 5_000_000,    "payload": "bud_1_5"},
    {"label": "5 — 20 млн",       "min": 5_000_000,   "max": 20_000_000,   "payload": "bud_5_20"},
    {"label": "20+ млн",          "min": 20_000_000,  "max": None,         "payload": "bud_20_plus"},
    {"label": "Любой бюджет",     "min": None,        "max": None,         "payload": "bud_any"},
]

POPULAR_REGIONS = [
    {"name": "Москва",            "payload": "reg_moscow"},
    {"name": "Санкт-Петербург",   "payload": "reg_spb"},
    {"name": "Московская обл.",   "payload": "reg_mo"},
    {"name": "Краснодарский край", "payload": "reg_krasnodar"},
    {"name": "Свердловская обл.", "payload": "reg_sverdlovsk"},
    {"name": "Новосибирская обл.", "payload": "reg_novosibirsk"},
    {"name": "Татарстан",         "payload": "reg_tatarstan"},
    {"name": "Нижегородская обл.", "payload": "reg_nn"},
]


# ── Keyboard layouts ──────────────────────────────────────────────

MAIN_MENU_KEYBOARD = [
    [{"type": "callback", "text": "🤖 Tender-GPT", "payload": "gpt"}],
    [
        {"type": "callback", "text": "➕ Создать фильтр", "payload": "new_filter"},
        {"type": "callback", "text": "📋 Мои фильтры", "payload": "my_filters"},
    ],
    [
        {"type": "callback", "text": "💳 Подписка", "payload": "sub"},
        {"type": "callback", "text": "❓ Помощь", "payload": "help"},
    ],
]

BACK_KEYBOARD = [
    [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
]

EXIT_GPT_KEYBOARD = [
    [{"type": "callback", "text": "🚪 Выйти из чата", "payload": "menu"}],
]

CANCEL_WIZARD_KEYBOARD = [
    [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
]


def _make_tender_type_keyboard(selected: List[str] = None) -> list:
    """Build tender-type toggle keyboard."""
    if selected is None:
        selected = []
    keyboard = []
    row = []
    for code, info in TENDER_TYPES.items():
        if code == "any":
            continue
        check = "✅ " if code in selected else ""
        btn = {"type": "callback", "text": f"{check}{info['icon']} {info['name']}", "payload": f"tt_{code}"}
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([{"type": "callback", "text": "📋 Любые (сбросить)", "payload": "tt_any"}])
    keyboard.append([{"type": "callback", "text": "➡️ Продолжить", "payload": "tt_continue"}])
    keyboard.append([{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}])
    return keyboard


def _make_budget_keyboard() -> list:
    """Build budget presets keyboard."""
    keyboard = []
    row = []
    for preset in BUDGET_PRESETS:
        btn = {"type": "callback", "text": f"💰 {preset['label']}", "payload": preset["payload"]}
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([{"type": "callback", "text": "✍️ Ввести вручную", "payload": "bud_custom"}])
    keyboard.append([{"type": "callback", "text": "◀️ Назад", "payload": "wiz_back_name"}])
    keyboard.append([{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}])
    return keyboard


def _make_region_keyboard(selected: List[str] = None) -> list:
    """Build region selection keyboard."""
    if selected is None:
        selected = []
    keyboard = []
    row = []
    for reg in POPULAR_REGIONS:
        check = "✅ " if reg["name"] in selected else ""
        btn = {"type": "callback", "text": f"{check}📍 {reg['name']}", "payload": reg["payload"]}
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([{"type": "callback", "text": "🌍 Вся Россия (сбросить)", "payload": "reg_all"}])
    sel_count = len(selected)
    continue_label = f"➡️ Продолжить ({sel_count} выбрано)" if sel_count else "➡️ Продолжить (вся Россия)"
    keyboard.append([{"type": "callback", "text": continue_label, "payload": "reg_continue"}])
    keyboard.append([{"type": "callback", "text": "✍️ Ввести свой регион", "payload": "reg_custom"}])
    keyboard.append([{"type": "callback", "text": "◀️ Назад", "payload": "wiz_back_budget"}])
    keyboard.append([{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}])
    return keyboard


def _make_law_keyboard() -> list:
    """Build law selection keyboard."""
    keyboard = []
    for code, info in LAW_TYPES.items():
        keyboard.append([{"type": "callback", "text": f"{info['icon']} {info['name']}", "payload": f"law_{code}"}])
    keyboard.append([{"type": "callback", "text": "◀️ Назад", "payload": "wiz_back_region"}])
    keyboard.append([{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}])
    return keyboard


def _make_exclusions_keyboard() -> list:
    """Build exclusions step keyboard."""
    return [
        [{"type": "callback", "text": "⏭ Пропустить (без исключений)", "payload": "excl_skip"}],
        [{"type": "callback", "text": "◀️ Назад", "payload": "wiz_back_law"}],
        [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
    ]


def _make_automonitor_keyboard() -> list:
    """Build automonitor step keyboard."""
    return [
        [{"type": "callback", "text": "🔔 Да, отслеживать новые тендеры", "payload": "mon_yes"}],
        [{"type": "callback", "text": "🔕 Нет, только разовый поиск", "payload": "mon_no"}],
        [{"type": "callback", "text": "◀️ Назад", "payload": "wiz_back_excl"}],
        [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
    ]


def _make_confirm_keyboard() -> list:
    """Build confirmation step keyboard."""
    return [
        [{"type": "callback", "text": "🚀 Создать фильтр", "payload": "wiz_confirm"}],
        [{"type": "callback", "text": "✏️ Изменить настройки", "payload": "wiz_edit"}],
        [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
    ]


def _make_edit_keyboard() -> list:
    """Build edit-settings keyboard."""
    return [
        [
            {"type": "callback", "text": "📦 Тип", "payload": "edit_type"},
            {"type": "callback", "text": "🔑 Слова", "payload": "edit_keywords"},
        ],
        [
            {"type": "callback", "text": "📝 Название", "payload": "edit_name"},
            {"type": "callback", "text": "💰 Бюджет", "payload": "edit_budget"},
        ],
        [
            {"type": "callback", "text": "📍 Регион", "payload": "edit_region"},
            {"type": "callback", "text": "⚖️ Закон", "payload": "edit_law"},
        ],
        [
            {"type": "callback", "text": "🚫 Исключения", "payload": "edit_excl"},
            {"type": "callback", "text": "📡 Мониторинг", "payload": "edit_monitor"},
        ],
        [{"type": "callback", "text": "🚀 Создать фильтр", "payload": "wiz_confirm"}],
        [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
    ]


# ── Texts ─────────────────────────────────────────────────────────

WELCOME_TEXT = (
    "🎯 <b>Tender Sniper</b>\n\n"
    "Бот для мониторинга и анализа госзакупок.\n\n"
    "Я помогу вам находить тендеры на zakupki.gov.ru автоматически:\n"
    "1️⃣ Создайте фильтр с критериями\n"
    "2️⃣ Бот мониторит 15,000+ тендеров ежедневно\n"
    "3️⃣ Получаете уведомления о подходящих\n\n"
    "Выберите действие:"
)

HELP_TEXT = (
    "❓ <b>Помощь — Tender Sniper</b>\n\n"
    "🤖 <b>Tender-GPT</b> — AI-ассистент по госзакупкам. "
    "Найдёт тендеры, проанализирует документацию, ответит на вопросы по 44-ФЗ и 223-ФЗ.\n\n"
    "➕ <b>Создать фильтр</b> — настройте фильтр мониторинга: "
    "тип закупки, ключевые слова, бюджет, регион, закон, исключения и автомониторинг.\n\n"
    "📋 <b>Мои фильтры</b> — просмотр, включение/выключение и удаление фильтров.\n\n"
    "💳 <b>Подписка</b> — информация о тарифах и текущем плане.\n\n"
    "По вопросам: @nikolai_chizhik"
)


def _get_current_settings_text(data: dict) -> str:
    """Format current wizard settings for display."""
    tender_type_name = data.get("tender_type_name", "Любые")
    keywords = data.get("keywords", [])
    filter_name = data.get("filter_name", "—")
    regions = data.get("regions", [])
    law_type_name = data.get("law_type_name", "Любой")
    exclude_keywords = data.get("exclude_keywords", [])
    automonitor = data.get("automonitor", True)

    budget_text = _format_budget_text(data)

    region_text = ", ".join(regions) if regions else "Вся Россия"
    if len(regions) > 3:
        region_text = f"{', '.join(regions[:3])} +{len(regions) - 3}"

    excl_text = ", ".join(exclude_keywords[:3]) if exclude_keywords else "нет"
    if len(exclude_keywords) > 3:
        excl_text += f" +{len(exclude_keywords) - 3}"

    monitor_text = "включён 🔔" if automonitor else "выключен 🔕"

    return (
        f"<b>Текущие настройки:</b>\n"
        f"📦 Тип: <b>{tender_type_name}</b>\n"
        f"🔑 Слова: <b>{', '.join(keywords) if keywords else 'не указаны'}</b>\n"
        f"📝 Название: <b>{filter_name}</b>\n"
        f"💰 Бюджет: <b>{budget_text}</b>\n"
        f"📍 Регион: <b>{region_text}</b>\n"
        f"⚖️ Закон: <b>{law_type_name}</b>\n"
        f"🚫 Исключения: <b>{excl_text}</b>\n"
        f"📡 Автомониторинг: <b>{monitor_text}</b>"
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


# ── Region name resolver ─────────────────────────────────────────

REGION_PAYLOAD_MAP = {
    "reg_moscow":     "Москва",
    "reg_spb":        "Санкт-Петербург",
    "reg_mo":         "Московская область",
    "reg_krasnodar":  "Краснодарский край",
    "reg_sverdlovsk": "Свердловская область",
    "reg_novosibirsk": "Новосибирская область",
    "reg_tatarstan":  "Республика Татарстан",
    "reg_nn":         "Нижегородская область",
}


# ══════════════════════════════════════════════════════════════════
# UPDATE DISPATCHER
# ══════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════
# HANDLERS
# ══════════════════════════════════════════════════════════════════

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

    # ── Filter wizard: start / cancel ──
    elif payload == "new_filter":
        await _wizard_start(client, chat_id, user_id, username)

    elif payload == "wiz_cancel":
        _user_states.pop(user_id, None)
        await client.send_message(chat_id, "❌ Создание фильтра отменено.", keyboard=MAIN_MENU_KEYBOARD)

    # ── Step 1: Tender type ──
    elif payload.startswith("tt_"):
        await _wizard_handle_tender_type(client, chat_id, user_id, payload)

    # ── Step 4: Budget ──
    elif payload.startswith("bud_"):
        await _wizard_handle_budget(client, chat_id, user_id, payload)

    # ── Step 5: Region ──
    elif payload.startswith("reg_"):
        await _wizard_handle_region(client, chat_id, user_id, payload)

    # ── Step 6: Law ──
    elif payload.startswith("law_"):
        await _wizard_handle_law(client, chat_id, user_id, payload)

    # ── Step 7: Exclusions ──
    elif payload == "excl_skip":
        await _wizard_handle_excl_skip(client, chat_id, user_id)

    # ── Step 8: Automonitor ──
    elif payload.startswith("mon_"):
        await _wizard_handle_automonitor(client, chat_id, user_id, payload)

    # ── Step 3: Filter name skip ──
    elif payload == "name_skip":
        await _wizard_handle_name_skip(client, chat_id, user_id)

    # ── Step 9: Confirm / Edit ──
    elif payload == "wiz_confirm":
        await _wizard_confirm(client, chat_id, user_id, username)

    elif payload == "wiz_edit":
        await _wizard_show_edit(client, chat_id, user_id)

    # ── Edit individual fields ──
    elif payload.startswith("edit_"):
        await _wizard_handle_edit_field(client, chat_id, user_id, payload)

    # ── Back buttons ──
    elif payload.startswith("wiz_back_"):
        await _wizard_handle_back(client, chat_id, user_id, payload)

    # ── My Filters ──
    elif payload == "my_filters":
        await _show_filters(client, chat_id, user_id, username)

    elif payload.startswith("fv_"):
        await _view_filter(client, chat_id, user_id, username, payload)

    elif payload.startswith("ft_"):
        await _toggle_filter(client, chat_id, user_id, username, payload)

    elif payload.startswith("fd_"):
        await _confirm_delete_filter(client, chat_id, user_id, payload)

    elif payload.startswith("fdc_"):
        await _delete_filter(client, chat_id, user_id, username, payload)

    elif payload.startswith("fdn_"):
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
            "⚠️ Пользователь не найден. Нажмите Start для регистрации.",
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
            response_text += f"\n\n<i>⏳ Осталось сообщений: {remaining}/{limit}</i>"

        await client.send_message(chat_id, response_text, keyboard=EXIT_GPT_KEYBOARD)

        if not quota.get('allowed', True) and result.get('session_id') is None:
            _gpt_active_chats.discard(chat_id)

    except Exception as e:
        logger.error(f"Max bot GPT error for user {user_id}: {e}", exc_info=True)
        await client.send_message(
            chat_id,
            "⚠️ Произошла ошибка. Попробуйте ещё раз.",
            keyboard=EXIT_GPT_KEYBOARD,
        )


# ══════════════════════════════════════════════════════════════════
# FILTER CREATION WIZARD — 9 STEPS
# ══════════════════════════════════════════════════════════════════
#
# Step 1: Tender type (goods/services/works/any) — callbacks
# Step 2: Keywords — text input
# Step 3: Filter name — text input or skip (auto)
# Step 4: Budget — preset callbacks or custom text input
# Step 5: Regions — multi-select callbacks or custom text input
# Step 6: Law (44-FZ / 223-FZ / any) — callbacks
# Step 7: Exclude keywords — text input or skip
# Step 8: Automonitor (on/off) — callbacks
# Step 9: Confirmation — confirm / edit / cancel
# ══════════════════════════════════════════════════════════════════


async def _wizard_start(client: MaxBotClient, chat_id: int, user_id: int, username: str):
    """Start filter creation wizard — Step 1: tender type."""
    await _ensure_user(user_id, username)

    _user_states[user_id] = {
        "state": "wiz_tender_type",
        "data": {
            "selected_types": [],
            "tender_type_name": "Любые",
            "tender_types": [],
            "keywords": [],
            "filter_name": "",
            "price_min": None,
            "price_max": None,
            "regions": [],
            "law_type": None,
            "law_type_name": "Любой",
            "exclude_keywords": [],
            "automonitor": True,
        },
        "chat_id": chat_id,
    }

    await client.send_message(
        chat_id,
        (
            "🎯 <b>Создание фильтра</b>\n\n"
            "<b>Шаг 1/9:</b> Что ищем?\n\n"
            "Выберите один или несколько типов закупки:"
        ),
        keyboard=_make_tender_type_keyboard([]),
    )


async def _wizard_handle_tender_type(client: MaxBotClient, chat_id: int, user_id: int, payload: str):
    """Handle tender type toggle/continue callbacks."""
    state = _user_states.get(user_id)
    if not state or not state["state"].startswith("wiz_tender_type"):
        # Maybe user is at a different step but we got an old callback
        return

    code = payload.replace("tt_", "")

    if code == "continue":
        # Finalize selection and go to step 2
        selected = state["data"].get("selected_types", [])
        if selected:
            tender_types_list = [TENDER_TYPES[c]["value"] for c in selected if TENDER_TYPES[c]["value"]]
            type_names = [TENDER_TYPES[c]["name"] for c in selected]
            type_name_str = ", ".join(type_names)
        else:
            tender_types_list = []
            type_name_str = "Любые"

        state["data"]["tender_types"] = tender_types_list
        state["data"]["tender_type_name"] = type_name_str
        state["state"] = "wiz_keywords"

        await client.send_message(
            chat_id,
            (
                f"🎯 <b>Создание фильтра</b>\n\n"
                f"✅ Тип: <b>{type_name_str}</b>\n\n"
                f"<b>Шаг 2/9:</b> Введите ключевые слова\n\n"
                f"Укажите через запятую, что вы ищете.\n"
                f"Например: <i>компьютер, ноутбук, сервер</i>"
            ),
            keyboard=[
                [{"type": "callback", "text": "◀️ Назад", "payload": "wiz_back_type"}],
                [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
            ],
        )
        return

    if code == "any":
        # Reset selection
        state["data"]["selected_types"] = []
    else:
        selected = state["data"].get("selected_types", [])
        if code in selected:
            selected.remove(code)
        else:
            if code in TENDER_TYPES:
                selected.append(code)
        state["data"]["selected_types"] = selected

    await client.send_message(
        chat_id,
        (
            "🎯 <b>Создание фильтра</b>\n\n"
            "<b>Шаг 1/9:</b> Что ищем?\n\n"
            "Выберите один или несколько типов закупки:"
        ),
        keyboard=_make_tender_type_keyboard(state["data"]["selected_types"]),
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

    # ── Step 2: Keywords ──
    if current == "wiz_keywords":
        keywords = [kw.strip() for kw in text.replace(";", ",").split(",") if kw.strip()]
        if not keywords:
            await client.send_message(
                chat_id,
                "⚠️ Введите хотя бы одно ключевое слово:",
                keyboard=CANCEL_WIZARD_KEYBOARD,
            )
            return

        keywords = keywords[:15]
        state["data"]["keywords"] = keywords

        # Auto-generate filter name
        auto_name = ", ".join(keywords[:3])
        if len(keywords) > 3:
            auto_name += f" +{len(keywords) - 3}"
        state["data"]["filter_name"] = auto_name

        # Move to step 3: filter name
        state["state"] = "wiz_filter_name"

        await client.send_message(
            chat_id,
            (
                f"🎯 <b>Создание фильтра</b>\n\n"
                f"✅ Тип: <b>{state['data'].get('tender_type_name', 'Любые')}</b>\n"
                f"✅ Слова: <b>{', '.join(keywords)}</b>\n\n"
                f"<b>Шаг 3/9:</b> Название фильтра\n\n"
                f"Введите название для фильтра (для удобства поиска).\n\n"
                f"💡 Или нажмите «Пропустить» — название будет:\n"
                f"<code>{auto_name}</code>"
            ),
            keyboard=[
                [{"type": "callback", "text": "⏭ Пропустить (авто-название)", "payload": "name_skip"}],
                [{"type": "callback", "text": "◀️ Назад", "payload": "wiz_back_keywords"}],
                [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
            ],
        )

    # ── Step 3: Filter name (user typed custom name) ──
    elif current == "wiz_filter_name":
        custom_name = text.strip()
        if len(custom_name) < 2:
            await client.send_message(
                chat_id,
                "⚠️ Название слишком короткое. Минимум 2 символа.",
                keyboard=[
                    [{"type": "callback", "text": "⏭ Пропустить (авто-название)", "payload": "name_skip"}],
                    [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
                ],
            )
            return
        if len(custom_name) > 100:
            await client.send_message(
                chat_id,
                "⚠️ Название слишком длинное. Максимум 100 символов.",
                keyboard=[
                    [{"type": "callback", "text": "⏭ Пропустить (авто-название)", "payload": "name_skip"}],
                    [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
                ],
            )
            return

        state["data"]["filter_name"] = custom_name
        state["state"] = "wiz_budget"
        await _show_budget_step(client, chat_id, state)

    # ── Step 4: Budget custom input ──
    elif current == "wiz_budget_custom_min":
        cleaned = text.strip().replace(" ", "").replace(",", "")
        try:
            price_min = int(cleaned)
            if price_min < 0:
                raise ValueError
            if price_min == 0:
                price_min = None
        except ValueError:
            await client.send_message(
                chat_id,
                "⚠️ Введите число. Например: 1000000",
                keyboard=CANCEL_WIZARD_KEYBOARD,
            )
            return

        state["data"]["price_min"] = price_min
        state["state"] = "wiz_budget_custom_max"

        await client.send_message(
            chat_id,
            (
                f"💰 <b>Бюджет</b>\n\n"
                f"✅ Минимум: <b>{_format_price(price_min)}</b>\n\n"
                f"Введите <b>максимальную</b> сумму (в рублях).\n"
                f"Или <b>0</b> для «без максимума»."
            ),
            keyboard=[
                [{"type": "callback", "text": "◀️ Назад", "payload": "wiz_back_budget"}],
                [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
            ],
        )

    elif current == "wiz_budget_custom_max":
        cleaned = text.strip().replace(" ", "").replace(",", "")
        try:
            price_max = int(cleaned)
            if price_max < 0:
                raise ValueError
            if price_max == 0:
                price_max = None
        except ValueError:
            await client.send_message(
                chat_id,
                "⚠️ Введите число. Например: 10000000",
                keyboard=CANCEL_WIZARD_KEYBOARD,
            )
            return

        price_min = state["data"].get("price_min")
        if price_min and price_max and price_max < price_min:
            await client.send_message(
                chat_id,
                f"⚠️ Максимум ({_format_price(price_max)}) меньше минимума ({_format_price(price_min)}).\nВведите корректную сумму.",
                keyboard=[
                    [{"type": "callback", "text": "◀️ Назад", "payload": "wiz_back_budget"}],
                    [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
                ],
            )
            return

        state["data"]["price_max"] = price_max
        state["state"] = "wiz_region"
        await _show_region_step(client, chat_id, state)

    # ── Step 5: Region custom input ──
    elif current == "wiz_region_custom":
        region_name = text.strip()
        if not region_name:
            await client.send_message(
                chat_id,
                "⚠️ Введите название региона:",
                keyboard=CANCEL_WIZARD_KEYBOARD,
            )
            return
        regions = state["data"].get("regions", [])
        if region_name not in regions:
            regions.append(region_name)
        state["data"]["regions"] = regions
        # Show region keyboard again with updated selection
        state["state"] = "wiz_region"
        await _show_region_step(client, chat_id, state)

    # ── Step 7: Exclude keywords ──
    elif current == "wiz_exclude":
        excluded = [kw.strip() for kw in text.replace(";", ",").split(",") if kw.strip()]
        state["data"]["exclude_keywords"] = excluded[:15]
        state["state"] = "wiz_automonitor"
        await _show_automonitor_step(client, chat_id, state)


# ── Wizard step display helpers ──────────────────────────────────

async def _show_budget_step(client: MaxBotClient, chat_id: int, state: dict):
    """Show step 4: budget selection."""
    data = state["data"]
    state["state"] = "wiz_budget"
    await client.send_message(
        chat_id,
        (
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
            f"✅ Слова: <b>{', '.join(data.get('keywords', []))}</b>\n"
            f"✅ Название: <b>{data.get('filter_name', '—')}</b>\n\n"
            f"<b>Шаг 4/9:</b> Укажите бюджет\n\n"
            f"Выберите диапазон или введите вручную:"
        ),
        keyboard=_make_budget_keyboard(),
    )


async def _show_region_step(client: MaxBotClient, chat_id: int, state: dict):
    """Show step 5: region selection."""
    data = state["data"]
    state["state"] = "wiz_region"
    regions = data.get("regions", [])
    await client.send_message(
        chat_id,
        (
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
            f"✅ Слова: <b>{', '.join(data.get('keywords', []))}</b>\n"
            f"✅ Бюджет: <b>{_format_budget_text(data)}</b>\n\n"
            f"<b>Шаг 5/9:</b> Выберите регионы\n\n"
            f"Нажмите на нужные регионы (можно несколько) и «Продолжить»."
        ),
        keyboard=_make_region_keyboard(regions),
    )


async def _show_law_step(client: MaxBotClient, chat_id: int, state: dict):
    """Show step 6: law selection."""
    data = state["data"]
    state["state"] = "wiz_law"
    regions = data.get("regions", [])
    reg_text = ", ".join(regions) if regions else "Вся Россия"
    await client.send_message(
        chat_id,
        (
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
            f"✅ Слова: <b>{', '.join(data.get('keywords', []))}</b>\n"
            f"✅ Бюджет: <b>{_format_budget_text(data)}</b>\n"
            f"✅ Регион: <b>{reg_text}</b>\n\n"
            f"<b>Шаг 6/9:</b> Выберите закон:"
        ),
        keyboard=_make_law_keyboard(),
    )


async def _show_excl_step(client: MaxBotClient, chat_id: int, state: dict):
    """Show step 7: exclude keywords."""
    data = state["data"]
    state["state"] = "wiz_exclude"
    await client.send_message(
        chat_id,
        (
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
            f"✅ Слова: <b>{', '.join(data.get('keywords', []))}</b>\n"
            f"✅ Закон: <b>{data.get('law_type_name', 'Любой')}</b>\n\n"
            f"<b>Шаг 7/9:</b> Слова-исключения\n\n"
            f"Введите слова, которые НЕ должны встречаться в тендерах.\n"
            f"Через запятую. Например: <i>медицин, демонтаж, утилизация</i>\n\n"
            f"Или нажмите «Пропустить»."
        ),
        keyboard=_make_exclusions_keyboard(),
    )


async def _show_automonitor_step(client: MaxBotClient, chat_id: int, state: dict):
    """Show step 8: automonitor toggle."""
    data = state["data"]
    state["state"] = "wiz_automonitor"
    await client.send_message(
        chat_id,
        (
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"<b>Шаг 8/9:</b> Автомониторинг\n\n"
            f"Хотите получать уведомления о новых тендерах по этому фильтру?\n\n"
            f"🔔 <b>Да</b> — бот будет автоматически искать новые тендеры и отправлять уведомления\n"
            f"🔕 <b>Нет</b> — только разовый поиск, без отслеживания"
        ),
        keyboard=_make_automonitor_keyboard(),
    )


async def _show_confirm_step(client: MaxBotClient, chat_id: int, state: dict):
    """Show step 9: confirmation."""
    data = state["data"]
    state["state"] = "wiz_confirm"
    settings_text = _get_current_settings_text(data)
    await client.send_message(
        chat_id,
        (
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"<b>Шаг 9/9:</b> Подтверждение\n\n"
            f"{settings_text}\n\n"
            f"Всё верно? Нажмите «Создать фильтр» или измените настройки."
        ),
        keyboard=_make_confirm_keyboard(),
    )


# ── Wizard callback handlers for each step ───────────────────────

async def _wizard_handle_name_skip(client: MaxBotClient, chat_id: int, user_id: int):
    """Handle 'skip filter name' — use auto-generated name."""
    state = _user_states.get(user_id)
    if not state:
        return
    # filter_name already set to auto-name from step 2
    state["state"] = "wiz_budget"
    await _show_budget_step(client, chat_id, state)


async def _wizard_handle_budget(client: MaxBotClient, chat_id: int, user_id: int, payload: str):
    """Handle budget preset or custom callback."""
    state = _user_states.get(user_id)
    if not state:
        await client.send_message(chat_id, "⚠️ Сессия истекла. Начните заново.", keyboard=MAIN_MENU_KEYBOARD)
        return

    if payload == "bud_custom":
        state["state"] = "wiz_budget_custom_min"
        await client.send_message(
            chat_id,
            (
                "💰 <b>Бюджет — ручной ввод</b>\n\n"
                "Введите <b>минимальную</b> сумму контракта (в рублях).\n\n"
                "Примеры:\n"
                "• 100000 (100 тыс)\n"
                "• 1000000 (1 млн)\n"
                "• 0 (без минимума)"
            ),
            keyboard=[
                [{"type": "callback", "text": "⏭ Пропустить (любой бюджет)", "payload": "bud_any"}],
                [{"type": "callback", "text": "◀️ Назад", "payload": "wiz_back_name"}],
                [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
            ],
        )
        return

    # Match preset
    budget_map = {p["payload"]: (p["min"], p["max"]) for p in BUDGET_PRESETS}
    if payload not in budget_map:
        return

    price_min, price_max = budget_map[payload]
    state["data"]["price_min"] = price_min
    state["data"]["price_max"] = price_max

    # Move to step 5: region
    state["state"] = "wiz_region"
    await _show_region_step(client, chat_id, state)


async def _wizard_handle_region(client: MaxBotClient, chat_id: int, user_id: int, payload: str):
    """Handle region selection callbacks."""
    state = _user_states.get(user_id)
    if not state:
        await client.send_message(chat_id, "⚠️ Сессия истекла. Начните заново.", keyboard=MAIN_MENU_KEYBOARD)
        return

    if payload == "reg_all":
        # Reset to all Russia
        state["data"]["regions"] = []
        state["state"] = "wiz_region"
        await _show_region_step(client, chat_id, state)
        return

    if payload == "reg_continue":
        # Finalize region and go to law step
        state["state"] = "wiz_law"
        await _show_law_step(client, chat_id, state)
        return

    if payload == "reg_custom":
        state["state"] = "wiz_region_custom"
        await client.send_message(
            chat_id,
            "📍 Введите название региона:",
            keyboard=[
                [{"type": "callback", "text": "◀️ Назад", "payload": "wiz_back_budget"}],
                [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
            ],
        )
        return

    # Toggle a popular region
    region_name = REGION_PAYLOAD_MAP.get(payload)
    if not region_name:
        return

    regions = state["data"].get("regions", [])
    if region_name in regions:
        regions.remove(region_name)
    else:
        regions.append(region_name)
    state["data"]["regions"] = regions

    # Refresh region keyboard
    state["state"] = "wiz_region"
    await _show_region_step(client, chat_id, state)


async def _wizard_handle_law(client: MaxBotClient, chat_id: int, user_id: int, payload: str):
    """Handle law selection callback."""
    state = _user_states.get(user_id)
    if not state:
        return

    law_code = payload.replace("law_", "")
    law_info = LAW_TYPES.get(law_code, LAW_TYPES["any"])

    state["data"]["law_type"] = law_info["value"]
    state["data"]["law_type_name"] = law_info["name"]

    # Move to step 7: exclusions
    state["state"] = "wiz_exclude"
    await _show_excl_step(client, chat_id, state)


async def _wizard_handle_excl_skip(client: MaxBotClient, chat_id: int, user_id: int):
    """Handle 'skip exclusions' callback."""
    state = _user_states.get(user_id)
    if not state:
        return

    state["data"]["exclude_keywords"] = []
    state["state"] = "wiz_automonitor"
    await _show_automonitor_step(client, chat_id, state)


async def _wizard_handle_automonitor(client: MaxBotClient, chat_id: int, user_id: int, payload: str):
    """Handle automonitor selection callback."""
    state = _user_states.get(user_id)
    if not state:
        return

    choice = payload.replace("mon_", "")
    state["data"]["automonitor"] = (choice == "yes")

    # Move to step 9: confirmation
    state["state"] = "wiz_confirm"
    await _show_confirm_step(client, chat_id, state)


async def _wizard_show_edit(client: MaxBotClient, chat_id: int, user_id: int):
    """Show edit-settings keyboard."""
    state = _user_states.get(user_id)
    if not state:
        return

    settings_text = _get_current_settings_text(state["data"])
    await client.send_message(
        chat_id,
        (
            f"✏️ <b>Редактирование фильтра</b>\n\n"
            f"{settings_text}\n\n"
            f"Выберите параметр для изменения:"
        ),
        keyboard=_make_edit_keyboard(),
    )


async def _wizard_handle_edit_field(client: MaxBotClient, chat_id: int, user_id: int, payload: str):
    """Handle editing an individual wizard field."""
    state = _user_states.get(user_id)
    if not state:
        return

    field = payload.replace("edit_", "")

    if field == "type":
        state["state"] = "wiz_tender_type"
        selected = state["data"].get("selected_types", [])
        await client.send_message(
            chat_id,
            "📦 <b>Изменить тип закупки</b>\n\nВыберите типы:",
            keyboard=_make_tender_type_keyboard(selected),
        )
    elif field == "keywords":
        state["state"] = "wiz_keywords"
        await client.send_message(
            chat_id,
            "🔑 <b>Изменить ключевые слова</b>\n\nВведите новые через запятую:",
            keyboard=[
                [{"type": "callback", "text": "◀️ К подтверждению", "payload": "wiz_back_confirm"}],
                [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
            ],
        )
    elif field == "name":
        state["state"] = "wiz_filter_name"
        await client.send_message(
            chat_id,
            "📝 <b>Изменить название</b>\n\nВведите новое название:",
            keyboard=[
                [{"type": "callback", "text": "◀️ К подтверждению", "payload": "wiz_back_confirm"}],
                [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
            ],
        )
    elif field == "budget":
        await _show_budget_step(client, chat_id, state)
    elif field == "region":
        await _show_region_step(client, chat_id, state)
    elif field == "law":
        await _show_law_step(client, chat_id, state)
    elif field == "excl":
        await _show_excl_step(client, chat_id, state)
    elif field == "monitor":
        await _show_automonitor_step(client, chat_id, state)


async def _wizard_handle_back(client: MaxBotClient, chat_id: int, user_id: int, payload: str):
    """Handle back navigation during wizard."""
    state = _user_states.get(user_id)
    if not state:
        await client.send_message(chat_id, "⚠️ Сессия истекла.", keyboard=MAIN_MENU_KEYBOARD)
        return

    target = payload.replace("wiz_back_", "")

    if target == "type":
        state["state"] = "wiz_tender_type"
        selected = state["data"].get("selected_types", [])
        await client.send_message(
            chat_id,
            (
                "🎯 <b>Создание фильтра</b>\n\n"
                "<b>Шаг 1/9:</b> Что ищем?\n\n"
                "Выберите один или несколько типов закупки:"
            ),
            keyboard=_make_tender_type_keyboard(selected),
        )

    elif target == "keywords":
        state["state"] = "wiz_keywords"
        await client.send_message(
            chat_id,
            (
                f"🎯 <b>Создание фильтра</b>\n\n"
                f"✅ Тип: <b>{state['data'].get('tender_type_name', 'Любые')}</b>\n\n"
                f"<b>Шаг 2/9:</b> Введите ключевые слова\n\n"
                f"Укажите через запятую, что вы ищете:"
            ),
            keyboard=[
                [{"type": "callback", "text": "◀️ Назад", "payload": "wiz_back_type"}],
                [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
            ],
        )

    elif target == "name":
        state["state"] = "wiz_filter_name"
        keywords = state["data"].get("keywords", [])
        auto_name = ", ".join(keywords[:3])
        if len(keywords) > 3:
            auto_name += f" +{len(keywords) - 3}"
        await client.send_message(
            chat_id,
            (
                f"🎯 <b>Создание фильтра</b>\n\n"
                f"<b>Шаг 3/9:</b> Название фильтра\n\n"
                f"Введите название или нажмите «Пропустить».\n\n"
                f"💡 Авто-название: <code>{auto_name}</code>"
            ),
            keyboard=[
                [{"type": "callback", "text": "⏭ Пропустить (авто-название)", "payload": "name_skip"}],
                [{"type": "callback", "text": "◀️ Назад", "payload": "wiz_back_keywords"}],
                [{"type": "callback", "text": "❌ Отмена", "payload": "wiz_cancel"}],
            ],
        )

    elif target == "budget":
        await _show_budget_step(client, chat_id, state)

    elif target == "region":
        await _show_region_step(client, chat_id, state)

    elif target == "law":
        await _show_law_step(client, chat_id, state)

    elif target == "excl":
        await _show_excl_step(client, chat_id, state)

    elif target == "automonitor":
        await _show_automonitor_step(client, chat_id, state)

    elif target == "confirm":
        await _show_confirm_step(client, chat_id, state)


async def _wizard_confirm(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
):
    """Finalize wizard — create filter in DB."""
    state = _user_states.pop(user_id, None)
    if not state:
        await client.send_message(chat_id, "⚠️ Сессия истекла. Начните заново.", keyboard=MAIN_MENU_KEYBOARD)
        return

    data = state["data"]
    keywords = data.get("keywords", [])
    filter_name = data.get("filter_name", "Мой фильтр")
    tender_types = data.get("tender_types", [])
    exclude_keywords = data.get("exclude_keywords", [])
    automonitor = data.get("automonitor", True)

    if not keywords:
        await client.send_message(chat_id, "⚠️ Ключевые слова не указаны.", keyboard=MAIN_MENU_KEYBOARD)
        return

    try:
        user = await _ensure_user(user_id, username)
        if not user:
            await client.send_message(chat_id, "⚠️ Пользователь не найден.", keyboard=MAIN_MENU_KEYBOARD)
            return

        db = await get_sniper_db()

        # Check filter limit
        existing_filters = await db.get_user_filters(user['id'], active_only=False)
        max_filters = user.get('filters_limit', 3)
        if len(existing_filters) >= max_filters:
            await client.send_message(
                chat_id,
                (
                    f"⚠️ <b>Достигнут лимит фильтров</b>\n\n"
                    f"У вас {len(existing_filters)} из {max_filters} фильтров.\n"
                    f"Удалите ненужные фильтры или улучшите тариф."
                ),
                keyboard=BACK_KEYBOARD,
            )
            return

        filter_id = await db.create_filter(
            user_id=user['id'],
            name=filter_name[:255],
            keywords=keywords,
            exclude_keywords=exclude_keywords,
            price_min=data.get("price_min"),
            price_max=data.get("price_max"),
            regions=data.get("regions") or None,
            tender_types=tender_types if tender_types else None,
            law_type=data.get("law_type"),
            is_active=automonitor,
        )

        logger.info(f"Max bot: created filter {filter_id} for user {user_id}, automonitor={automonitor}")

        # Generate AI intent
        try:
            from tender_sniper.ai_relevance_checker import generate_intent
            ai_intent = await generate_intent(
                filter_name=filter_name,
                keywords=keywords,
                exclude_keywords=exclude_keywords,
            )
            if ai_intent:
                await db.update_filter_intent(filter_id, ai_intent)
        except Exception as e:
            logger.warning(f"Max bot: failed to generate AI intent for filter {filter_id}: {e}")

        # Format confirmation message
        regions = data.get("regions", [])
        reg_text = ", ".join(regions) if regions else "Вся Россия"
        budget_text = _format_budget_text(data)
        law_text = data.get("law_type_name", "Любой")
        excl_text = ", ".join(exclude_keywords) if exclude_keywords else "нет"
        monitor_text = "включён 🔔" if automonitor else "выключен 🔕"
        type_text = data.get("tender_type_name", "Любые")

        result_keyboard = [
            [{"type": "callback", "text": "📋 Мои фильтры", "payload": "my_filters"}],
            [{"type": "callback", "text": "➕ Создать ещё", "payload": "new_filter"}],
            [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
        ]

        await client.send_message(
            chat_id,
            (
                f"✅ <b>Фильтр создан!</b>\n\n"
                f"🆔 ID: #{filter_id}\n"
                f"📝 Название: <b>{filter_name}</b>\n"
                f"📦 Тип: <b>{type_text}</b>\n"
                f"🔑 Слова: <b>{', '.join(keywords)}</b>\n"
                f"💰 Бюджет: <b>{budget_text}</b>\n"
                f"📍 Регион: <b>{reg_text}</b>\n"
                f"⚖️ Закон: <b>{law_text}</b>\n"
                f"🚫 Исключения: <b>{excl_text}</b>\n"
                f"📡 Автомониторинг: <b>{monitor_text}</b>\n\n"
                f"Бот начал мониторинг. Вы получите уведомление, "
                f"как только появится подходящий тендер."
            ),
            keyboard=result_keyboard,
        )

    except Exception as e:
        logger.error(f"Max bot: error creating filter for user {user_id}: {e}", exc_info=True)
        await client.send_message(
            chat_id,
            "⚠️ Произошла ошибка при создании фильтра. Попробуйте позже.",
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
        await client.send_message(chat_id, "⚠️ Пользователь не найден.", keyboard=BACK_KEYBOARD)
        return

    db = await get_sniper_db()
    filters = await db.get_user_filters(user['id'], active_only=False)

    if not filters:
        no_filters_keyboard = [
            [{"type": "callback", "text": "➕ Создать фильтр", "payload": "new_filter"}],
            [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
        ]
        await client.send_message(
            chat_id,
            (
                "📋 <b>Мои фильтры</b>\n\n"
                "У вас пока нет фильтров.\n"
                "Создайте первый фильтр, чтобы получать уведомления о тендерах."
            ),
            keyboard=no_filters_keyboard,
        )
        return

    lines = ["📋 <b>Мои фильтры</b>\n"]

    keyboard = []
    for i, f in enumerate(filters, 1):
        fid = f.get('id')
        status = "🟢" if f.get('is_active') else "🔴"
        name = f.get('name', 'Без названия')
        kw = ", ".join(f.get('keywords', [])[:3]) if f.get('keywords') else "—"

        lines.append(f"{i}. {status} <b>{name}</b>\n   🔑 {kw}")

        keyboard.append([
            {"type": "callback", "text": f"{status} {i}. {name[:30]}", "payload": f"fv_{fid}"},
        ])

    keyboard.append([{"type": "callback", "text": "➕ Создать фильтр", "payload": "new_filter"}])
    keyboard.append([{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}])

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
        await client.send_message(chat_id, "⚠️ Фильтр не найден.", keyboard=BACK_KEYBOARD)
        return

    user = await _ensure_user(user_id, username)
    if not user or filter_data.get('user_id') != user['id']:
        await client.send_message(chat_id, "⚠️ Фильтр не найден.", keyboard=BACK_KEYBOARD)
        return

    name = filter_data.get('name', 'Без названия')
    keywords = filter_data.get('keywords', [])
    is_active = filter_data.get('is_active', False)
    price_min = filter_data.get('price_min')
    price_max = filter_data.get('price_max')
    regions = filter_data.get('regions', [])
    exclude_kw = filter_data.get('exclude_keywords', [])
    law_type = filter_data.get('law_type', '')

    status_icon = "🟢" if is_active else "🔴"
    status_text = "Активен" if is_active else "Приостановлен"
    kw_text = ", ".join(keywords) if keywords else "—"
    budget_text = _format_budget_text({"price_min": price_min, "price_max": price_max})
    reg_text = ", ".join(regions) if regions else "Вся Россия"
    excl_text = ", ".join(exclude_kw) if exclude_kw else "нет"
    law_text = law_type if law_type else "Любой"

    toggle_text = "⏸ Приостановить" if is_active else "▶️ Возобновить"

    keyboard = [
        [{"type": "callback", "text": toggle_text, "payload": f"ft_{filter_id}"}],
        [{"type": "callback", "text": "🗑 Удалить", "payload": f"fd_{filter_id}"}],
        [{"type": "callback", "text": "◀️ Назад к фильтрам", "payload": "my_filters"}],
        [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
    ]

    await client.send_message(
        chat_id,
        (
            f"📄 <b>Фильтр #{filter_id}</b>\n\n"
            f"📝 Название: <b>{name}</b>\n"
            f"{status_icon} Статус: <b>{status_text}</b>\n"
            f"🔑 Ключевые слова: <b>{kw_text}</b>\n"
            f"💰 Бюджет: <b>{budget_text}</b>\n"
            f"📍 Регион: <b>{reg_text}</b>\n"
            f"⚖️ Закон: <b>{law_text}</b>\n"
            f"🚫 Исключения: <b>{excl_text}</b>"
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
            await client.send_message(chat_id, "⚠️ Фильтр не найден.", keyboard=BACK_KEYBOARD)
            return

        user = await _ensure_user(user_id, username)
        if not user or filter_data.get('user_id') != user['id']:
            await client.send_message(chat_id, "⚠️ Фильтр не найден.", keyboard=BACK_KEYBOARD)
            return

        new_status = not filter_data.get('is_active', True)
        await db.update_filter(filter_id=filter_id, is_active=new_status)

        status_icon = "🟢" if new_status else "🔴"
        status_text = "возобновлён" if new_status else "приостановлен"
        logger.info(f"Max bot: filter {filter_id} toggled to {new_status} by user {user_id}")

        await client.send_message(
            chat_id,
            f"{status_icon} Фильтр <b>#{filter_id}</b> {status_text}.",
            keyboard=[
                [{"type": "callback", "text": "📄 Назад к фильтру", "payload": f"fv_{filter_id}"}],
                [{"type": "callback", "text": "📋 Мои фильтры", "payload": "my_filters"}],
            ],
        )

    except Exception as e:
        logger.error(f"Max bot: error toggling filter {filter_id}: {e}", exc_info=True)
        await client.send_message(chat_id, "⚠️ Произошла ошибка.", keyboard=BACK_KEYBOARD)


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
        [{"type": "callback", "text": "🗑 Да, удалить", "payload": f"fdc_{filter_id}"}],
        [{"type": "callback", "text": "◀️ Нет, отмена", "payload": f"fdn_{filter_id}"}],
    ]

    await client.send_message(
        chat_id,
        f"⚠️ Вы уверены, что хотите удалить фильтр <b>#{filter_id}</b>?\n\nЭто действие нельзя отменить.",
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
            await client.send_message(chat_id, "⚠️ Фильтр не найден.", keyboard=BACK_KEYBOARD)
            return

        user = await _ensure_user(user_id, username)
        if not user or filter_data.get('user_id') != user['id']:
            await client.send_message(chat_id, "⚠️ Фильтр не найден.", keyboard=BACK_KEYBOARD)
            return

        await db.delete_filter(filter_id)
        logger.info(f"Max bot: filter {filter_id} deleted by user {user_id}")

        await client.send_message(
            chat_id,
            f"🗑 Фильтр <b>#{filter_id}</b> удалён.",
            keyboard=[
                [{"type": "callback", "text": "📋 Мои фильтры", "payload": "my_filters"}],
                [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
            ],
        )

    except Exception as e:
        logger.error(f"Max bot: error deleting filter {filter_id}: {e}", exc_info=True)
        await client.send_message(chat_id, "⚠️ Произошла ошибка.", keyboard=BACK_KEYBOARD)


# ══════════════════════════════════════════════════════════════════
# SUBSCRIPTION
# ══════════════════════════════════════════════════════════════════

SUBSCRIPTION_TIERS = {
    'trial': {
        'name': '🆓 Пробный период',
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
        'name': '⭐ Basic',
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
        'name': '💎 Premium',
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
        await client.send_message(chat_id, "⚠️ Пользователь не найден.", keyboard=BACK_KEYBOARD)
        return

    db = await get_sniper_db()
    user_full = await db.get_user_subscription_info(user_id)

    tier = user_full.get('subscription_tier', 'trial') if user_full else 'trial'
    expires_at = user_full.get('trial_expires_at') if user_full else None
    filters_limit = user_full.get('filters_limit', 3) if user_full else 3
    notifications_limit = user_full.get('notifications_limit', 20) if user_full else 20

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
            f"💳 <b>Ваша подписка</b>\n\n"
            f"Тариф: <b>{tier_info['name']}</b>\n"
            f"📅 Действует до: <b>{expires_display}</b>\n"
            f"⏳ Осталось дней: <b>{days_remaining}</b>\n\n"
            f"<b>📊 Лимиты:</b>\n"
            f"• Фильтров: {filters_limit}\n"
            f"• Уведомлений/день: {notifications_limit}\n\n"
            f"<b>🎁 Возможности:</b>\n"
        )
        for feature in tier_info['features']:
            text += f"• {feature}\n"

        if is_trial:
            text += f"\n<i>⏳ Пробный период закончится через {days_remaining} дней.</i>"
    else:
        text = (
            "💳 <b>Подписка</b>\n\n"
            "У вас нет активной подписки.\n\n"
            "Активируйте пробный период на 14 дней бесплатно или выберите тариф."
        )

    keyboard = []
    if not is_active:
        keyboard.append([{"type": "callback", "text": "🆓 Активировать Trial (14 дней)", "payload": "sub_trial"}])
    keyboard.append([{"type": "callback", "text": "📊 Посмотреть тарифы", "payload": "sub_tiers"}])
    keyboard.append([{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}])

    await client.send_message(chat_id, text, keyboard=keyboard)


async def _show_subscription_tiers(client: MaxBotClient, chat_id: int):
    """Show available subscription tiers."""
    text = "📊 <b>Тарифные планы</b>\n\n"

    for tier_id, tier_info in SUBSCRIPTION_TIERS.items():
        if tier_id == 'trial':
            continue
        price_text = f"{tier_info['price']} руб./мес"
        text += f"<b>{tier_info['name']}</b> — {price_text}\n"
        for feature in tier_info['features']:
            text += f"  • {feature}\n"
        text += "\n"

    text += (
        "<i>💳 Оплата подписки доступна через Telegram-бота @TenderAI111_bot.\n"
        "Подписка действует для обоих ботов (Telegram и Max).</i>"
    )

    keyboard = [
        [{"type": "callback", "text": "◀️ Назад", "payload": "sub"}],
        [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
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
        await client.send_message(chat_id, "⚠️ Пользователь не найден.", keyboard=BACK_KEYBOARD)
        return

    db = await get_sniper_db()
    existing_sub = await db.get_subscription(user['id'])

    if existing_sub:
        await client.send_message(
            chat_id,
            (
                "⚠️ <b>Пробный период уже был активирован</b>\n\n"
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

    features_text = "\n".join([f"• {f}" for f in trial_config['features']])

    await client.send_message(
        chat_id,
        (
            f"✅ <b>Пробный период активирован!</b>\n\n"
            f"Тариф: {trial_config['name']}\n"
            f"📅 Срок: {trial_config['days']} дней\n\n"
            f"<b>🎁 Доступные возможности:</b>\n"
            f"{features_text}\n\n"
            f"Теперь вы можете создавать фильтры и получать уведомления!"
        ),
        keyboard=[
            [{"type": "callback", "text": "➕ Создать фильтр", "payload": "new_filter"}],
            [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
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

        if price:
            price_text = f"{_format_price(price)}"
        else:
            price_text = "не указана"

        text = "🔔 <b>Новый тендер</b>\n\n"

        if match_info:
            filter_name = match_info.get('filter_name', '')
            relevance = match_info.get('relevance_score')
            if filter_name:
                text += f"📋 Фильтр: <b>{filter_name}</b>\n"
            if relevance is not None:
                text += f"🎯 Релевантность: <b>{relevance}%</b>\n"
            text += "\n"

        text += (
            f"📝 <b>{name[:200]}</b>\n\n"
            f"💰 Цена: <b>{price_text}</b>\n"
            f"🏢 Заказчик: {customer[:100]}\n"
            f"📍 Регион: {region}\n"
            f"📅 Подача до: {deadline}\n"
        )

        if law_type:
            text += f"⚖️ Закон: {law_type}\n"

        keyboard = []
        if url:
            keyboard.append([{"type": "callback", "text": "🔗 Подробнее", "payload": "menu"}])
            text += f"\n<a href=\"{url}\">🔗 Открыть на zakupki.gov.ru</a>"

        keyboard.append([{"type": "callback", "text": "📋 Мои фильтры", "payload": "my_filters"}])

        await client.send_message(chat_id, text, keyboard=keyboard)

        logger.info(f"Max bot: notification sent to chat {chat_id}: {name[:50]}")

    except Exception as e:
        logger.error(f"Max bot: error sending notification to chat {chat_id}: {e}", exc_info=True)
