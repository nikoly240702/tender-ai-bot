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
import re
from datetime import datetime
from typing import Dict, Any, Optional, List

from bot_max.client import MaxBotClient
from tender_sniper.database import get_sniper_db
from bot.utils.ai_access import can_use_ai

logger = logging.getLogger(__name__)

# ── Filter Templates (from onboarding) ──────────────────────────
FILTER_TEMPLATES = {
    "it": {
        "name": "IT и компьютеры",
        "emoji": "💻",
        "description": "Компьютерная техника, ПО, IT-услуги",
        "keywords": ["компьютер", "ноутбук", "сервер", "программное обеспечение", "IT", "информационные технологии"],
        "price_min": 100000,
        "price_max": 10000000,
    },
    "construction": {
        "name": "Строительство",
        "emoji": "🏗️",
        "description": "Строительные работы, материалы, ремонт",
        "keywords": ["строительство", "ремонт", "строительные работы", "капитальный ремонт", "реконструкция"],
        "price_min": 500000,
        "price_max": 50000000,
    },
    "office": {
        "name": "Канцелярия",
        "emoji": "📎",
        "description": "Канцтовары, бумага, офисные принадлежности",
        "keywords": ["канцелярские товары", "бумага", "канцтовары", "офисные принадлежности"],
        "price_min": 50000,
        "price_max": 2000000,
    },
    "food": {
        "name": "Продукты питания",
        "emoji": "🍎",
        "description": "Продовольствие, питание, кейтеринг",
        "keywords": ["продукты питания", "продовольствие", "питание", "пищевые продукты"],
        "price_min": 100000,
        "price_max": 5000000,
    },
    "cleaning": {
        "name": "Клининг",
        "emoji": "🧹",
        "description": "Уборка, клининговые услуги",
        "keywords": ["уборка", "клининг", "клининговые услуги", "содержание помещений"],
        "price_min": 100000,
        "price_max": 5000000,
    },
    "security": {
        "name": "Охрана",
        "emoji": "🔒",
        "description": "Охранные услуги, безопасность",
        "keywords": ["охрана", "охранные услуги", "безопасность", "пропускной режим"],
        "price_min": 200000,
        "price_max": 10000000,
    },
    "medical": {
        "name": "Медицина",
        "emoji": "🏥",
        "description": "Медоборудование, медикаменты, медуслуги",
        "keywords": ["медицинское оборудование", "медикаменты", "лекарственные средства", "медицинские изделия"],
        "price_min": 100000,
        "price_max": 20000000,
    },
    "furniture": {
        "name": "Мебель",
        "emoji": "🪑",
        "description": "Офисная и специальная мебель",
        "keywords": ["мебель", "офисная мебель", "мебель для школ", "учебная мебель"],
        "price_min": 100000,
        "price_max": 5000000,
    },
}

