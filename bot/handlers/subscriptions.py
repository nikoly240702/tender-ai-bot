"""
Subscription Management Handlers.

Управление подписками пользователей:
- Проверка статуса подписки
- Отображение информации о тарифах
- Активация trial
- Продление подписки

Feature flag: subscriptions (config/features.yaml)
"""

import logging
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from tender_sniper.database.sqlalchemy_adapter import get_sniper_db

logger = logging.getLogger(__name__)

router = Router(name="subscriptions")


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

    builder.row(
        InlineKeyboardButton(text="◀️ В меню", callback_data="sniper_menu")
    )

    return builder.as_markup()


# ============================================
# Subscription Tiers Configuration
# ============================================

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
            '14 дней бесплатно',
        ]
    },
    'basic': {
        'name': 'Basic',
        'emoji': '⭐',
        'price': 490,
        'days': 30,
        'max_filters': 5,
        'max_notifications_per_day': 100,
        'features': [
            '5 фильтров мониторинга',
            '100 уведомлений/день',
            'Мгновенный поиск',
            'Экспорт в Excel',
            'Напоминания о тендерах',
            'Telegram-поддержка',
        ]
    },
    'premium': {
        'name': 'Premium',
        'emoji': '💎',
        'price': 990,
        'days': 30,
        'max_filters': 20,
        'max_notifications_per_day': 9999,
        'features': [
            '20 фильтров мониторинга',
            'Безлимит уведомлений',
            'Архивный поиск',
            'Расширенные настройки фильтров',
            'Доступ к бета-функциям',
            'Приоритетная поддержка',
        ]
    }
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

    # Get subscription
    subscription = await db.get_subscription(user['id'])

    if subscription and subscription['is_active']:
        tier_info = SUBSCRIPTION_TIERS.get(subscription['tier'], SUBSCRIPTION_TIERS['trial'])

        text = f"""
📦 <b>Ваша подписка</b>

{tier_info['emoji']} <b>Тариф:</b> {tier_info['name']}
📅 <b>Действует до:</b> {subscription['expires_at'][:10] if subscription['expires_at'] else 'Н/Д'}
⏳ <b>Осталось дней:</b> {subscription['days_remaining']}

<b>Лимиты:</b>
• Фильтров: {subscription['max_filters']}
• Уведомлений/день: {subscription['max_notifications_per_day']}

<b>Возможности:</b>
"""
        for feature in tier_info['features']:
            text += f"✅ {feature}\n"

        if subscription['is_trial']:
            text += "\n⚠️ <i>Пробный период закончится через {0} дней. Оформите подписку чтобы продолжить пользоваться сервисом.</i>".format(
                subscription['days_remaining']
            )
    else:
        # No active subscription
        text = """
📦 <b>Подписка</b>

❌ <b>У вас нет активной подписки</b>

Активируйте пробный период на 14 дней бесплатно или выберите тариф:
"""

    await message.answer(
        text,
        reply_markup=get_subscription_keyboard(subscription),
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
    """Show tier details and payment options."""
    await callback.answer()

    tier_name = callback.data.replace("subscription_select_", "")
    tier_info = SUBSCRIPTION_TIERS.get(tier_name)

    if not tier_info:
        await callback.message.answer("❌ Тариф не найден")
        return

    text = f"""
{tier_info['emoji']} <b>Тариф {tier_info['name']}</b>

💰 <b>Стоимость:</b> {tier_info['price']} ₽/месяц

<b>Что включено:</b>
"""
    for feature in tier_info['features']:
        text += f"✅ {feature}\n"

    text += "\n<i>Для оплаты нажмите кнопку ниже:</i>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"💳 Оплатить {tier_info['price']} ₽",
            callback_data=f"subscription_pay_{tier_name}"
        )],
        [InlineKeyboardButton(
            text="◀️ Назад к тарифам",
            callback_data="sniper_subscription"
        )],
    ])

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("subscription_pay_"))
async def callback_pay_tier(callback: CallbackQuery):
    """Initiate payment for subscription via YooKassa."""
    await callback.answer()

    tier_name = callback.data.replace("subscription_pay_", "")
    tier_info = SUBSCRIPTION_TIERS.get(tier_name)

    if not tier_info:
        await callback.message.answer("❌ Тариф не найден")
        return

    # Интеграция с YooKassa
    try:
        from tender_sniper.payments import get_yookassa_client

        client = get_yookassa_client()

        if not client.is_configured:
            # YooKassa не настроена - показываем заглушку
            await callback.message.edit_text(
                f"""
💳 <b>Оплата тарифа {tier_info['name']}</b>

Сумма: <b>{tier_info['price']} ₽</b>

🚧 <i>Платежная система временно недоступна.</i>

Для активации подписки обратитесь к администратору.
""",
                parse_mode="HTML",
                reply_markup=get_back_to_menu_keyboard()
            )
            return

        # Создаём платёж
        result = client.create_payment(
            telegram_id=callback.from_user.id,
            tier=tier_name
        )

        if 'error' in result:
            await callback.message.edit_text(
                f"❌ Ошибка создания платежа: {result['error']}",
                parse_mode="HTML",
                reply_markup=get_back_to_menu_keyboard()
            )
            return

        # Отправляем ссылку на оплату
        payment_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"💳 Оплатить {tier_info['price']} ₽",
                url=result['url']
            )],
            [InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="subscription_tiers"
            )],
        ])

        await callback.message.edit_text(
            f"""
💳 <b>Оплата тарифа {tier_info['name']}</b>

Сумма: <b>{tier_info['price']} ₽</b>

Нажмите кнопку ниже для перехода к оплате.
После успешной оплаты подписка активируется автоматически.

⏳ <i>Ссылка действительна 15 минут</i>
""",
            parse_mode="HTML",
            reply_markup=payment_keyboard
        )

        logger.info(f"Payment created for user {callback.from_user.id}, tier {tier_name}, payment_id {result['payment_id']}")

    except ImportError:
        logger.warning("YooKassa module not available")
        await callback.message.edit_text(
            f"""
💳 <b>Оплата тарифа {tier_info['name']}</b>

Сумма: <b>{tier_info['price']} ₽</b>

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

    text += "\n<i>Выберите тариф для подробностей:</i>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{info['emoji']} {info['name']} — {info['price']} ₽",
            callback_data=f"subscription_select_{tier_id}"
        )]
        for tier_id, info in SUBSCRIPTION_TIERS.items()
        if tier_id != 'trial'
    ] + [
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

    user = await db.get_user_by_telegram_id(telegram_id)
    if not user:
        return "❌ Нет подписки"

    subscription = await db.get_subscription(user['id'])

    if not subscription or not subscription['is_active']:
        return "❌ Подписка неактивна"

    tier_info = SUBSCRIPTION_TIERS.get(subscription['tier'], SUBSCRIPTION_TIERS['trial'])
    return f"{tier_info['emoji']} {tier_info['name']} ({subscription['days_remaining']} дн.)"
