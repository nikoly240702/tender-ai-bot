"""
Приоритетный роутер для кнопок меню.

Этот роутер регистрируется ПЕРВЫМ и обрабатывает кнопки меню
в ЛЮБОМ FSM состоянии, прерывая текущий процесс.

Также отвечает за удаление старых сообщений при переходе между меню.
"""

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)

# Создаем роутер с высоким приоритетом
router = Router(name="menu_priority")


# ============================================
# КНОПКИ МЕНЮ - РАБОТАЮТ В ЛЮБОМ СОСТОЯНИИ FSM
# ============================================

# Список системных кнопок меню
MENU_BUTTONS = [
    "🏠 Главное меню",
    "🏠 В главное меню",
    "🎯 Tender Sniper",
    "📊 Мои фильтры",
    "📋 Мои фильтры",
    "📊 Все мои тендеры",
    "⭐ Избранное",
    "📈 Статистика",
    "🔍 Новый поиск",
]


# ============================================
# УПРАВЛЕНИЕ СООБЩЕНИЯМИ
# ============================================

async def safe_delete_message(message: Message):
    """Безопасное удаление сообщения."""
    try:
        await message.delete()
    except Exception:
        pass  # Игнорируем ошибки удаления


async def delete_tracked_messages(state: FSMContext, bot: Bot, chat_id: int):
    """
    Удаляет отслеживаемые сообщения из FSM данных.
    Это помогает убрать старые меню при переходе к новому.
    """
    data = await state.get_data()
    tracked_messages: List[int] = data.get('tracked_message_ids', [])

    for msg_id in tracked_messages:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass  # Игнорируем ошибки (сообщение уже удалено или устарело)

    # Очищаем список
    await state.update_data(tracked_message_ids=[])


async def track_message(state: FSMContext, message_id: int):
    """Добавляет ID сообщения в список отслеживаемых."""
    data = await state.get_data()
    tracked_messages: List[int] = data.get('tracked_message_ids', [])
    tracked_messages.append(message_id)
    # Храним только последние 5 сообщений для экономии памяти
    await state.update_data(tracked_message_ids=tracked_messages[-5:])


