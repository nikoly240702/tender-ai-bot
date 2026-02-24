"""
Subscription Management Handlers.

Управление подписками пользователей:
- Проверка статуса подписки
- Отображение информации о тарифах
- Активация trial
- Продление подписки
- Активация промокодов

Feature flag: subscriptions (config/features.yaml)
"""

import logging
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from tender_sniper.database.sqlalchemy_adapter import get_sniper_db

logger = logging.getLogger(__name__)

router = Router(name="subscriptions")


# ============================================
# FSM States for Promocode
# ============================================

class PromocodeStates(StatesGroup):
    """Состояния для ввода промокода."""
    waiting_for_code = State()


# ============================================
# Keyboard Helpers
# ============================================

def get_back_to_menu_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура возврата в меню."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ В меню", callback_data="sniper_menu")]
    ])


def get_subscription_keyboard(subscription: dict = None) -> InlineKeyboardMarkup:
    """Клавиатура управления подпиской."""
    builder = InlineKeyboardBuilder()

    if not subscription or not subscription.get('is_active'):
        # No active subscription
        builder.row(
            InlineKeyboardButton(text="🎁 Активировать Trial (14 дней)", callback_data="subscription_activate_trial")
        )
        builder.row(
            InlineKeyboardButton(text="📦 Посмотреть тарифы", callback_data="subscription_tiers")
        )
    else:
        # Has subscription
        if subscription.get('is_trial'):
            builder.row(
                InlineKeyboardButton(text="⬆️ Повысить тариф", callback_data="subscription_tiers")
            )
        elif subscription.get('tier') == 'basic':
            builder.row(
                InlineKeyboardButton(text="💎 Перейти на Premium", callback_data="subscription_select_premium")
            )

        builder.row(
            InlineKeyboardButton(text="📊 История платежей", callback_data="subscription_history")
        )

    # Кнопка промокода всегда доступна
    builder.row(
        InlineKeyboardButton(text="🎟 Ввести промокод", callback_data="subscription_promocode")
    )

    builder.row(
        InlineKeyboardButton(text="◀️ В меню", callback_data="sniper_menu")
    )

    return builder.as_markup()


# ============================================
# Subscription Tiers Configuration
# ============================================

# Базовые тарифы (месячная цена)
BASE_PRICES = {
    'basic': 1490,
    'premium': 2990,
    'ai_unlimited': 1490,
}

# Фиксированные цены для разных периодов
FIXED_PRICES = {
    'basic': {
        1: 1490,   # 1 месяц
        3: 4020,   # 3 месяца (скидка 450₽ = ~10%)
        6: 7150,   # 6 месяцев (скидка 1790₽ = ~20%)
    },
    'premium': {
        1: 2990,   # 1 месяц
        3: 8070,   # 3 месяца (скидка 900₽)
        6: 14350,  # 6 месяцев (скидка 3590₽)
    },
    'ai_unlimited': {
        1: 1490,   # 1 месяц
        3: 4020,   # 3 месяца (скидка 450₽)
        6: 7150,   # 6 месяцев (скидка 1790₽)
    }
}

# Описания периодов
DURATION_OPTIONS = {
    1: {'months': 1, 'label': '1 месяц'},
    3: {'months': 3, 'label': '3 месяца', 'badge': '🔥 Выгодно'},
    6: {'months': 6, 'label': '6 месяцев', 'badge': '💰 Лучшая цена'},
}

SUBSCRIPTION_TIERS = {
    'trial': {
        'name': 'Пробный период',
        'emoji': '🎁',
        'price': 0,
        'days': 14,
        'max_filters': 3,
        'max_notifications_per_day': 20,
        'features': [
            '3 фильтра мониторинга',
            '20 уведомлений/день',
            'Мгновенный поиск',
            'Избранное',
        ]
    },
    'basic': {
        'name': 'Basic',
        'emoji': '⭐',
        'price': 1490,
        'days': 30,
        'max_filters': 5,
        'max_notifications_per_day': 100,
        'features': [
            '5 фильтров мониторинга',
            '100 уведомлений/день',
            'Мгновенный поиск',
            'AI-анализ (10/мес)',
            'Telegram-поддержка',
        ]
    },
    'premium': {
        'name': 'Premium',
        'emoji': '💎',
        'price': 2990,
        'days': 30,
        'max_filters': 20,
        'max_notifications_per_day': 9999,
        'features': [
            '20 фильтров мониторинга',
            'Безлимит уведомлений',
            'AI-анализ (50/мес)',
            'Архивный поиск',
            'Расширенные настройки фильтров',
            'Приоритетная поддержка',
        ]
    }
}