# ── In-memory favorites (user_id -> set of tender_numbers) ──────
_user_favorites: Dict[int, set] = {}

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
    [
        {"type": "callback", "text": "🤖 Tender-GPT", "payload": "gpt"},
        {"type": "callback", "text": "➕ Создать фильтр", "payload": "new_filter"},
    ],
    [
        {"type": "callback", "text": "📋 Мои фильтры", "payload": "my_filters"},
        {"type": "callback", "text": "📊 Все мои тендеры", "payload": "all_tenders"},
    ],
    [
        {"type": "callback", "text": "⭐ Избранное", "payload": "favorites"},
        {"type": "callback", "text": "📈 Статистика", "payload": "stats"},
    ],
    [
        {"type": "callback", "text": "⏸ Пауза мониторинга", "payload": "pause_mon"},
        {"type": "callback", "text": "🔬 AI Анализ", "payload": "ai_analyze"},
    ],
    [
        {"type": "callback", "text": "🔍 Поиск тендеров", "payload": "man_search"},
    ],
    [
        {"type": "callback", "text": "💳 Подписка", "payload": "sub"},
        {"type": "callback", "text": "🔗 Связать с Telegram", "payload": "link_tg"},
    ],
    [
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

async def _ensure_user(user_id: int, username: str = None, first_name: str = None) -> dict:
    """Create or update user in DB, return user dict. Mark as Max platform."""
    db = await get_sniper_db()
    max_username = f"max_{username}" if username else f"max_{user_id}"
    # Check if user already exists — don't overwrite their subscription tier
    user = await db.get_user_by_telegram_id(user_id)
    if not user:
        await db.create_or_update_user(
            telegram_id=user_id,
            username=max_username,
            first_name=first_name,
            subscription_tier='trial',
        )
        # Override default 14-day trial with new 7-day trial for Max users
        try:
            from datetime import datetime, timedelta
            from database import DatabaseSession, SniperUser as SniperUserModel
            from sqlalchemy import update as sa_update
            now = datetime.utcnow()
            async with DatabaseSession() as session:
                await session.execute(
                    sa_update(SniperUserModel)
                    .where(SniperUserModel.telegram_id == user_id)
                    .values(
                        trial_started_at=now,
                        trial_expires_at=now + timedelta(days=7),
                        filters_limit=3,
                        notifications_limit=50,
                    )
                )
                await session.commit()
        except Exception as e:
            logger.warning(f"Max bot: failed to set 7-day trial for {user_id}: {e}")
        user = await db.get_user_by_telegram_id(user_id)
    # Mark platform as Max (for notification routing)
    if user:
        user_data = user.get('data') or {}
        if user_data.get('platform') != 'max':
            user_data['platform'] = 'max'
            try:
                await db.update_user_json_data(user['id'], user_data)
            except Exception:
                pass
    return user


async def _get_linked_user_id(max_user: dict) -> int:
    """Get linked Telegram user_id if accounts are linked via email, otherwise return Max user_id."""
    user_data = max_user.get('data') or {}
    linked_id = user_data.get('linked_telegram_user_id')
    if linked_id:
        return linked_id
    return max_user['id']


async def _link_account_by_email(max_user_id: int, email: str) -> dict:
    """Try to link Max account to Telegram account by email. Returns result dict."""
    from database import DatabaseSession, SniperUser as SniperUserModel
    from sqlalchemy import select

    async with DatabaseSession() as session:
        # Find Telegram user with this email
        tg_user = await session.scalar(
            select(SniperUserModel).where(
                SniperUserModel.email == email,
                SniperUserModel.id != max_user_id,
            )
        )
        if not tg_user:
            return {'success': False, 'reason': 'no_telegram_user'}

        # Check it's actually a Telegram user (not another Max user)
        tg_data = {}
        if tg_user.data:
            import json
            tg_data = json.loads(tg_user.data) if isinstance(tg_user.data, str) else tg_user.data
        if tg_data.get('platform') == 'max':
            return {'success': False, 'reason': 'not_telegram_user'}

        # Link: save linked_telegram_user_id in Max user's data
        max_user_obj = await session.scalar(
            select(SniperUserModel).where(SniperUserModel.id == max_user_id)
        )
        if max_user_obj:
            import json
            max_data = {}
            if max_user_obj.data:
                max_data = json.loads(max_user_obj.data) if isinstance(max_user_obj.data, str) else max_user_obj.data
            max_data['linked_telegram_user_id'] = tg_user.id
            max_data['linked_email'] = email
            max_user_obj.data = json.dumps(max_data, ensure_ascii=False)

            # Also save email on Max user
            max_user_obj.email = email
            await session.commit()

        return {
            'success': True,
            'telegram_user_id': tg_user.id,
            'telegram_username': tg_user.username,
        }


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
    """User pressed 'Start' — send welcome message or onboarding."""
    logger.info(f"Max bot: bot_started from user {user_id} in chat {chat_id}")
    user = await _ensure_user(user_id, username)
    _gpt_active_chats.discard(chat_id)
    _user_states.pop(user_id, None)

    # Check if new user (no filters) — show onboarding with templates
    try:
        db = await get_sniper_db()
        filters = await db.get_user_filters(user['id'], active_only=False)
        if not filters:
            await _show_onboarding(client, chat_id, user_id)
            return
    except Exception as e:
        logger.warning(f"Max bot: onboarding check failed: {e}")

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
        current_state = state.get("state", "")

        # AI Analysis — waiting for tender number
        if current_state == "analyze_waiting":
            await _handle_analyze_input(client, chat_id, user_id, username, text)
            return

        # Manual Search — waiting for keywords
        if current_state == "search_waiting":
            await _handle_search_input(client, chat_id, user_id, username, text)
            return

        # Payment email input
        if current_state == "pay_email":
            await _handle_payment_email(client, chat_id, user_id, text, state)
            return

        # Link account email input
        if current_state == "link_email":
            await _handle_link_email(client, chat_id, user_id, text, state)
            return

        # Filter editing states
        if current_state.startswith("fedit_"):
            await _handle_filter_edit_text(client, chat_id, user_id, username, text, state)
            return

        # Wizard states
        await _handle_wizard_text(client, chat_id, user_id, username, text, state)
        return

    # Default: show main menu
    await client.send_message(chat_id, WELCOME_TEXT, keyboard=MAIN_MENU_KEYBOARD)


_recent_callbacks: Dict[str, float] = {}  # callback_id -> timestamp for dedup


async def handle_callback(client: MaxBotClient, update: Dict[str, Any]):
    """Handle inline keyboard button press."""
    import time
    callback = update.get("callback", {})
    callback_id = callback.get("callback_id")
    payload = callback.get("payload")
    user = callback.get("user", {})
    user_id = user.get("user_id")
    username = user.get("username")

    # Deduplicate rapid clicks (same callback_id within 2 seconds)
    if callback_id:
        now = time.time()
        if callback_id in _recent_callbacks and now - _recent_callbacks[callback_id] < 2.0:
            logger.debug(f"Max bot: duplicate callback {callback_id}, skipping")
            return
        _recent_callbacks[callback_id] = now
        # Cleanup old entries
        if len(_recent_callbacks) > 100:
            cutoff = now - 10
            _recent_callbacks.clear()
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

    elif payload == "link_tg":
        user = await _ensure_user(user_id, username)
        if user:
            user_data = user.get('data') or {}
            if user_data.get('linked_telegram_user_id'):
                linked_email = user_data.get('linked_email', '?')
                await client.send_message(
                    chat_id,
                    f"🔗 Аккаунт уже привязан к Telegram (email: <code>{linked_email}</code>).\n\n"
                    f"Фильтры и подписка общие.",
                    keyboard=BACK_KEYBOARD
                )
            else:
                _user_states[user_id] = {"state": "link_email", "chat_id": chat_id}
                await client.send_message(
                    chat_id,
                    "🔗 <b>Привязка к Telegram-аккаунту</b>\n\n"
                    "Введите email, который вы указали в Telegram-боте @TenderAI111_bot "
                    "(через команду /email).\n\n"
                    "Это позволит:\n"
                    "• Видеть фильтры из Telegram\n"
                    "• Получать уведомления в обоих мессенджерах\n"
                    "• Общая подписка",
                    keyboard=[[{"type": "callback", "text": "❌ Отмена", "payload": "menu"}]]
                )

    elif payload.startswith("sub_pay_"):
        tier_name = payload.replace("sub_pay_", "")
        _user_states[user_id] = {"state": "pay_email", "tier": tier_name, "chat_id": chat_id}
        await client.send_message(
            chat_id,
            "📧 Введите ваш email для получения чека об оплате:",
            keyboard=[[{"type": "callback", "text": "❌ Отмена", "payload": "sub_tiers"}]]
        )

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

    # ── Onboarding templates ──
    elif payload.startswith("tpl_"):
        await _handle_template_select(client, chat_id, user_id, username, payload)

    elif payload == "tpl_custom":
        await _wizard_start(client, chat_id, user_id, username)

    # ── All My Tenders ──
    elif payload == "all_tenders":
        await _show_all_tenders(client, chat_id, user_id, username)

    elif payload.startswith("td_"):
        await _show_tender_detail(client, chat_id, user_id, username, payload)

    # ── Favorites ──
    elif payload == "favorites":
        await _show_favorites(client, chat_id, user_id, username)

    elif payload.startswith("fa_"):
        await _add_favorite(client, chat_id, user_id, payload)

    elif payload.startswith("fr_"):
        await _remove_favorite(client, chat_id, user_id, payload)

    # ── Statistics ──
    elif payload == "stats":
        await _show_stats(client, chat_id, user_id, username)

    # ── Monitoring Pause/Resume ──
    elif payload == "pause_mon":
        await _toggle_monitoring(client, chat_id, user_id, username, pause=True)

    elif payload == "resume_mon":
        await _toggle_monitoring(client, chat_id, user_id, username, pause=False)

    # ── AI Analysis ──
    elif payload == "ai_analyze":
        await _start_ai_analyze(client, chat_id, user_id)

    elif payload.startswith("aia_"):
        # Quick analyze from tender card: aia_{tender_number}
        tender_num = payload.replace("aia_", "")
        await _do_ai_analyze(client, chat_id, user_id, username, tender_num)

    # ── Manual Search ──
    elif payload == "man_search":
        await _start_manual_search(client, chat_id, user_id)

    # ── Filter Editing ──
    elif payload.startswith("fe_"):
        await _show_filter_edit_menu(client, chat_id, user_id, username, payload)

    elif payload.startswith("fen_"):
        await _start_filter_edit(client, chat_id, user_id, "name", payload.replace("fen_", ""))

    elif payload.startswith("fek_"):
        await _start_filter_edit(client, chat_id, user_id, "keywords", payload.replace("fek_", ""))

    elif payload.startswith("fepn_"):
        await _start_filter_edit(client, chat_id, user_id, "price_min", payload.replace("fepn_", ""))

    elif payload.startswith("fepx_"):
        await _start_filter_edit(client, chat_id, user_id, "price_max", payload.replace("fepx_", ""))

    elif payload.startswith("fer_"):
        await _start_filter_edit(client, chat_id, user_id, "regions", payload.replace("fer_", ""))

    # ── GPT from tender card ──
    elif payload.startswith("gpt_"):
        tender_num = payload.replace("gpt_", "")
        _gpt_active_chats.add(chat_id)
        _user_states.pop(user_id, None)
        service = await _get_gpt_service()
        greeting = await service.get_greeting()
        await client.send_message(
            chat_id,
            f"🤖 <b>Tender-GPT</b>\n\n{greeting}\n\n💡 Можете спросить о тендере {tender_num}",
            keyboard=EXIT_GPT_KEYBOARD,
        )

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
    linked_id = await _get_linked_user_id(user)
    filters = await db.get_user_filters(linked_id, active_only=False)

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
    if not user:
        await client.send_message(chat_id, "⚠️ Пользователь не найден.", keyboard=BACK_KEYBOARD)
        return
    linked_id = await _get_linked_user_id(user)
    if filter_data.get('user_id') != linked_id:
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
        [{"type": "callback", "text": "✏️ Редактировать", "payload": f"fe_{filter_id}"}],
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
        if not user:
            await client.send_message(chat_id, "⚠️ Пользователь не найден.", keyboard=BACK_KEYBOARD)
            return
        linked_id = await _get_linked_user_id(user)
        if filter_data.get('user_id') != linked_id:
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
        linked_id = await _get_linked_user_id(user) if user else None
        if not user or filter_data.get('user_id') != linked_id:
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
        'name': 'Пробный период',
        'emoji': '🎁',
        'price': 0,
        'days': 7,
        'max_filters': 3,
        'max_notifications_per_day': 50,
        'features': [
            '✅ До 3 фильтров',
            '✅ До 50 уведомлений в день',
            '✅ Поиск по всем тендерам',
            '⏱ 7 дней бесплатно',
        ],
    },
    'starter': {
        'name': 'Starter',
        'emoji': '🚀',
        'price': 499,
        'days': 30,
        'max_filters': 5,
        'max_notifications_per_day': 50,
        'features': [
            '✅ До 5 фильтров',
            '✅ До 50 уведомлений в день',
            '✅ Быстрые уведомления о новых тендерах',
            'ℹ️ Без AI-анализа (см. Pro)',
        ],
    },
    'pro': {
        'name': 'Pro',
        'emoji': '⭐',
        'price': 1490,
        'days': 30,
        'max_filters': 15,
        'max_notifications_per_day': 9999,
        'features': [
            '✅ До 15 фильтров',
            '✅ Безлимит уведомлений',
            '✅ AI-анализ: 500 в месяц',
            '✅ Tender-GPT: 50 сообщений в месяц',
        ],
    },
    'premium': {
        'name': 'Business',
        'emoji': '💎',
        'price': 2990,
        'days': 30,
        'max_filters': 30,
        'max_notifications_per_day': 9999,
        'features': [
            '✅ До 30 фильтров',
            '✅ Безлимит уведомлений',
            '✅ Безлимитный AI-анализ',
            '✅ Tender-GPT: 200 сообщений в месяц',
            '✅ Приоритетная поддержка',
        ],
    },
}


