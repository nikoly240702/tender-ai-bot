"""
Обработчики команды /start и главного меню.
"""

import logging
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext

logger = logging.getLogger(__name__)
router = Router()


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """
    Возвращает постоянную клавиатуру управления ботом.
    Отображается справа от текстовой строки.
    """
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 Главное меню")],
            [KeyboardButton(text="🎯 Tender Sniper"), KeyboardButton(text="📊 Мои фильтры")],
            [KeyboardButton(text="📊 Все мои тендеры")],
            [KeyboardButton(text="⭐ Избранное"), KeyboardButton(text="📈 Статистика")]
        ],
        resize_keyboard=True,
        persistent=True  # Клавиатура остается видимой всегда
    )
    return keyboard


@router.message(CommandStart())
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """
    Обработчик команды /start.
    Приветствует пользователя и показывает главное меню.
    Для новых пользователей запускает онбординг.

    ВАЖНО: Работает в любом состоянии FSM для возврата в главное меню.
    """
    # Получаем текущее состояние для логирования
    current_state = await state.get_state()
    if current_state:
        logger.info(f"Пользователь {message.from_user.id} вызвал /start из состояния {current_state}")

    # Очищаем любое предыдущее состояние
    await state.clear()

    # Проверяем, новый ли пользователь
    # Если команда /start onboarding - принудительно показываем онбординг
    force_onboarding = message.text and "onboarding" in message.text.lower()

    if force_onboarding:
        logger.info(f"Принудительный запуск онбординга для пользователя {message.from_user.id}")
        from bot.handlers.onboarding import start_onboarding
        await start_onboarding(message, state)
        return

    # Проверяем, новый ли пользователь (автоматический онбординг)
    try:
        from bot.handlers.onboarding import is_first_time_user, start_onboarding

        if await is_first_time_user(message.from_user.id):
            logger.info(f"Первый запуск для пользователя {message.from_user.id} - показываем онбординг")
            await start_onboarding(message, state)
            return
    except Exception as e:
        logger.error(f"Ошибка проверки нового пользователя: {e}")

    welcome_text = (
        "👋 <b>Добро пожаловать в Tender Sniper!</b>\n\n"
        "🎯 Автоматический мониторинг и уведомления о тендерах zakupki.gov.ru\n\n"
        "<b>Что я умею:</b>\n"
        "🔍 Мгновенный поиск по вашим критериям\n"
        "🎯 Умное сопоставление (scoring 0-100)\n"
        "📱 Автоматические уведомления о новых тендерах\n"
        "📊 Продвинутые фильтры (регион, закон, тип)\n\n"
        "<b>Ваш тариф:</b> 🆓 Бесплатный\n"
        "• 5 фильтров мониторинга\n"
        "• 10 уведомлений в день\n\n"
        "<i>Нажмите кнопку ниже для начала!</i>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Запустить Tender Sniper", callback_data="sniper_menu")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="sniper_help")]
    ])

    await message.answer(
        welcome_text,
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

    await message.answer(
        "Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help."""

    help_text = (
        "❓ <b>Справка Tender Sniper</b>\n\n"

        "<b>Что такое Tender Sniper?</b>\n"
        "Это система автоматического мониторинга новых тендеров на zakupki.gov.ru. "
        "Вы создаете фильтры с вашими критериями, и бот автоматически уведомляет вас "
        "о подходящих тендерах.\n\n"

        "<b>Как это работает?</b>\n"
        "1. Создайте фильтр с ключевыми словами и критериями\n"
        "2. Бот проверяет новые тендеры каждые 5 минут\n"
        "3. При совпадении вы получаете уведомление\n"
        "4. Можете сразу перейти к анализу или открыть на zakupki.gov.ru\n\n"

        "<b>Scoring (релевантность)</b>\n"
        "Каждый тендер оценивается по шкале 0-100:\n"
        "• 80-100: Отличное совпадение 🔥\n"
        "• 60-79: Хорошее совпадение ✨\n"
        "• 40-59: Среднее совпадение 📌\n\n"

        "<b>Квоты и лимиты</b>\n"
        "Зависят от вашего тарифа:\n"
        "• Free: 5 фильтров, 10 уведомлений/день\n"
        "• Basic: 15 фильтров, 50 уведомлений/день\n"
        "• Premium: Unlimited"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Открыть Tender Sniper", callback_data="sniper_menu")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await message.answer(help_text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "main_menu")
async def return_to_main_menu(callback: CallbackQuery, state: FSMContext):
    """
    Возврат в главное меню из любого состояния.
    Очищает FSM state и показывает стартовое сообщение.
    """
    await callback.answer()

    # Очищаем любое состояние
    await state.clear()

    welcome_text = (
        "👋 <b>Добро пожаловать в Tender Sniper!</b>\n\n"
        "🎯 Автоматический мониторинг и уведомления о тендерах zakupki.gov.ru\n\n"
        "<b>Что я умею:</b>\n"
        "🔍 Мгновенный поиск по вашим критериям\n"
        "🎯 Умное сопоставление (scoring 0-100)\n"
        "📱 Автоматические уведомления о новых тендерах\n"
        "📊 Продвинутые фильтры (регион, закон, тип)\n\n"
        "<b>Ваш тариф:</b> 🆓 Бесплатный\n"
        "• 5 фильтров мониторинга\n"
        "• 15 уведомлений в день\n\n"
        "<i>Нажмите кнопку ниже для начала!</i>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Запустить Tender Sniper", callback_data="sniper_menu")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="sniper_help")]
    ])

    await callback.message.edit_text(
        welcome_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "start_onboarding")
