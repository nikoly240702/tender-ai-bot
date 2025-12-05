"""
Онбординг для новых пользователей Tender AI Bot.

Пошаговое введение в функционал бота с интерактивными примерами.
"""

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from tender_sniper.database import get_sniper_db

logger = logging.getLogger(__name__)
router = Router()


class OnboardingStates(StatesGroup):
    """Состояния онбординга."""
    welcome = State()
    features = State()
    demo_search = State()
    create_filter = State()
    notifications = State()
    completed = State()


# ============================================
# КОНСТАНТЫ ДЛЯ ОНБОРДИНГА
# ============================================

ONBOARDING_STEPS = {
    "welcome": {
        "emoji": "👋",
        "title": "Добро пожаловать!",
        "text": (
            "Привет! Я **Tender AI Bot** — ваш умный помощник для поиска тендеров на zakupki.gov.ru.\n\n"
            "🎯 **Tender Sniper** — главная функция бота:\n\n"
            "**1. Мгновенный поиск** 🔍\n"
            "Быстро найдите тендеры по ключевым словам, региону и цене. "
            "AI автоматически расширит ваш запрос для более точных результатов.\n\n"
            "**2. Умные фильтры** 🎨\n"
            "Создайте фильтры с точными критериями отбора (цена, регион, тип закупки, ОКПД2).\n\n"
            "**3. Автоматический мониторинг** 🤖\n"
            "Бот сам будет искать новые тендеры каждые 5 минут и присылать уведомления "
            "о подходящих тендерах прямо в чат.\n\n"
            "**4. AI анализ** 🧠\n"
            "Автоматическая оценка релевантности (scoring система 0-100) для каждого тендера.\n\n"
            "**5. Все мои тендеры** 📊\n"
            "Единая история всех найденных тендеров с фильтрацией по цене, срокам и регионам.\n\n"
            "💡 **Начните с команды** /sniper или нажмите кнопку ниже!"
        ),
        "button": "🎯 Перейти в Tender Sniper"
    },
}


async def is_first_time_user(user_id: int) -> bool:
    """
    Проверка, впервые ли пользователь запустил бота.

    Args:
        user_id: Telegram ID пользователя

    Returns:
        True если пользователь новый
    """
    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(user_id)

        if not user:
            return True

        # Проверяем, проходил ли пользователь онбординг
        # (можно добавить поле onboarding_completed в БД)
        # Пока считаем новым, если у него нет фильтров
        filters = await db.get_user_filters(user['id'])
        return len(filters) == 0

    except Exception as e:
        logger.error(f"Ошибка проверки первого запуска: {e}")
        return False


def get_onboarding_keyboard(step: str) -> InlineKeyboardMarkup:
    """
    Создание клавиатуры для шага онбординга.

    Args:
        step: Название шага

    Returns:
        Клавиатура с кнопками
    """
    step_data = ONBOARDING_STEPS.get(step, ONBOARDING_STEPS["welcome"])

    keyboard = [
        [InlineKeyboardButton(
            text=step_data["button"],
            callback_data="onboarding_start_sniper"
        )],
        [InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data="main_menu"
        )]
    ]

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def show_onboarding_step(
    message_or_query: Message | CallbackQuery,
    step: str,
    state: FSMContext
):
    """
    Отображение шага онбординга.

    Args:
        message_or_query: Сообщение или callback query
        step: Название шага
        state: FSM контекст
    """
    step_data = ONBOARDING_STEPS.get(step, ONBOARDING_STEPS["welcome"])

    text = f"{step_data['emoji']} **{step_data['title']}**\n\n{step_data['text']}"
    keyboard = get_onboarding_keyboard(step)

    try:
        if isinstance(message_or_query, Message):
            await message_or_query.answer(text, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await message_or_query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка отображения онбординга: {e}")


# ============================================
# HANDLERS
# ============================================

@router.callback_query(F.data == "onboarding_start_sniper")
async def start_sniper_from_onboarding(callback: CallbackQuery, state: FSMContext):
    """Переход в Tender Sniper из онбординга."""
    await callback.answer("🎯 Открываю Tender Sniper...")

    # Сохраняем в БД, что пользователь прошёл онбординг
    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await db.create_or_update_user(
                telegram_id=callback.from_user.id,
                username=callback.from_user.username,
                subscription_tier='free'
            )
    except Exception as e:
        logger.error(f"Ошибка создания пользователя: {e}")

    # Открываем Tender Sniper
    from bot.handlers.sniper import show_sniper_menu
    await show_sniper_menu(callback.message, state)


async def start_onboarding(message: Message, state: FSMContext):
    """
    Запуск онбординга для нового пользователя.

    Args:
        message: Сообщение пользователя
        state: FSM контекст
    """
    logger.info(f"Запуск онбординга для пользователя {message.from_user.id}")

    await state.set_state(OnboardingStates.welcome)
    await show_onboarding_step(message, "welcome", state)


# ============================================
# ЭКСПОРТ
# ============================================

__all__ = [
    "router",
    "start_onboarding",
    "is_first_time_user"
]