async def _handle_payment_email(client: MaxBotClient, chat_id: int, user_id: int, text: str, state: dict):
    """Handle email input for payment flow."""
    import re
    email = text.strip()
    if not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        await client.send_message(
            chat_id,
            "❌ Некорректный email. Попробуйте ещё раз:",
            keyboard=[[{"type": "callback", "text": "❌ Отмена", "payload": "sub_tiers"}]]
        )
        return

    tier_name = state.get("tier", "starter")
    _user_states.pop(user_id, None)

    # Get tier info
    tier_info = SUBSCRIPTION_TIERS.get(tier_name)
    if not tier_info:
        await client.send_message(chat_id, "❌ Тариф не найден.", keyboard=BACK_KEYBOARD)
        return

    # Check first payment discount
    is_first = False
    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(user_id)
        if user:
            user_data = user.get('data') or {}
            is_first = not user_data.get('has_paid_before', False)
    except Exception:
        pass

    # Calculate price
    from bot.handlers.subscriptions import calculate_price
    price_info = calculate_price(tier_name, 1, is_first_payment=is_first)

    # Create YooKassa payment
    try:
        from tender_sniper.payments import get_yookassa_client
        yoo_client = get_yookassa_client()

        if not yoo_client.is_configured:
            await client.send_message(chat_id, "🚧 Платежная система временно недоступна.", keyboard=BACK_KEYBOARD)
            return

        result = yoo_client.create_payment(
            telegram_id=user_id,
            tier=tier_name,
            amount=price_info['final_price'],
            days=price_info['days'],
            description=f"Подписка {tier_info['name']} на {price_info['label']}",
            customer_email=email
        )

        if 'error' in result:
            await client.send_message(chat_id, f"❌ Ошибка: {result['error']}", keyboard=BACK_KEYBOARD)
            return

        if price_info['has_discount']:
            price_text = f"{price_info['full_price']} руб. → <b>{price_info['final_price']} руб.</b>"
        else:
            price_text = f"<b>{price_info['final_price']} руб.</b>"

        keyboard = [
            [{"type": "link", "text": f"💳 Оплатить {price_info['final_price']} руб.", "url": result['url']}],
            [{"type": "callback", "text": "◀️ Назад", "payload": "sub_tiers"}],
        ]

        await client.send_message(
            chat_id,
            f"💳 <b>Оплата тарифа {tier_info['name']}</b>\n\n"
            f"📅 Период: <b>{price_info['label']}</b>\n"
            f"💰 Сумма: {price_text}\n"
            f"📧 Чек: <code>{email}</code>\n\n"
            f"Нажмите кнопку ниже для перехода к оплате.\n"
            f"После оплаты подписка активируется автоматически.\n\n"
            f"⏳ <i>Ссылка действительна 15 минут</i>",
            keyboard=keyboard
        )

        logger.info(f"Max bot: payment created for user {user_id}, tier {tier_name}, amount {price_info['final_price']}")

    except Exception as e:
        logger.error(f"Max bot payment error: {e}", exc_info=True)
        await client.send_message(chat_id, f"❌ Ошибка: {str(e)}", keyboard=BACK_KEYBOARD)