async def callback_start_onboarding(callback: CallbackQuery, state: FSMContext):
    """Запуск онбординга по кнопке."""
    await callback.answer("👋 Запускаю экскурсию...")

    from bot.handlers.onboarding import start_onboarding
    await start_onboarding(callback.message, state)


# ============================================
# ОБРАБОТЧИКИ ПОСТОЯННОЙ КЛАВИАТУРЫ
# ============================================

@router.message(F.text == "🏠 Главное меню")
async def keyboard_main_menu(message: Message, state: FSMContext):
    """Обработчик кнопки 'Главное меню' из постоянной клавиатуры."""
    # Используем существующую логику cmd_start
    await cmd_start(message, state)


@router.message(F.text == "🎯 Tender Sniper")
async def keyboard_tender_sniper(message: Message):
    """Обработчик кнопки 'Tender Sniper' из постоянной клавиатуры."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Мгновенный поиск", callback_data="sniper_new_search")],
        [InlineKeyboardButton(text="➕ Создать фильтр", callback_data="sniper_create_filter")],
        [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
        [InlineKeyboardButton(text="📊 Все мои тендеры", callback_data="sniper_all_tenders")]
    ])

    await message.answer(
        "🎯 <b>Tender Sniper</b>\n\nВыберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.message(F.text == "📊 Мои фильтры")
async def keyboard_my_filters(message: Message):
    """Обработчик кнопки 'Мои фильтры' из постоянной клавиатуры."""
    # Импортируем и вызываем напрямую handler из sniper.py
    from bot.handlers.sniper import show_my_filters_message
    await show_my_filters_message(message)


@router.message(F.text == "📊 Все мои тендеры")
async def keyboard_all_tenders(message: Message, state: FSMContext):
    """Обработчик кнопки 'Все мои тендеры' из постоянной клавиатуры."""
    # Импортируем функции напрямую
    from bot.handlers.all_tenders import get_all_user_tenders, show_tenders_menu, AllTendersStates

    try:
        # Получаем все тендеры напрямую
        tenders = await get_all_user_tenders(message.from_user.id)

        if not tenders:
            await message.answer(
                "📊 <b>Все мои тендеры</b>\n\n"
                "У вас пока нет найденных тендеров.\n\n"
                "Используйте:\n"
                "• 🔍 <b>Мгновенный поиск</b> для быстрого поиска\n"
                "• 🎨 <b>Фильтры</b> для автоматического мониторинга",
                parse_mode="HTML"
            )
            return

        # Сохраняем тендеры в состоянии
        await state.update_data(all_tenders=tenders, filter_params={'sort_by': 'date_desc'})
        await state.set_state(AllTendersStates.viewing_list)

        # Показываем меню фильтрации
        await show_tenders_menu(message, tenders, {}, state)

    except Exception as e:
        logger.error(f"Ошибка загрузки тендеров: {e}", exc_info=True)
        await message.answer("❌ Ошибка при загрузке тендеров")


@router.message(F.text == "⭐ Избранное")
async def keyboard_favorites(message: Message):
    """Обработчик кнопки 'Избранное' из постоянной клавиатуры."""
    # Импортируем обработчик из user_management
    from bot.handlers.user_management import favorites_command
    await favorites_command(message)


@router.message(F.text == "📈 Статистика")
async def keyboard_stats(message: Message):
    """Обработчик кнопки 'Статистика' из постоянной клавиатуры."""
    # Импортируем обработчик из user_management
    from bot.handlers.user_management import stats_command
    await stats_command(message)


# Старые handlers отключены - теперь используем только Tender Sniper
# @router.message(F.text == "🔍 Новый поиск")
# async def start_new_search(message: Message, state: FSMContext):
#     pass