def calculate_price(tier: str, months: int) -> dict:
    """Рассчитать цену с учётом скидки."""
    base_price = BASE_PRICES.get(tier, 1490)
    duration = DURATION_OPTIONS.get(months, DURATION_OPTIONS[1])

    # Получаем фиксированную цену
    tier_prices = FIXED_PRICES.get(tier, FIXED_PRICES['basic'])
    final_price = tier_prices.get(months, base_price * months)

    full_price = base_price * duration['months']
    discount_amount = full_price - final_price

    return {
        'base_price': base_price,
        'months': duration['months'],
        'days': duration['months'] * 30,
        'full_price': full_price,
        'discount_amount': discount_amount,
        'final_price': final_price,
        'label': duration['label'],
        'badge': duration.get('badge', ''),
        'has_discount': discount_amount > 0,
    }


# ============================================
# Handlers
# ============================================

@router.message(Command("subscription"))
async def cmd_subscription(message: Message):
    """Show subscription status."""
    await show_subscription_status(message)


@router.callback_query(F.data == "sniper_subscription")
async def callback_subscription(callback: CallbackQuery):
    """Show subscription status from menu."""
    await callback.answer()
    await show_subscription_status(callback.message, callback.from_user.id)


async def show_subscription_status(message: Message, user_id: int = None):
    """Display subscription status for user."""
    user_id = user_id or message.from_user.id

    db = await get_sniper_db()

    # Get user
    user = await db.get_user_by_telegram_id(user_id)
    if not user:
        await message.answer(
            "❌ Пользователь не найден. Используйте /start для регистрации."
        )
        return

    # Get subscription from subscriptions table
    subscription = await db.get_subscription(user['id'])

    # Get user subscription data directly from sniper_users
    user_full = await db.get_user_subscription_info(user_id)

    # Determine active subscription (prefer sniper_users data for paid subscriptions)
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
            except:
                expires_dt = datetime.now()
        else:
            expires_dt = expires_at

        # Remove timezone for comparison
        if expires_dt.tzinfo:
            expires_dt = expires_dt.replace(tzinfo=None)

        delta = expires_dt - datetime.now()
        days_remaining = max(0, delta.days)

    # Check if subscription is active
    is_active = tier in ['basic', 'premium'] or (tier == 'trial' and days_remaining > 0)
    is_trial = tier == 'trial'

    if is_active:
        tier_info = SUBSCRIPTION_TIERS.get(tier, SUBSCRIPTION_TIERS['trial'])

        # Format expires_at for display
        expires_display = 'Н/Д'
        if expires_at:
            if isinstance(expires_at, str):
                expires_display = expires_at[:10]
            else:
                expires_display = expires_at.strftime('%d.%m.%Y')

        text = f"""
📦 <b>Ваша подписка</b>

{tier_info['emoji']} <b>Тариф:</b> {tier_info['name']}
📅 <b>Действует до:</b> {expires_display}
⏳ <b>Осталось дней:</b> {days_remaining}

<b>Лимиты:</b>
• Фильтров: {filters_limit}
• Уведомлений/день: {notifications_limit}

<b>Возможности:</b>
"""
        for feature in tier_info['features']:
            text += f"✅ {feature}\n"

        if is_trial:
            text += f"\n⚠️ <i>Пробный период закончится через {days_remaining} дней. Оформите подписку чтобы продолжить пользоваться сервисом.</i>"
    else:
        # No active subscription
        text = """
📦 <b>Подписка</b>

❌ <b>У вас нет активной подписки</b>

Активируйте пробный период на 14 дней бесплатно или выберите тариф:
"""

    # Build subscription dict for keyboard
    sub_for_keyboard = {
        'is_active': is_active,
        'is_trial': is_trial,
        'tier': tier
    } if is_active else None

    await message.answer(
        text,
        reply_markup=get_subscription_keyboard(sub_for_keyboard),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "subscription_activate_trial")