@router.message(StateFilter("*"), F.text == "🏠 Главное меню")
@router.message(StateFilter("*"), F.text == "🏠 В главное меню")
async def priority_main_menu(message: Message, state: FSMContext):
    """Главное меню - работает в любом состоянии."""
    # Удаляем предыдущие отслеживаемые сообщения
    await delete_tracked_messages(state, message.bot, message.chat.id)

    # Очищаем FSM состояние
    current_state = await state.get_state()
    if current_state:
        logger.info(f"Прерывание FSM состояния {current_state} для главного меню")
        await state.clear()

    # Показываем главное меню
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Мгновенный поиск", callback_data="sniper_new_search")],
        [InlineKeyboardButton(text="➕ Создать фильтр", callback_data="sniper_create_filter")],
        [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
        [InlineKeyboardButton(text="📊 Все мои тендеры", callback_data="sniper_all_tenders")],
        [InlineKeyboardButton(text="📈 Статистика", callback_data="sniper_stats")],
    ])

    sent = await message.answer(
        "🏠 <b>Главное меню</b>\n\n"
        "Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    # Отслеживаем новое сообщение
    await track_message(state, sent.message_id)


@router.message(StateFilter("*"), F.text == "🎯 Tender Sniper")
async def priority_tender_sniper(message: Message, state: FSMContext):
    """Tender Sniper меню - работает в любом состоянии."""
    # Удаляем предыдущие отслеживаемые сообщения
    await delete_tracked_messages(state, message.bot, message.chat.id)

    current_state = await state.get_state()
    if current_state:
        logger.info(f"Прерывание FSM состояния {current_state} для Tender Sniper")
        await state.clear()

    # Получаем статус автомониторинга для динамической кнопки паузы
    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()
    is_monitoring_enabled = await db.get_monitoring_status(message.from_user.id)

    # Кнопка паузы/возобновления
    if is_monitoring_enabled:
        monitoring_button = InlineKeyboardButton(
            text="⏸️ Пауза автомониторинга",
            callback_data="sniper_pause_monitoring"
        )
        monitoring_status = "🟢 <b>Автомониторинг активен</b>"
    else:
        monitoring_button = InlineKeyboardButton(
            text="▶️ Возобновить автомониторинг",
            callback_data="sniper_resume_monitoring"
        )
        monitoring_status = "🔴 <b>Автомониторинг на паузе</b>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Новый поиск", callback_data="sniper_new_search")],
        [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
        [InlineKeyboardButton(text="📊 Все мои тендеры", callback_data="sniper_all_tenders")],
        [monitoring_button],
        [InlineKeyboardButton(text="📈 Статистика", callback_data="sniper_stats")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    sent = await message.answer(
        f"🎯 <b>Tender Sniper</b>\n\n"
        f"{monitoring_status}\n\n"
        f"Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await track_message(state, sent.message_id)


@router.message(StateFilter("*"), F.text.in_(["📊 Мои фильтры", "📋 Мои фильтры"]))
async def priority_my_filters(message: Message, state: FSMContext):
    """Мои фильтры - работает в любом состоянии."""
    # Удаляем предыдущие отслеживаемые сообщения
    await delete_tracked_messages(state, message.bot, message.chat.id)

    current_state = await state.get_state()
    if current_state:
        logger.info(f"Прерывание FSM состояния {current_state} для Мои фильтры")
        await state.clear()

    # Импортируем и вызываем handler
    from bot.handlers.sniper import show_my_filters_message
    await show_my_filters_message(message)


@router.message(StateFilter("*"), F.text == "📊 Все мои тендеры")
async def priority_all_tenders(message: Message, state: FSMContext):
    """Все мои тендеры - работает в любом состоянии."""
    # Удаляем предыдущие отслеживаемые сообщения
    await delete_tracked_messages(state, message.bot, message.chat.id)

    current_state = await state.get_state()
    if current_state:
        logger.info(f"Прерывание FSM состояния {current_state} для Все мои тендеры")
        await state.clear()

    # Импортируем функции
    from bot.handlers.all_tenders import get_all_user_tenders, show_tenders_menu

    try:
        loading_msg = await message.answer("⏳ Загрузка ваших тендеров...")
        tenders = await get_all_user_tenders(message.from_user.id)

        try:
            await loading_msg.delete()
        except:
            pass

        await show_tenders_menu(message, tenders, state)
    except Exception as e:
        logger.error(f"Ошибка загрузки тендеров: {e}")
        await message.answer(
            "❌ Ошибка загрузки тендеров. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])
        )


@router.message(StateFilter("*"), F.text == "⭐ Избранное")
async def priority_favorites(message: Message, state: FSMContext):
    """Избранное - работает в любом состоянии."""
    # Удаляем предыдущие отслеживаемые сообщения
    await delete_tracked_messages(state, message.bot, message.chat.id)

    current_state = await state.get_state()
    if current_state:
        logger.info(f"Прерывание FSM состояния {current_state} для Избранное")
        await state.clear()

    # Импортируем и вызываем handler
    from bot.handlers.user_management import cmd_favorites
    await cmd_favorites(message)


@router.message(StateFilter("*"), F.text == "📈 Статистика")
async def priority_stats(message: Message, state: FSMContext):
    """Статистика - работает в любом состоянии."""
    # Удаляем предыдущие отслеживаемые сообщения
    await delete_tracked_messages(state, message.bot, message.chat.id)

    current_state = await state.get_state()
    if current_state:
        logger.info(f"Прерывание FSM состояния {current_state} для Статистика")
        await state.clear()

    # Импортируем и вызываем handler
    from bot.handlers.sniper import show_stats_callback
    # Создаем фейковый callback для вызова
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    # Вызываем напрямую логику статистики
    from tender_sniper.database import get_sniper_db
    from datetime import datetime

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(message.from_user.id)

        if not user:
            await message.answer(
                "❌ Пользователь не найден. Нажмите /start для регистрации.",
                reply_markup=keyboard
            )
            return

        # Получаем статистику
        filters = await db.get_user_filters(user['id'])
        active_filters = [f for f in filters if f.get('is_active')]
        stats = await db.get_user_stats(user['id'])

        await message.answer(
            f"📈 <b>Ваша статистика</b>\n\n"
            f"<b>Тариф:</b> {user['subscription_tier'].title()}\n"
            f"<b>Фильтров:</b> {len(filters)} (активных: {len(active_filters)})\n"
            f"<b>Уведомлений сегодня:</b> {stats.get('notifications_today', 0)}\n"
            f"<b>Уведомлений за все время:</b> {stats.get('total_notifications', 0)}\n"
            f"<b>Дата регистрации:</b> {user.get('created_at', 'Неизвестно')[:10] if user.get('created_at') else 'Неизвестно'}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        await message.answer(
            "❌ Ошибка получения статистики. Попробуйте позже.",
            reply_markup=keyboard
        )


# ============================================
# INLINE CALLBACKS - РАБОТАЮТ В ЛЮБОМ СОСТОЯНИИ FSM
# ============================================

@router.callback_query(StateFilter("*"), F.data == "main_menu")
async def priority_main_menu_callback(callback: CallbackQuery, state: FSMContext):
    """Callback главного меню - работает в любом состоянии."""
    await callback.answer()

    current_state = await state.get_state()
    if current_state:
        logger.info(f"Прерывание FSM состояния {current_state} для main_menu callback")
        await state.clear()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Мгновенный поиск", callback_data="sniper_new_search")],
        [InlineKeyboardButton(text="➕ Создать фильтр", callback_data="sniper_create_filter")],
        [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
        [InlineKeyboardButton(text="📊 Все мои тендеры", callback_data="sniper_all_tenders")],
        [InlineKeyboardButton(text="📈 Статистика", callback_data="sniper_stats")],
    ])

    try:
        await callback.message.edit_text(
            "🏠 <b>Главное меню</b>\n\n"
            "Выберите действие:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        # Если не удалось отредактировать - отправляем новое
        sent = await callback.message.answer(
            "🏠 <b>Главное меню</b>\n\n"
            "Выберите действие:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await track_message(state, sent.message_id)


@router.callback_query(StateFilter("*"), F.data == "sniper_my_filters")
async def priority_my_filters_callback(callback: CallbackQuery, state: FSMContext):
    """Callback Мои фильтры - работает в любом состоянии."""
    current_state = await state.get_state()
    if current_state:
        logger.info(f"Прерывание FSM состояния {current_state} для sniper_my_filters callback")
        await state.clear()

    # Импортируем и вызываем handler для отображения фильтров
    # НЕ вызываем callback.answer() - это сделает show_my_filters
    from bot.handlers.sniper import show_my_filters
    await show_my_filters(callback)


@router.callback_query(StateFilter("*"), F.data == "sniper_menu")
async def priority_sniper_menu_callback(callback: CallbackQuery, state: FSMContext):
    """Callback меню Sniper - работает в любом состоянии."""
    current_state = await state.get_state()
    if current_state:
        logger.info(f"Прерывание FSM состояния {current_state} для sniper_menu callback")
        await state.clear()

    # Вызываем оригинальный handler с динамической кнопкой паузы
    from bot.handlers.sniper import show_sniper_menu
    await show_sniper_menu(callback)