async def _handle_link_email(client: MaxBotClient, chat_id: int, user_id: int, text: str, state: dict):
    """Handle email input for account linking."""
    import re
    email = text.strip()
    if not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        await client.send_message(
            chat_id,
            "❌ Некорректный email. Попробуйте ещё раз:",
            keyboard=[[{"type": "callback", "text": "❌ Отмена", "payload": "menu"}]]
        )
        return

    _user_states.pop(user_id, None)

    user = await _ensure_user(user_id)
    if not user:
        await client.send_message(chat_id, "⚠️ Ошибка.", keyboard=BACK_KEYBOARD)
        return

    result = await _link_account_by_email(user['id'], email)

    if result['success']:
        await client.send_message(
            chat_id,
            f"✅ <b>Аккаунт привязан!</b>\n\n"
            f"Связан с Telegram: @{result.get('telegram_username', '—')}\n\n"
            f"Теперь вы видите фильтры из Telegram и получаете уведомления в обоих мессенджерах.",
            keyboard=MAIN_MENU_KEYBOARD
        )
        logger.info(f"Max user {user_id} linked to Telegram user {result['telegram_user_id']} via email {email}")
    else:
        reason = result.get('reason', 'unknown')
        if reason == 'no_telegram_user':
            msg = (
                f"❌ Пользователь с email <code>{email}</code> не найден в Telegram-боте.\n\n"
                f"Сначала укажите email в @TenderAI111_bot командой:\n"
                f"<code>/email {email}</code>"
            )
        else:
            msg = "❌ Не удалось привязать аккаунт."
        await client.send_message(chat_id, msg, keyboard=BACK_KEYBOARD)


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

    is_active = (tier in ['starter', 'pro', 'premium', 'basic'] and (not expires_at or days_remaining > 0)) or (tier == 'trial' and days_remaining > 0)
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
            "Активируйте пробный период на 7 дней бесплатно или выберите тариф."
        )

    keyboard = []
    if not is_active:
        keyboard.append([{"type": "callback", "text": "🆓 Активировать Trial (7 дней)", "payload": "sub_trial"}])
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

    text += "<i>Выберите тариф для оплаты:</i>"

    keyboard = []
    for tier_id, info in SUBSCRIPTION_TIERS.items():
        if tier_id == 'trial':
            continue
        keyboard.append([{"type": "callback", "text": f"{info['emoji']} {info['name']} — {info['price']} руб.", "payload": f"sub_pay_{tier_id}"}])
    keyboard.append([{"type": "callback", "text": "◀️ Назад", "payload": "sub"}])

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

        # Extract tender number for action buttons
        tender_number = tender.get('number', '')
        if not tender_number and url:
            m = re.search(r'regNumber=(\d+)', url)
            if m:
                tender_number = m.group(1)

        keyboard = []
        if url:
            text += f"\n<a href=\"{url}\">🔗 Открыть на zakupki.gov.ru</a>"

        # Action buttons on tender card
        action_row = []
        if tender_number:
            action_row.append({"type": "callback", "text": "🔬 AI Анализ", "payload": f"aia_{tender_number[:20]}"})
            action_row.append({"type": "callback", "text": "⭐ В избранное", "payload": f"fa_{tender_number[:20]}"})
        if action_row:
            keyboard.append(action_row)

        if tender_number:
            keyboard.append([{"type": "callback", "text": "🤖 Спросить GPT", "payload": f"gpt_{tender_number[:20]}"}])

        keyboard.append([{"type": "callback", "text": "📋 Мои фильтры", "payload": "my_filters"}])

        await client.send_message(chat_id, text, keyboard=keyboard)

        logger.info(f"Max bot: notification sent to chat {chat_id}: {name[:50]}")

    except Exception as e:
        logger.error(f"Max bot: error sending notification to chat {chat_id}: {e}", exc_info=True)


# ══════════════════════════════════════════════════════════════════
# ONBOARDING — QUICK TEMPLATES
# ══════════════════════════════════════════════════════════════════

async def _show_onboarding(client: MaxBotClient, chat_id: int, user_id: int):
    """Show onboarding template selection for new users."""
    text = (
        "👋 <b>Добро пожаловать в Tender Sniper!</b>\n\n"
        "Я помогу вам находить тендеры на zakupki.gov.ru автоматически.\n\n"
        "<b>Как это работает:</b>\n"
        "1️⃣ Вы создаёте фильтр с критериями\n"
        "2️⃣ Бот мониторит 15,000+ тендеров ежедневно\n"
        "3️⃣ Получаете уведомления о подходящих\n\n"
        "🎁 <b>У вас 14 дней бесплатного доступа!</b>\n\n"
        "🚀 Выберите вашу нишу для быстрого старта:"
    )

    keyboard = []
    templates_list = list(FILTER_TEMPLATES.items())
    for i in range(0, len(templates_list), 2):
        row = []
        for key, tpl in templates_list[i:i + 2]:
            row.append({
                "type": "callback",
                "text": f"{tpl['emoji']} {tpl['name']}",
                "payload": f"tpl_{key}",
            })
        keyboard.append(row)

    keyboard.append([{"type": "callback", "text": "🎯 Своя ниша", "payload": "tpl_custom"}])

    await client.send_message(chat_id, text, keyboard=keyboard)


