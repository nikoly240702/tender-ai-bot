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

# Контакт для связи
DEVELOPER_CONTACT = "@nikolai_chizhik"

# Сообщение об ошибке для бета-теста
BETA_ERROR_MESSAGE = (
    "❌ <b>Произошла ошибка</b>\n\n"
    "🧪 Бот находится в стадии бета-тестирования.\n\n"
    f"Если вы столкнулись с ошибкой или багом, пожалуйста, "
    f"свяжитесь с разработчиком: {DEVELOPER_CONTACT}\n\n"
    "Попробуйте нажать /start для перезапуска."
)

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
    try:
        # Удаляем предыдущие отслеживаемые сообщения
        await delete_tracked_messages(state, message.bot, message.chat.id)

        # Очищаем FSM состояние
        current_state = await state.get_state()
        if current_state:
            logger.info(f"Прерывание FSM состояния {current_state} для главного меню")
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
            monitoring_status = "🟢 Автомониторинг активен"
        else:
            monitoring_button = InlineKeyboardButton(
                text="▶️ Возобновить автомониторинг",
                callback_data="sniper_resume_monitoring"
            )
            monitoring_status = "🔴 Автомониторинг на паузе"

        # Показываем главное меню
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            # Поиск тендеров
            [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
            [InlineKeyboardButton(text="🔍 Разовый поиск", callback_data="sniper_new_search")],
            # Найденное
            [InlineKeyboardButton(text="📊 Все тендеры", callback_data="sniper_all_tenders")],
            [InlineKeyboardButton(text="⭐ Избранное", callback_data="sniper_favorites")],
            # Управление
            [monitoring_button],
            # Настройки
            [
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="open_settings"),
                InlineKeyboardButton(text="🎛 Фильтры 🧪", callback_data="sniper_extended_settings"),
            ],
            [
                InlineKeyboardButton(text="📈 Статистика", callback_data="sniper_stats"),
                InlineKeyboardButton(text="💎 Тарифы", callback_data="sniper_plans"),
            ],
            [InlineKeyboardButton(text="❓ Помощь", callback_data="sniper_help")],
        ])

        sent = await message.answer(
            f"🎯 <b>TENDER SNIPER</b>\n\n"
            f"{monitoring_status}\n\n"
            f"━━━ <b>ПОИСК ТЕНДЕРОВ</b> ━━━\n"
            f"📋 <b>Мои фильтры</b> — автоматический мониторинг 24/7\n"
            f"🔍 <b>Разовый поиск</b> — быстрый поиск без сохранения\n\n"
            f"━━━ <b>НАЙДЕННОЕ</b> ━━━\n"
            f"📊 <b>Все тендеры</b> — что нашёл бот\n"
            f"⭐ <b>Избранное</b> — сохранённые вами\n\n"
            f"━━━ <b>НАСТРОЙКИ</b> ━━━\n"
            f"⚙️ Уведомления, интеграции, профиль\n"
            f"🎛 Расширенные настройки фильтров",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        # Отслеживаем новое сообщение
        await track_message(state, sent.message_id)
    except Exception as e:
        logger.error(f"Ошибка в главном меню: {e}", exc_info=True)
        await message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


@router.message(StateFilter("*"), F.text == "🎯 Tender Sniper")
async def priority_tender_sniper(message: Message, state: FSMContext):
    """Tender Sniper меню - работает в любом состоянии."""
    try:
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
    except Exception as e:
        logger.error(f"Ошибка в Tender Sniper меню: {e}", exc_info=True)
        await message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


@router.message(StateFilter("*"), F.text.in_(["📊 Мои фильтры", "📋 Мои фильтры"]))
async def priority_my_filters(message: Message, state: FSMContext):
    """Мои фильтры - работает в любом состоянии."""
    try:
        # Удаляем предыдущие отслеживаемые сообщения
        await delete_tracked_messages(state, message.bot, message.chat.id)

        current_state = await state.get_state()
        if current_state:
            logger.info(f"Прерывание FSM состояния {current_state} для Мои фильтры")
            await state.clear()

        # Импортируем и вызываем handler
        from bot.handlers.sniper import show_my_filters_message
        await show_my_filters_message(message)
    except Exception as e:
        logger.error(f"Ошибка в Мои фильтры: {e}", exc_info=True)
        await message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


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

        await show_tenders_menu(message, tenders, {'sort_by': 'date_desc'}, state)
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
    try:
        # Удаляем предыдущие отслеживаемые сообщения
        await delete_tracked_messages(state, message.bot, message.chat.id)

        current_state = await state.get_state()
        if current_state:
            logger.info(f"Прерывание FSM состояния {current_state} для Избранное")
            await state.clear()

        # Импортируем и вызываем handler
        from bot.handlers.user_management import favorites_command
        await favorites_command(message)
    except Exception as e:
        logger.error(f"Ошибка в Избранное: {e}", exc_info=True)
        await message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


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


@router.message(StateFilter("*"), F.text.in_(["⏸️ Пауза мониторинга", "▶️ Вкл. мониторинг"]))
async def priority_toggle_monitoring(message: Message, state: FSMContext):
    """Переключение автомониторинга - работает в любом состоянии."""
    try:
        from tender_sniper.database import get_sniper_db
        from bot.handlers.start import get_main_keyboard
        db = await get_sniper_db()

        # Определяем новый статус по тексту кнопки
        if message.text == "⏸️ Пауза мониторинга":
            new_status = False  # Выключаем
        else:
            new_status = True  # Включаем

        # Устанавливаем новый статус
        await db.set_monitoring_status(message.from_user.id, new_status)

        if new_status:
            status_text = "🟢 <b>Автомониторинг включён!</b>\n\nВы будете получать уведомления о новых тендерах по вашим фильтрам."
        else:
            status_text = "🔴 <b>Автомониторинг приостановлен</b>\n\nУведомления временно отключены. Нажмите кнопку ещё раз, чтобы возобновить."

        # Обновляем reply keyboard с новым статусом кнопки
        reply_keyboard = get_main_keyboard(new_status)

        await message.answer(status_text, reply_markup=reply_keyboard, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка переключения мониторинга: {e}", exc_info=True)
        await message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


# ============================================
# INLINE CALLBACKS - РАБОТАЮТ В ЛЮБОМ СОСТОЯНИИ FSM
# ============================================

@router.callback_query(StateFilter("*"), F.data == "main_menu")
async def priority_main_menu_callback(callback: CallbackQuery, state: FSMContext):
    """Callback главного меню - работает в любом состоянии."""
    try:
        # ВАЖНО: Сначала показываем загрузку, потом делаем async операции
        await callback.answer("⏳ Загрузка...")

        # Показываем индикатор загрузки СРАЗУ (до async операций)
        try:
            await callback.message.edit_text(
                "🎯 <b>TENDER SNIPER</b>\n\n⏳ Загрузка меню...",
                parse_mode="HTML"
            )
        except Exception:
            pass  # Если не удалось - не страшно

        current_state = await state.get_state()
        if current_state:
            logger.info(f"Прерывание FSM состояния {current_state} для main_menu callback")
            await state.clear()

        # Получаем статус автомониторинга для динамической кнопки паузы
        from tender_sniper.database import get_sniper_db
        db = await get_sniper_db()
        is_monitoring_enabled = await db.get_monitoring_status(callback.from_user.id)

        # Кнопка паузы/возобновления
        if is_monitoring_enabled:
            monitoring_button = InlineKeyboardButton(
                text="⏸️ Пауза автомониторинга",
                callback_data="sniper_pause_monitoring"
            )
            monitoring_status = "🟢 Автомониторинг активен"
        else:
            monitoring_button = InlineKeyboardButton(
                text="▶️ Возобновить автомониторинг",
                callback_data="sniper_resume_monitoring"
            )
            monitoring_status = "🔴 Автомониторинг на паузе"

        # Главное меню
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            # Поиск тендеров
            [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
            [InlineKeyboardButton(text="🔍 Разовый поиск", callback_data="sniper_new_search")],
            # Найденное
            [InlineKeyboardButton(text="📊 Все тендеры", callback_data="sniper_all_tenders")],
            [InlineKeyboardButton(text="⭐ Избранное", callback_data="sniper_favorites")],
            # Управление
            [monitoring_button],
            # Настройки
            [
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="open_settings"),
                InlineKeyboardButton(text="🎛 Фильтры 🧪", callback_data="sniper_extended_settings"),
            ],
            [
                InlineKeyboardButton(text="📈 Статистика", callback_data="sniper_stats"),
                InlineKeyboardButton(text="💎 Тарифы", callback_data="sniper_plans"),
            ],
            [InlineKeyboardButton(text="❓ Помощь", callback_data="sniper_help")],
        ])

        menu_text = (
            f"🎯 <b>TENDER SNIPER</b>\n\n"
            f"{monitoring_status}\n\n"
            f"━━━ <b>ПОИСК ТЕНДЕРОВ</b> ━━━\n"
            f"📋 <b>Мои фильтры</b> — автоматический мониторинг 24/7\n"
            f"🔍 <b>Разовый поиск</b> — быстрый поиск без сохранения\n\n"
            f"━━━ <b>НАЙДЕННОЕ</b> ━━━\n"
            f"📊 <b>Все тендеры</b> — что нашёл бот\n"
            f"⭐ <b>Избранное</b> — сохранённые вами\n\n"
            f"━━━ <b>НАСТРОЙКИ</b> ━━━\n"
            f"⚙️ Уведомления, интеграции, профиль\n"
            f"🎛 Расширенные настройки фильтров"
        )

        try:
            await callback.message.edit_text(
                menu_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception:
            # Если не удалось отредактировать - отправляем новое
            sent = await callback.message.answer(
                menu_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await track_message(state, sent.message_id)
    except Exception as e:
        logger.error(f"Ошибка в main_menu callback: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(StateFilter("*"), F.data == "sniper_my_filters")
async def priority_my_filters_callback(callback: CallbackQuery, state: FSMContext):
    """Callback Мои фильтры - работает в любом состоянии."""
    try:
        # Мгновенный ответ + индикатор загрузки
        await callback.answer("⏳ Загрузка фильтров...")
        try:
            await callback.message.edit_text(
                "📋 <b>Мои фильтры</b>\n\n⏳ Загрузка...",
                parse_mode="HTML"
            )
        except Exception:
            pass

        current_state = await state.get_state()
        if current_state:
            logger.info(f"Прерывание FSM состояния {current_state} для sniper_my_filters callback")
            await state.clear()

        # Импортируем и вызываем handler для отображения фильтров
        from bot.handlers.sniper import show_my_filters
        await show_my_filters(callback)
    except Exception as e:
        logger.error(f"Ошибка в callback sniper_my_filters: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(StateFilter("*"), F.data == "sniper_menu")
async def priority_sniper_menu_callback(callback: CallbackQuery, state: FSMContext):
    """Callback меню Sniper - работает в любом состоянии."""
    try:
        # Мгновенный ответ + индикатор загрузки
        await callback.answer("⏳ Загрузка...")
        try:
            await callback.message.edit_text(
                "🎯 <b>TENDER SNIPER</b>\n\n⏳ Загрузка...",
                parse_mode="HTML"
            )
        except Exception:
            pass

        current_state = await state.get_state()
        if current_state:
            logger.info(f"Прерывание FSM состояния {current_state} для sniper_menu callback")
            # Сохраняем all_tenders перед очисткой state (чтобы не терять загруженные данные)
            data = await state.get_data()
            all_tenders = data.get('all_tenders')

            await state.clear()

            # Восстанавливаем all_tenders если были
            if all_tenders:
                await state.update_data(all_tenders=all_tenders)

        # Вызываем оригинальный handler с динамической кнопкой паузы
        from bot.handlers.sniper import show_sniper_menu
        await show_sniper_menu(callback)
    except Exception as e:
        logger.error(f"Ошибка в callback sniper_menu: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(StateFilter("*"), F.data == "open_settings")
async def open_settings_callback(callback: CallbackQuery, state: FSMContext):
    """Открыть настройки пользователя."""
    try:
        await callback.answer()
        current_state = await state.get_state()
        if current_state:
            await state.clear()

        # Показываем меню настроек
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔔 Уведомления", callback_data="settings_notifications")],
            [InlineKeyboardButton(text="⚙️ Расширенные настройки", callback_data="settings_advanced")],
            [InlineKeyboardButton(text="« Назад", callback_data="main_menu")],
        ])

        await callback.message.edit_text(
            "⚙️ <b>НАСТРОЙКИ</b>\n\n"
            "🔔 <b>Уведомления</b>\n"
            "Включение/выключение автомониторинга, лимиты\n\n"
            "⚙️ <b>Расширенные настройки</b>\n"
            "Тихие часы, дайджест, интеграции (CRM, Email), профиль компании",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в open_settings: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(StateFilter("*"), F.data == "sniper_favorites")
async def sniper_favorites_callback(callback: CallbackQuery, state: FSMContext):
    """Избранное - callback."""
    try:
        await callback.answer()
        current_state = await state.get_state()
        if current_state:
            await state.clear()

        # Получаем избранные тендеры
        from tender_sniper.database import get_sniper_db
        from bot.utils.tender_db_helpers import get_user_favorites
        from bot.utils.tender_notifications import format_favorites_list

        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not sniper_user:
            await callback.message.edit_text("❌ Пользователь не найден")
            return

        favorites = await get_user_favorites(sniper_user['id'], limit=50)

        if not favorites:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="main_menu")]
            ])
            await callback.message.edit_text(
                "⭐ <b>ИЗБРАННОЕ</b>\n\n"
                "У вас пока нет избранных тендеров.\n\n"
                "Используйте кнопку '⭐ В избранное' в уведомлениях о тендерах, "
                "чтобы добавить их сюда.",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            return

        # Форматируем список
        favorites_text = format_favorites_list(favorites, callback.from_user.username or "Пользователь")

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📄 Получить HTML отчет", callback_data="html_favorites")],
            [InlineKeyboardButton(text="« Назад", callback_data="main_menu")]
        ])

        await callback.message.edit_text(
            text=favorites_text,
            reply_markup=keyboard,
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Ошибка в sniper_favorites: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)