async def callback_activate_trial(callback: CallbackQuery):
    """Activate trial subscription."""
    await callback.answer()

    db = await get_sniper_db()

    # Get user
    user = await db.get_user_by_telegram_id(callback.from_user.id)
    if not user:
        await callback.message.answer("❌ Ошибка: пользователь не найден")
        return

    # Check if already had trial
    existing_sub = await db.get_subscription(user['id'])
    if existing_sub:
        await callback.message.edit_text(
            "⚠️ <b>Пробный период уже был активирован</b>\n\n"
            "Вы можете оформить платную подписку для продолжения использования сервиса.",
            parse_mode="HTML",
            reply_markup=get_subscription_keyboard(existing_sub)
        )
        return

    # Create trial subscription
    trial_config = SUBSCRIPTION_TIERS['trial']
    await db.create_subscription(
        user_id=user['id'],
        tier='trial',
        days=trial_config['days'],
        max_filters=trial_config['max_filters'],
        max_notifications_per_day=trial_config['max_notifications_per_day']
    )

    await callback.message.edit_text(
        f"""
🎉 <b>Пробный период активирован!</b>

{trial_config['emoji']} Тариф: {trial_config['name']}
📅 Срок: {trial_config['days']} дней

<b>Доступные возможности:</b>
""" + "\n".join([f"✅ {f}" for f in trial_config['features']]) + """

Теперь вы можете создавать фильтры и получать уведомления о новых тендерах!

Используйте /menu для навигации по боту.
""",
        parse_mode="HTML",
        reply_markup=get_back_to_menu_keyboard()
    )

    logger.info(f"✅ Trial activated for user {callback.from_user.id}")