async def _handle_template_select(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
    payload: str,
):
    """Create filter from template selection."""
    template_key = payload.replace("tpl_", "")
    template = FILTER_TEMPLATES.get(template_key)

    if not template:
        await client.send_message(chat_id, "⚠️ Шаблон не найден.", keyboard=MAIN_MENU_KEYBOARD)
        return

    try:
        user = await _ensure_user(user_id, username)
        if not user:
            await client.send_message(chat_id, "⚠️ Пользователь не найден.", keyboard=MAIN_MENU_KEYBOARD)
            return

        db = await get_sniper_db()

        filter_id = await db.create_filter(
            user_id=user['id'],
            name=f"{template['emoji']} {template['name']}",
            keywords=template['keywords'],
            price_min=template['price_min'],
            price_max=template['price_max'],
            is_active=True,
        )

        # Generate AI intent
        try:
            from tender_sniper.ai_relevance_checker import generate_intent
            ai_intent = await generate_intent(
                filter_name=f"{template['emoji']} {template['name']}",
                keywords=template['keywords'],
                exclude_keywords=[],
            )
            if ai_intent:
                await db.update_filter_intent(filter_id, ai_intent)
        except Exception as e:
            logger.warning(f"Max bot: failed to generate AI intent for template filter {filter_id}: {e}")

        price_min_fmt = f"{template['price_min']:,}".replace(",", " ")
        price_max_fmt = f"{template['price_max']:,}".replace(",", " ")
        keywords_str = ", ".join(template['keywords'][:5])

        await client.send_message(
            chat_id,
            (
                f"✅ <b>Фильтр создан!</b>\n\n"
                f"{template['emoji']} <b>{template['name']}</b>\n"
                f"🆔 ID: #{filter_id}\n\n"
                f"🔑 Ключевые слова: <i>{keywords_str}</i>\n"
                f"💰 Бюджет: {price_min_fmt} — {price_max_fmt} ₽\n"
                f"📍 Регионы: Вся Россия\n\n"
                f"🤖 Бот начал мониторинг! Вы получите уведомление, "
                f"как только появится подходящий тендер."
            ),
            keyboard=[
                [{"type": "callback", "text": "📋 Мои фильтры", "payload": "my_filters"}],
                [{"type": "callback", "text": "➕ Создать ещё", "payload": "new_filter"}],
                [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
            ],
        )

        logger.info(f"Max bot: template filter #{filter_id} created for user {user_id} (template={template_key})")

    except Exception as e:
        logger.error(f"Max bot: error creating template filter: {e}", exc_info=True)
        await client.send_message(chat_id, "⚠️ Произошла ошибка при создании фильтра.", keyboard=MAIN_MENU_KEYBOARD)


# ══════════════════════════════════════════════════════════════════
# ALL MY TENDERS (notification history)
# ══════════════════════════════════════════════════════════════════

async def _show_all_tenders(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
):
    """Show recent tender notifications."""
    try:
        user = await _ensure_user(user_id, username)
        if not user:
            await client.send_message(chat_id, "⚠️ Пользователь не найден.", keyboard=BACK_KEYBOARD)
            return

        db = await get_sniper_db()
        linked_id = await _get_linked_user_id(user)
        tenders = await db.get_user_tenders(linked_id, limit=10)

        if not tenders:
            await client.send_message(
                chat_id,
                (
                    "📊 <b>Все мои тендеры</b>\n\n"
                    "У вас пока нет уведомлений о тендерах.\n"
                    "Создайте фильтр, и бот начнёт присылать подходящие тендеры."
                ),
                keyboard=[
                    [{"type": "callback", "text": "➕ Создать фильтр", "payload": "new_filter"}],
                    [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
                ],
            )
            return

        lines = ["📊 <b>Все мои тендеры</b>\n"]
        keyboard = []

        for i, t in enumerate(tenders, 1):
            name = t.get('name', 'Без названия')
            short_name = name[:50] + "..." if len(name) > 50 else name
            price = t.get('price')
            price_text = _format_price(price) if price else "не указана"
            sent_at = t.get('sent_at', '')
            date_text = sent_at[:10] if sent_at else ""
            number = t.get('number', '')

            lines.append(f"{i}. 💰 {price_text} — {short_name}")
            if date_text:
                lines.append(f"   📅 {date_text}")

            if number:
                keyboard.append([{
                    "type": "callback",
                    "text": f"📄 {i}. {short_name[:35]}",
                    "payload": f"td_{number[:20]}",
                }])

        keyboard.append([{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}])

        await client.send_message(chat_id, "\n".join(lines), keyboard=keyboard)

    except Exception as e:
        logger.error(f"Max bot: error showing all tenders: {e}", exc_info=True)
        await client.send_message(chat_id, "⚠️ Произошла ошибка.", keyboard=BACK_KEYBOARD)


async def _show_tender_detail(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
    payload: str,
):
    """Show details of a specific tender from history."""
    tender_number = payload.replace("td_", "")

    try:
        user = await _ensure_user(user_id, username)
        if not user:
            return

        db = await get_sniper_db()
        tenders = await db.get_user_tenders(await _get_linked_user_id(user), limit=100)

        tender = None
        for t in tenders:
            if t.get('number', '').startswith(tender_number):
                tender = t
                break

        if not tender:
            await client.send_message(chat_id, "⚠️ Тендер не найден.", keyboard=BACK_KEYBOARD)
            return

        name = tender.get('name', 'Без названия')
        price = tender.get('price')
        price_text = _format_price(price) if price else "не указана"
        region = tender.get('region', '—')
        customer = tender.get('customer_name', '—')
        score = tender.get('score')
        url = tender.get('url', '')
        deadline = tender.get('submission_deadline', '—')
        filter_name = tender.get('filter_name', '—')

        text = (
            f"📄 <b>Тендер</b>\n\n"
            f"📝 <b>{name[:200]}</b>\n\n"
            f"💰 Цена: <b>{price_text}</b>\n"
            f"🏢 Заказчик: {customer[:100]}\n"
            f"📍 Регион: {region}\n"
            f"📅 Подача до: {deadline[:10] if deadline != '—' else '—'}\n"
        )

        if score:
            text += f"🎯 Релевантность: <b>{score}%</b>\n"
        if filter_name:
            text += f"📋 Фильтр: {filter_name}\n"
        if url:
            text += f"\n<a href=\"{url}\">🔗 Открыть на zakupki.gov.ru</a>"

        full_number = tender.get('number', '')
        keyboard = []
        action_row = []
        if full_number:
            action_row.append({"type": "callback", "text": "🔬 AI Анализ", "payload": f"aia_{full_number[:20]}"})
            action_row.append({"type": "callback", "text": "⭐ В избранное", "payload": f"fa_{full_number[:20]}"})
        if action_row:
            keyboard.append(action_row)
        keyboard.append([{"type": "callback", "text": "◀️ Все тендеры", "payload": "all_tenders"}])
        keyboard.append([{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}])

        await client.send_message(chat_id, text, keyboard=keyboard)

    except Exception as e:
        logger.error(f"Max bot: error showing tender detail: {e}", exc_info=True)
        await client.send_message(chat_id, "⚠️ Произошла ошибка.", keyboard=BACK_KEYBOARD)


# ══════════════════════════════════════════════════════════════════
# FAVORITES
# ══════════════════════════════════════════════════════════════════

async def _show_favorites(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
):
    """Show user's favorite tenders."""
    favs = _user_favorites.get(user_id, set())

    if not favs:
        await client.send_message(
            chat_id,
            (
                "⭐ <b>Избранное</b>\n\n"
                "У вас пока нет избранных тендеров.\n\n"
                "Нажмите ⭐ на карточке тендера, чтобы добавить в избранное."
            ),
            keyboard=[
                [{"type": "callback", "text": "📊 Все мои тендеры", "payload": "all_tenders"}],
                [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
            ],
        )
        return

    # Try to get tender details from DB
    try:
        user = await _ensure_user(user_id, username)
        db = await get_sniper_db()
        all_tenders = await db.get_user_tenders(await _get_linked_user_id(user), limit=100)
        tender_map = {t.get('number', ''): t for t in all_tenders}
    except Exception:
        tender_map = {}

    lines = ["⭐ <b>Избранное</b>\n"]
    keyboard = []

    for i, number in enumerate(list(favs)[:15], 1):
        tender = tender_map.get(number, {})
        name = tender.get('name', f'Тендер {number}')
        short_name = name[:40] + "..." if len(name) > 40 else name
        price = tender.get('price')
        price_text = _format_price(price) if price else ""

        line = f"{i}. {short_name}"
        if price_text:
            line += f" — {price_text}"
        lines.append(line)

        keyboard.append([
            {"type": "callback", "text": f"📄 {short_name[:30]}", "payload": f"td_{number[:20]}"},
            {"type": "callback", "text": "❌ Убрать", "payload": f"fr_{number[:20]}"},
        ])

    keyboard.append([{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}])

    await client.send_message(chat_id, "\n".join(lines), keyboard=keyboard)


async def _add_favorite(client: MaxBotClient, chat_id: int, user_id: int, payload: str):
    """Add tender to favorites."""
    tender_number = payload.replace("fa_", "")
    if user_id not in _user_favorites:
        _user_favorites[user_id] = set()
    _user_favorites[user_id].add(tender_number)

    await client.send_message(
        chat_id,
        f"⭐ Тендер <b>{tender_number}</b> добавлен в избранное!",
        keyboard=[
            [{"type": "callback", "text": "⭐ Избранное", "payload": "favorites"}],
            [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
        ],
    )
    logger.info(f"Max bot: user {user_id} added tender {tender_number} to favorites")


async def _remove_favorite(client: MaxBotClient, chat_id: int, user_id: int, payload: str):
    """Remove tender from favorites."""
    tender_number = payload.replace("fr_", "")
    if user_id in _user_favorites:
        _user_favorites[user_id].discard(tender_number)

    await client.send_message(
        chat_id,
        f"❌ Тендер <b>{tender_number}</b> удалён из избранного.",
        keyboard=[
            [{"type": "callback", "text": "⭐ Избранное", "payload": "favorites"}],
            [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
        ],
    )
    logger.info(f"Max bot: user {user_id} removed tender {tender_number} from favorites")


# ══════════════════════════════════════════════════════════════════
# STATISTICS
# ══════════════════════════════════════════════════════════════════

async def _show_stats(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
):
    """Show user statistics."""
    try:
        user = await _ensure_user(user_id, username)
        if not user:
            await client.send_message(chat_id, "⚠️ Пользователь не найден.", keyboard=BACK_KEYBOARD)
            return

        db = await get_sniper_db()

        # Get filters count
        filters = await db.get_user_filters(user['id'], active_only=False)
        active_filters = sum(1 for f in filters if f.get('is_active'))
        total_filters = len(filters)

        # Get notification count
        tenders = await db.get_user_tenders(await _get_linked_user_id(user), limit=10000)
        total_notifications = len(tenders)

        # Days since registration
        created_at = user.get('created_at', '')
        days_since = 0
        if created_at:
            try:
                created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                if created_dt.tzinfo:
                    created_dt = created_dt.replace(tzinfo=None)
                days_since = max(0, (datetime.now() - created_dt).days)
            except Exception:
                pass

        # Monitoring status
        monitoring_active = await db.get_monitoring_status(user_id)
        monitoring_text = "🟢 Активен" if monitoring_active else "🔴 На паузе"

        # Favorites count
        favs_count = len(_user_favorites.get(user_id, set()))

        text = (
            f"📈 <b>Ваша статистика</b>\n\n"
            f"📬 Уведомлений получено: <b>{total_notifications}</b>\n"
            f"📋 Активных фильтров: <b>{active_filters}/{total_filters}</b>\n"
            f"⭐ В избранном: <b>{favs_count}</b>\n"
            f"📡 Мониторинг: <b>{monitoring_text}</b>\n"
            f"📅 Дней с регистрации: <b>{days_since}</b>\n"
        )

        if total_notifications > 0:
            hours_saved = max(1, total_notifications * 0.5)
            text += f"\n⏱ Сэкономлено времени: <b>~{hours_saved:.0f} ч</b>"

        await client.send_message(chat_id, text, keyboard=BACK_KEYBOARD)

    except Exception as e:
        logger.error(f"Max bot: error showing stats: {e}", exc_info=True)
        await client.send_message(chat_id, "⚠️ Произошла ошибка.", keyboard=BACK_KEYBOARD)


# ══════════════════════════════════════════════════════════════════
# MONITORING PAUSE/RESUME
# ══════════════════════════════════════════════════════════════════

async def _toggle_monitoring(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
    pause: bool,
):
    """Toggle monitoring on/off."""
    try:
        await _ensure_user(user_id, username)
        db = await get_sniper_db()

        if pause:
            await db.pause_monitoring(user_id)
            text = (
                "⏸ <b>Мониторинг приостановлен</b>\n\n"
                "Вы не будете получать уведомления о новых тендерах.\n"
                "Фильтры сохранены — нажмите «Возобновить», чтобы продолжить."
            )
            keyboard = [
                [{"type": "callback", "text": "▶️ Возобновить мониторинг", "payload": "resume_mon"}],
                [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
            ]
        else:
            await db.resume_monitoring(user_id)
            text = (
                "▶️ <b>Мониторинг возобновлён</b>\n\n"
                "Вы снова будете получать уведомления о подходящих тендерах."
            )
            keyboard = [
                [{"type": "callback", "text": "⏸ Приостановить", "payload": "pause_mon"}],
                [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
            ]

        await client.send_message(chat_id, text, keyboard=keyboard)
        logger.info(f"Max bot: monitoring {'paused' if pause else 'resumed'} for user {user_id}")

    except Exception as e:
        logger.error(f"Max bot: error toggling monitoring: {e}", exc_info=True)
        await client.send_message(chat_id, "⚠️ Произошла ошибка.", keyboard=BACK_KEYBOARD)


# ══════════════════════════════════════════════════════════════════
# AI DOCUMENT ANALYSIS
# ══════════════════════════════════════════════════════════════════

async def _start_ai_analyze(client: MaxBotClient, chat_id: int, user_id: int):
    """Start AI analysis — ask for tender number."""
    _user_states[user_id] = {
        "state": "analyze_waiting",
        "data": {},
        "chat_id": chat_id,
    }

    await client.send_message(
        chat_id,
        (
            "🔬 <b>AI Анализ документации</b>\n\n"
            "Отправьте номер тендера или ссылку на закупку.\n\n"
            "Пример:\n"
            "• <code>0373100012324000015</code>\n"
            "• Ссылка с zakupki.gov.ru\n\n"
            "Бот скачает документацию и выполнит AI-анализ."
        ),
        keyboard=[
            [{"type": "callback", "text": "❌ Отмена", "payload": "menu"}],
        ],
    )


async def _handle_analyze_input(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
    text: str,
):
    """Handle tender number input for AI analysis."""
    _user_states.pop(user_id, None)

    # Extract tender number
    tender_number = _extract_tender_number_max(text)
    if not tender_number:
        await client.send_message(
            chat_id,
            (
                "⚠️ Не удалось найти номер тендера.\n\n"
                "Введите номер закупки (18-25 цифр) или ссылку с zakupki.gov.ru."
            ),
            keyboard=[
                [{"type": "callback", "text": "🔬 Попробовать снова", "payload": "ai_analyze"}],
                [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
            ],
        )
        return

    await _do_ai_analyze(client, chat_id, user_id, username, tender_number)


async def _do_ai_analyze(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
    tender_number: str,
):
    """Perform AI analysis of tender documents."""
    user = await _ensure_user(user_id, username)
    from types import SimpleNamespace
    _fake_user = SimpleNamespace(
        subscription_tier=(user.get('subscription_tier', 'trial') if user else 'trial'),
        has_ai_unlimited=(user.get('has_ai_unlimited', False) if user else False),
        ai_unlimited_expires_at=(user.get('ai_unlimited_expires_at') if user else None),
        ai_analyses_used_month=(user.get('ai_analyses_used_month', 0) if user else 0),
    )
    _allowed, _reason = can_use_ai(_fake_user)
    if not _allowed:
        await client.send_message(
            chat_id,
            f"⚠️ {_reason}",
            keyboard=[
                [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
            ],
        )
        return

    await client.send_message(
        chat_id,
        f"🔍 <b>Анализирую документацию тендера {tender_number}...</b>\n\nЭто может занять некоторое время.",
        keyboard=[],
    )

    try:
        from bot.handlers.webapp import _run_ai_analysis

        tier = user.get('subscription_tier', 'trial') if user else 'trial'

        result_text, is_ai, raw_data = await _run_ai_analysis(tender_number, tier)

        # Truncate if too long for Max
        if len(result_text) > 3500:
            result_text = result_text[:3400] + "\n\n<i>... (сокращено)</i>"

        header = "🔬 <b>AI Анализ документации</b>\n\n" if is_ai else "📄 <b>Анализ документации</b>\n\n"

        await client.send_message(
            chat_id,
            header + result_text,
            keyboard=[
                [{"type": "callback", "text": "🔬 Анализ другого тендера", "payload": "ai_analyze"}],
                [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
            ],
        )

        logger.info(f"Max bot: AI analysis completed for tender {tender_number} by user {user_id}")

    except Exception as e:
        logger.error(f"Max bot: AI analysis error for {tender_number}: {e}", exc_info=True)
        error_msg = str(e)
        if "Не удалось загрузить" in error_msg:
            text = f"⚠️ Не удалось загрузить документацию тендера {tender_number}.\n\nВозможно, тендер не найден или документы недоступны."
        else:
            text = f"⚠️ Произошла ошибка при анализе тендера {tender_number}.\n\nПопробуйте позже."

        await client.send_message(
            chat_id,
            text,
            keyboard=[
                [{"type": "callback", "text": "🔬 Попробовать снова", "payload": "ai_analyze"}],
                [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
            ],
        )


def _extract_tender_number_max(text: str) -> Optional[str]:
    """Extract tender number from text or URL."""
    # URL: regNumber=(\d+)
    m = re.search(r'regNumber=(\d+)', text)
    if m:
        return m.group(1)
    # Pure number (18-25 digits)
    m = re.search(r'\b(\d{18,25})\b', text)
    if m:
        return m.group(1)
    return None


# ══════════════════════════════════════════════════════════════════
# MANUAL SEARCH
# ══════════════════════════════════════════════════════════════════

async def _start_manual_search(client: MaxBotClient, chat_id: int, user_id: int):
    """Start manual search — ask for keywords."""
    _user_states[user_id] = {
        "state": "search_waiting",
        "data": {},
        "chat_id": chat_id,
    }

    await client.send_message(
        chat_id,
        (
            "🔍 <b>Поиск тендеров</b>\n\n"
            "Введите ключевые слова для поиска.\n\n"
            "Пример: <i>компьютер, ноутбук</i>\n\n"
            "Бот найдёт актуальные тендеры по вашему запросу."
        ),
        keyboard=[
            [{"type": "callback", "text": "❌ Отмена", "payload": "menu"}],
        ],
    )


async def _handle_search_input(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
    text: str,
):
    """Handle search keywords input."""
    _user_states.pop(user_id, None)

    keywords = [kw.strip() for kw in text.replace(";", ",").split(",") if kw.strip()]
    if not keywords:
        await client.send_message(
            chat_id,
            "⚠️ Введите хотя бы одно ключевое слово.",
            keyboard=[
                [{"type": "callback", "text": "🔍 Попробовать снова", "payload": "man_search"}],
                [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
            ],
        )
        return

    await client.send_message(
        chat_id,
        f"🔍 Ищу тендеры по запросу: <b>{', '.join(keywords[:5])}</b>...\n\nЭто может занять некоторое время.",
        keyboard=[],
    )

    try:
        from tender_sniper.instant_search import InstantSearch
        searcher = InstantSearch()

        filter_data = {
            'name': ', '.join(keywords[:3]),
            'keywords': keywords[:10],
            'exclude_keywords': [],
            'price_min': None,
            'price_max': None,
            'regions': [],
            'tender_types': [],
            'law_type': None,
        }

        user = await _ensure_user(user_id, username)
        tier = user.get('subscription_tier', 'trial') if user else 'trial'

        result = await searcher.search_by_filter(
            filter_data=filter_data,
            max_tenders=5,
            use_ai_check=False,
            user_id=user['id'] if user else None,
            subscription_tier=tier,
        )

        matches = result.get('matches', []) or result.get('tenders', [])

        if not matches:
            await client.send_message(
                chat_id,
                (
                    "🔍 <b>Результаты поиска</b>\n\n"
                    "К сожалению, по вашему запросу ничего не найдено.\n\n"
                    "💡 Попробуйте:\n"
                    "• Использовать другие ключевые слова\n"
                    "• Расширить запрос\n"
                    "• Создать фильтр для автомониторинга"
                ),
                keyboard=[
                    [{"type": "callback", "text": "🔍 Новый поиск", "payload": "man_search"}],
                    [{"type": "callback", "text": "➕ Создать фильтр", "payload": "new_filter"}],
                    [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
                ],
            )
            return

        lines = [f"🔍 <b>Найдено тендеров: {len(matches)}</b>\n"]
        keyboard = []

        for i, tender in enumerate(matches[:5], 1):
            name = tender.get('name', 'Без названия')
            short_name = name[:50] + "..." if len(name) > 50 else name
            price = tender.get('price')
            price_text = _format_price(price) if price else "не указана"
            region = tender.get('region', '')
            score = tender.get('score', tender.get('relevance_score', ''))

            line = f"{i}. <b>{short_name}</b>"
            line += f"\n   💰 {price_text}"
            if region:
                line += f" | 📍 {region}"
            if score:
                line += f" | 🎯 {score}%"
            lines.append(line)

            number = tender.get('number', '')
            if number:
                keyboard.append([{
                    "type": "callback",
                    "text": f"📄 {i}. {short_name[:30]}",
                    "payload": f"td_{number[:20]}",
                }])

        keyboard.append([{"type": "callback", "text": "🔍 Новый поиск", "payload": "man_search"}])
        keyboard.append([{"type": "callback", "text": "➕ Создать фильтр из запроса", "payload": "new_filter"}])
        keyboard.append([{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}])

        await client.send_message(chat_id, "\n".join(lines), keyboard=keyboard)

        logger.info(f"Max bot: search completed for user {user_id}, found {len(matches)} tenders")

    except Exception as e:
        logger.error(f"Max bot: search error: {e}", exc_info=True)
        await client.send_message(
            chat_id,
            "⚠️ Произошла ошибка при поиске. Попробуйте позже.",
            keyboard=[
                [{"type": "callback", "text": "🔍 Попробовать снова", "payload": "man_search"}],
                [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
            ],
        )


# ══════════════════════════════════════════════════════════════════
# FILTER EDITING
# ══════════════════════════════════════════════════════════════════

async def _show_filter_edit_menu(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
    payload: str,
):
    """Show edit menu for a filter."""
    try:
        filter_id = int(payload.replace("fe_", ""))
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
    price_min = filter_data.get('price_min')
    price_max = filter_data.get('price_max')
    regions = filter_data.get('regions', [])

    kw_text = ", ".join(keywords) if keywords else "—"
    budget_text = _format_budget_text({"price_min": price_min, "price_max": price_max})
    reg_text = ", ".join(regions) if regions else "Вся Россия"

    fid = str(filter_id)

    await client.send_message(
        chat_id,
        (
            f"✏️ <b>Редактирование фильтра #{filter_id}</b>\n\n"
            f"📝 Название: <b>{name}</b>\n"
            f"🔑 Слова: <b>{kw_text}</b>\n"
            f"💰 Бюджет: <b>{budget_text}</b>\n"
            f"📍 Регион: <b>{reg_text}</b>\n\n"
            f"Выберите что изменить:"
        ),
        keyboard=[
            [{"type": "callback", "text": "📝 Название", "payload": f"fen_{fid}"}],
            [{"type": "callback", "text": "🔑 Ключевые слова", "payload": f"fek_{fid}"}],
            [
                {"type": "callback", "text": "💰 Цена от", "payload": f"fepn_{fid}"},
                {"type": "callback", "text": "💰 Цена до", "payload": f"fepx_{fid}"},
            ],
            [{"type": "callback", "text": "📍 Регионы", "payload": f"fer_{fid}"}],
            [{"type": "callback", "text": "◀️ Назад к фильтру", "payload": f"fv_{fid}"}],
        ],
    )


async def _start_filter_edit(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    field: str,
    filter_id_str: str,
):
    """Start editing a specific filter field."""
    try:
        filter_id = int(filter_id_str)
    except ValueError:
        return

    _user_states[user_id] = {
        "state": f"fedit_{field}",
        "data": {"filter_id": filter_id},
        "chat_id": chat_id,
    }

    prompts = {
        "name": "📝 Введите новое <b>название</b> фильтра:",
        "keywords": "🔑 Введите новые <b>ключевые слова</b> через запятую:",
        "price_min": "💰 Введите <b>минимальную цену</b> (число, 0 = без минимума):",
        "price_max": "💰 Введите <b>максимальную цену</b> (число, 0 = без максимума):",
        "regions": "📍 Введите <b>регионы</b> через запятую (или «все» для всей России):",
    }

    await client.send_message(
        chat_id,
        prompts.get(field, "Введите новое значение:"),
        keyboard=[
            [{"type": "callback", "text": "❌ Отмена", "payload": f"fe_{filter_id}"}],
        ],
    )


async def _handle_filter_edit_text(
    client: MaxBotClient,
    chat_id: int,
    user_id: int,
    username: str,
    text: str,
    state: dict,
):
    """Handle text input for filter editing."""
    current = state.get("state", "")
    filter_id = state["data"].get("filter_id")
    _user_states.pop(user_id, None)

    if not filter_id:
        await client.send_message(chat_id, "⚠️ Сессия истекла.", keyboard=MAIN_MENU_KEYBOARD)
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

        field = current.replace("fedit_", "")
        update_kwargs = {}
        success_text = ""

        if field == "name":
            new_name = text.strip()[:100]
            if len(new_name) < 2:
                await client.send_message(chat_id, "⚠️ Название слишком короткое.", keyboard=BACK_KEYBOARD)
                return
            update_kwargs = {"name": new_name}
            success_text = f"📝 Название изменено на: <b>{new_name}</b>"

        elif field == "keywords":
            keywords = [kw.strip() for kw in text.replace(";", ",").split(",") if kw.strip()]
            if not keywords:
                await client.send_message(chat_id, "⚠️ Введите хотя бы одно слово.", keyboard=BACK_KEYBOARD)
                return
            keywords = keywords[:15]
            update_kwargs = {"keywords": keywords}
            success_text = f"🔑 Ключевые слова: <b>{', '.join(keywords)}</b>"

        elif field == "price_min":
            cleaned = text.strip().replace(" ", "").replace(",", "")
            try:
                val = int(cleaned)
                if val <= 0:
                    val = None
            except ValueError:
                await client.send_message(chat_id, "⚠️ Введите число.", keyboard=BACK_KEYBOARD)
                return
            update_kwargs = {"price_min": val}
            success_text = f"💰 Мин. цена: <b>{_format_price(val)}</b>"

        elif field == "price_max":
            cleaned = text.strip().replace(" ", "").replace(",", "")
            try:
                val = int(cleaned)
                if val <= 0:
                    val = None
            except ValueError:
                await client.send_message(chat_id, "⚠️ Введите число.", keyboard=BACK_KEYBOARD)
                return
            update_kwargs = {"price_max": val}
            success_text = f"💰 Макс. цена: <b>{_format_price(val)}</b>"

        elif field == "regions":
            if text.strip().lower() in ("все", "all", "вся россия"):
                update_kwargs = {"regions": []}
                success_text = "📍 Регионы: <b>Вся Россия</b>"
            else:
                regions = [r.strip() for r in text.replace(";", ",").split(",") if r.strip()]
                update_kwargs = {"regions": regions}
                success_text = f"📍 Регионы: <b>{', '.join(regions)}</b>"

        if update_kwargs:
            await db.update_filter(filter_id=filter_id, **update_kwargs)

            # Regenerate AI intent if keywords changed
            if "keywords" in update_kwargs:
                try:
                    from tender_sniper.ai_relevance_checker import generate_intent
                    updated_filter = await db.get_filter_by_id(filter_id)
                    ai_intent = await generate_intent(
                        filter_name=updated_filter.get('name', ''),
                        keywords=update_kwargs['keywords'],
                        exclude_keywords=updated_filter.get('exclude_keywords', []),
                    )
                    if ai_intent:
                        await db.update_filter_intent(filter_id, ai_intent)
                except Exception as e:
                    logger.warning(f"Max bot: failed to regenerate AI intent: {e}")

            await client.send_message(
                chat_id,
                f"✅ Фильтр #{filter_id} обновлён!\n\n{success_text}",
                keyboard=[
                    [{"type": "callback", "text": "✏️ Ещё изменения", "payload": f"fe_{filter_id}"}],
                    [{"type": "callback", "text": "📄 К фильтру", "payload": f"fv_{filter_id}"}],
                    [{"type": "callback", "text": "◀️ Главное меню", "payload": "menu"}],
                ],
            )
            logger.info(f"Max bot: filter {filter_id} updated ({field}) by user {user_id}")

    except Exception as e:
        logger.error(f"Max bot: error editing filter {filter_id}: {e}", exc_info=True)
        await client.send_message(chat_id, "⚠️ Произошла ошибка.", keyboard=BACK_KEYBOARD)