@router.callback_query(F.data.startswith("subscription_select_"))
async def callback_select_tier(callback: CallbackQuery):
    """Show tier details and duration options with discounts."""
    await callback.answer()

    tier_name = callback.data.replace("subscription_select_", "")

    # AI Unlimited — аддон
    if tier_name == 'ai_unlimited':
        text = (
            "🤖 <b>AI Unlimited (аддон)</b>\n\n"
            "Безлимитный AI-анализ документов тендеров.\n"
            "Работает поверх любого тарифа (Basic/Premium).\n\n"
            "<b>Выберите период:</b>\n"
        )
        buttons = []
        for months in [1, 3, 6]:
            price_info = calculate_price('ai_unlimited', months)
            if price_info['has_discount']:
                btn_text = f"{price_info['badge']} {price_info['label']} — {price_info['final_price']} ₽"
                text += f"\n{price_info['badge']} <b>{price_info['label']}</b>: <s>{price_info['full_price']} ₽</s> → <b>{price_info['final_price']} ₽</b>"
            else:
                btn_text = f"📅 {price_info['label']} — {price_info['final_price']} ₽"
                text += f"\n📅 <b>{price_info['label']}</b>: <b>{price_info['final_price']} ₽</b>"
            buttons.append([InlineKeyboardButton(
                text=btn_text,
                callback_data=f"subscription_pay_ai_unlimited_{months}"
            )])
        buttons.append([InlineKeyboardButton(text="◀️ Назад к тарифам", callback_data="subscription_tiers")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        return

    tier_info = SUBSCRIPTION_TIERS.get(tier_name)

    if not tier_info:
        await callback.message.answer("❌ Тариф не найден")
        return

    text = f"""
{tier_info['emoji']} <b>Тариф {tier_info['name']}</b>

<b>Что включено:</b>
"""
    for feature in tier_info['features']:
        text += f"✅ {feature}\n"

    text += "\n<b>Выберите период подписки:</b>\n"

    # Показываем все варианты длительности с ценами
    buttons = []
    for months in [1, 3, 6]:
        price_info = calculate_price(tier_name, months)

        if price_info['has_discount']:
            btn_text = f"{price_info['badge']} {price_info['label']} — {price_info['final_price']} ₽"
            text += f"\n{price_info['badge']} <b>{price_info['label']}</b>: <s>{price_info['full_price']} ₽</s> → <b>{price_info['final_price']} ₽</b>"
        else:
            btn_text = f"📅 {price_info['label']} — {price_info['final_price']} ₽"
            text += f"\n📅 <b>{price_info['label']}</b>: <b>{price_info['final_price']} ₽</b>"

        buttons.append([InlineKeyboardButton(
            text=btn_text,
            callback_data=f"subscription_pay_{tier_name}_{months}"
        )])

    buttons.append([InlineKeyboardButton(
        text="◀️ Назад к тарифам",
        callback_data="subscription_tiers"
    )])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("subscription_pay_"))
async def callback_pay_tier(callback: CallbackQuery):
    """Initiate payment for subscription via YooKassa."""
    await callback.answer()

    # Парсим callback: subscription_pay_{tier}_{months}
    parts = callback.data.replace("subscription_pay_", "").split("_")
    tier_name = parts[0]
    months = int(parts[1]) if len(parts) > 1 else 1

    tier_info = SUBSCRIPTION_TIERS.get(tier_name)
    if not tier_info:
        await callback.message.answer("❌ Тариф не найден")
        return

    # Рассчитываем цену со скидкой
    price_info = calculate_price(tier_name, months)

    # Интеграция с YooKassa
    try:
        from tender_sniper.payments import get_yookassa_client

        client = get_yookassa_client()

        if not client.is_configured:
            # YooKassa не настроена - показываем заглушку
            await callback.message.edit_text(
                f"""
💳 <b>Оплата тарифа {tier_info['name']}</b>

Период: <b>{price_info['label']}</b>
Сумма: <b>{price_info['final_price']} ₽</b>

🚧 <i>Платежная система временно недоступна.</i>

Для активации подписки обратитесь к администратору.
""",
                parse_mode="HTML",
                reply_markup=get_back_to_menu_keyboard()
            )
            return

        # Создаём платёж с учётом периода
        result = client.create_payment(
            telegram_id=callback.from_user.id,
            tier=tier_name,
            amount=price_info['final_price'],
            days=price_info['days'],
            description=f"Подписка {tier_info['name']} на {price_info['label']}"
        )

        if 'error' in result:
            await callback.message.edit_text(
                f"❌ Ошибка создания платежа: {result['error']}",
                parse_mode="HTML",
                reply_markup=get_back_to_menu_keyboard()
            )
            return

        # Формируем текст со скидкой
        if price_info['has_discount']:
            price_text = f"<s>{price_info['full_price']} ₽</s> → <b>{price_info['final_price']} ₽</b> (экономия {price_info['discount_amount']} ₽)"
        else:
            price_text = f"<b>{price_info['final_price']} ₽</b>"

        # Отправляем ссылку на оплату
        payment_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"💳 Оплатить {price_info['final_price']} ₽",
                url=result['url']
            )],
            [InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=f"subscription_select_{tier_name}"
            )],
        ])

        await callback.message.edit_text(
            f"""
💳 <b>Оплата тарифа {tier_info['name']}</b>

📅 Период: <b>{price_info['label']}</b>
💰 Сумма: {price_text}

Нажмите кнопку ниже для перехода к оплате.
После успешной оплаты подписка активируется автоматически.

⏳ <i>Ссылка действительна 15 минут</i>
""",
            parse_mode="HTML",
            reply_markup=payment_keyboard
        )

        logger.info(f"Payment created for user {callback.from_user.id}, tier {tier_name}, months {months}, amount {price_info['final_price']}, payment_id {result['payment_id']}")

        # Track subscription purchase intent
        import asyncio
        try:
            from bot.analytics import track_subscription_action
            asyncio.create_task(track_subscription_action(
                callback.from_user.id, 'purchased',
                tier=tier_name, amount=price_info['final_price']
            ))
        except Exception:
            pass

    except ImportError:
        logger.warning("YooKassa module not available")
        await callback.message.edit_text(
            f"""
💳 <b>Оплата тарифа {tier_info['name']}</b>

Период: <b>{price_info['label']}</b>
Сумма: <b>{price_info['final_price']} ₽</b>

🚧 <i>Платежный модуль не установлен.</i>

Для активации подписки обратитесь к администратору.
""",
            parse_mode="HTML",
            reply_markup=get_back_to_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Payment error: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ Ошибка: {str(e)}",
            parse_mode="HTML",
            reply_markup=get_back_to_menu_keyboard()
        )


@router.callback_query(F.data == "subscription_tiers")
async def callback_show_tiers(callback: CallbackQuery):
    """Show all available subscription tiers."""
    await callback.answer()

    # Track subscription viewed
    import asyncio
    try:
        from bot.analytics import track_subscription_action
        asyncio.create_task(track_subscription_action(callback.from_user.id, 'viewed'))
    except Exception:
        pass

    text = "📦 <b>Тарифные планы</b>\n\n"

    for tier_id, tier_info in SUBSCRIPTION_TIERS.items():
        if tier_id == 'trial':
            continue  # Skip trial in comparison

        price_text = f"{tier_info['price']} ₽/мес" if tier_info['price'] > 0 else "Бесплатно"

        text += f"""
{tier_info['emoji']} <b>{tier_info['name']}</b> — {price_text}
• {tier_info['max_filters']} фильтров
• {tier_info['max_notifications_per_day']} уведомлений/день
"""

    text += "\n🤖 <b>AI Unlimited</b> — аддон +1 490 ₽/мес\n• Безлимитный AI-анализ документов\n"

    text += "\n<i>Выберите тариф для подробностей:</i>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{info['emoji']} {info['name']} — {info['price']} ₽",
            callback_data=f"subscription_select_{tier_id}"
        )]
        for tier_id, info in SUBSCRIPTION_TIERS.items()
        if tier_id != 'trial'
    ] + [
        [InlineKeyboardButton(text="🤖 AI Unlimited — 1 490 ₽/мес", callback_data="subscription_select_ai_unlimited")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="sniper_subscription")]
    ])

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard
    )


# ============================================
# Subscription Check Middleware Functions
# ============================================

async def check_subscription_limit(telegram_id: int, action: str = 'filter') -> tuple[bool, str]:
    """
    Check if user can perform action based on subscription.

    Args:
        telegram_id: User's telegram ID
        action: Action type ('filter', 'notification', 'search')

    Returns:
        Tuple of (is_allowed, message)
    """
    db = await get_sniper_db()

    user = await db.get_user_by_telegram_id(telegram_id)
    if not user:
        return False, "Пользователь не найден. Используйте /start"

    subscription = await db.get_subscription(user['id'])

    if not subscription or not subscription['is_active']:
        return False, (
            "❌ <b>Нет активной подписки</b>\n\n"
            "Активируйте пробный период или оформите подписку:\n"
            "/subscription"
        )

    if action == 'filter':
        # Check filter limit
        filters = await db.get_user_filters(user['id'])
        if len(filters) >= subscription['max_filters']:
            return False, (
                f"❌ <b>Достигнут лимит фильтров</b>\n\n"
                f"Ваш тариф позволяет создать максимум {subscription['max_filters']} фильтров.\n"
                f"Удалите неиспользуемые фильтры или повысьте тариф:\n"
                f"/subscription"
            )

    elif action == 'notification':
        # Check daily notification limit
        stats = await db.get_user_stats(user['id'])
        if stats['notifications_today'] >= subscription['max_notifications_per_day']:
            return False, (
                f"❌ <b>Достигнут лимит уведомлений</b>\n\n"
                f"Сегодня отправлено {stats['notifications_today']} из {subscription['max_notifications_per_day']} уведомлений.\n"
                f"Лимит сбросится завтра или повысьте тариф:\n"
                f"/subscription"
            )

    return True, ""


async def get_subscription_status_line(telegram_id: int) -> str:
    """
    Get short subscription status for display in menus.

    Returns something like: "📦 Trial (12 дней)"
    """
    db = await get_sniper_db()

    # Get user subscription data directly from sniper_users
    user_full = await db.get_user_subscription_info(telegram_id)

    if not user_full:
        return "❌ Нет подписки"

    tier = user_full.get('subscription_tier', 'trial')
    expires_at = user_full.get('trial_expires_at')

    # Calculate days remaining
    days_remaining = 0
    if expires_at:
        from datetime import datetime
        if isinstance(expires_at, str):
            try:
                expires_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            except:
                expires_dt = datetime.now()
        else:
            expires_dt = expires_at

        if expires_dt.tzinfo:
            expires_dt = expires_dt.replace(tzinfo=None)

        delta = expires_dt - datetime.now()
        days_remaining = max(0, delta.days)

    # Check if subscription is active
    is_active = tier in ['basic', 'premium'] or (tier == 'trial' and days_remaining > 0)

    if not is_active or tier == 'expired':
        return "❌ Подписка неактивна"

    tier_info = SUBSCRIPTION_TIERS.get(tier, SUBSCRIPTION_TIERS['trial'])
    return f"{tier_info['emoji']} {tier_info['name']} ({days_remaining} дн.)"


# ============================================
# Promocode Handlers
# ============================================

@router.callback_query(F.data == "subscription_promocode")
async def callback_promocode_start(callback: CallbackQuery, state: FSMContext):
    """Начать ввод промокода."""
    await callback.answer()
    await state.set_state(PromocodeStates.waiting_for_code)

    await callback.message.edit_text(
        "🎟 <b>Введите промокод</b>\n\n"
        "Отправьте промокод сообщением.\n"
        "Промокод не чувствителен к регистру.\n\n"
        "<i>Для отмены нажмите кнопку ниже.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="subscription_promocode_cancel")]
        ])
    )


@router.callback_query(F.data == "subscription_promocode_cancel")
async def callback_promocode_cancel(callback: CallbackQuery, state: FSMContext):
    """Отменить ввод промокода."""
    await callback.answer()
    await state.clear()
    await show_subscription_status(callback.message, callback.from_user.id)


@router.message(PromocodeStates.waiting_for_code)
async def process_promocode(message: Message, state: FSMContext):
    """Обработать введённый промокод."""
    code = message.text.strip().upper()

    if not code:
        await message.answer(
            "❌ Промокод не может быть пустым.\n"
            "Пожалуйста, введите промокод или нажмите кнопку отмены.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="subscription_promocode_cancel")]
            ])
        )
        return

    db = await get_sniper_db()

    # Получаем пользователя
    user = await db.get_user_by_telegram_id(message.from_user.id)
    if not user:
        await message.answer("❌ Пользователь не найден. Используйте /start")
        await state.clear()
        return

    # Проверяем промокод
    result = await db.apply_promocode(user['id'], code)

    await state.clear()

    if result['success']:
        tier_info = SUBSCRIPTION_TIERS.get(result['tier'], SUBSCRIPTION_TIERS['basic'])
        await message.answer(
            f"✅ <b>Промокод активирован!</b>\n\n"
            f"🎟 Код: <code>{code}</code>\n"
            f"{tier_info['emoji']} Тариф: <b>{tier_info['name']}</b>\n"
            f"📅 Добавлено дней: <b>{result['days']}</b>\n"
            f"⏳ Подписка до: <b>{result['expires_at'].strftime('%d.%m.%Y')}</b>\n\n"
            f"Спасибо за использование промокода!",
            parse_mode="HTML",
            reply_markup=get_back_to_menu_keyboard()
        )
        logger.info(f"Promocode {code} applied for user {message.from_user.id}: tier={result['tier']}, days={result['days']}")
    else:
        error_messages = {
            'not_found': "Промокод не найден. Проверьте правильность ввода.",
            'expired': "Срок действия промокода истёк.",
            'inactive': "Промокод деактивирован.",
            'max_uses': "Достигнуто максимальное количество использований промокода.",
            'already_used': "Вы уже использовали этот промокод.",
        }
        error_text = error_messages.get(result.get('error'), "Не удалось применить промокод.")

        await message.answer(
            f"❌ <b>Ошибка активации</b>\n\n{error_text}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Попробовать другой", callback_data="subscription_promocode")],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="sniper_subscription")]
            ])
        )
