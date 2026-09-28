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

from bot.config import cabinet_login_url
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

def main_menu_keyboard(monitoring_button: InlineKeyboardButton) -> InlineKeyboardMarkup:
    """Главное меню бота.

    Собрано в одном месте намеренно: меню показывается и командой, и
    коллбэком, и раньше две копии расходились при каждой правке.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        # Поиск тендеров
        [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
        [InlineKeyboardButton(text="🔍 Разовый поиск", callback_data="sniper_new_search")],
        # Найденное
        [InlineKeyboardButton(text="📊 Все тендеры", callback_data="sniper_all_tenders")],
        [InlineKeyboardButton(text="⭐ Избранное", callback_data="sniper_favorites")],
        [InlineKeyboardButton(text="🔬 AI Анализ", callback_data="analyze_start")],
        # Управление
        [monitoring_button],
        [InlineKeyboardButton(text="🌐 Веб-кабинет", url=cabinet_login_url())],
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
        keyboard = main_menu_keyboard(monitoring_button)

        sent = await message.answer(
            f"🎯 <b>TENDER SNIPER</b>\n\n"
            f"{monitoring_status}\n\n"
            f"━━━ <b>ПОИСК ТЕНДЕРОВ</b> ━━━\n"
            f"📋 <b>Мои фильтры</b> — автоматический мониторинг 24/7\n"
            f"🔍 <b>Разовый поиск</b> — быстрый поиск без сохранения\n\n"
            f"━━━ <b>НАЙДЕННОЕ</b> ━━━\n"
            f"📊 <b>Все тендеры</b> — что нашёл бот\n"
            f"⭐ <b>Избранное</b> — сохранённые вами\n"
            f"🔬 <b>AI Анализ</b> — отправьте номер любого тендера для анализа\n\n"
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
        keyboard = main_menu_keyboard(monitoring_button)

        menu_text = (
            f"🎯 <b>TENDER SNIPER</b>\n\n"
            f"{monitoring_status}\n\n"
            f"━━━ <b>ПОИСК ТЕНДЕРОВ</b> ━━━\n"
            f"📋 <b>Мои фильтры</b> — автоматический мониторинг 24/7\n"
            f"🔍 <b>Разовый поиск</b> — быстрый поиск без сохранения\n\n"
            f"━━━ <b>НАЙДЕННОЕ</b> ━━━\n"
            f"📊 <b>Все тендеры</b> — что нашёл бот\n"
            f"⭐ <b>Избранное</b> — сохранённые вами\n"
            f"🔬 <b>AI Анализ</b> — отправьте номер любого тендера для анализа\n\n"
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

        # Track menu open
        import asyncio
        try:
            from bot.analytics import track_event, EventType
            asyncio.create_task(track_event(EventType.MENU_OPENED, telegram_id=callback.from_user.id))
        except Exception:
            pass

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
            [InlineKeyboardButton(text="🔍 Диагностика фильтров", callback_data="filter_diagnostics")],
            [InlineKeyboardButton(text="🗑 Очистка истории", callback_data="cleanup_history")],
            [InlineKeyboardButton(text="« Назад", callback_data="main_menu")],
        ])

        await callback.message.edit_text(
            "⚙️ <b>НАСТРОЙКИ</b>\n\n"
            "🔔 <b>Уведомления</b>\n"
            "Включение/выключение автомониторинга, лимиты\n\n"
            "⚙️ <b>Расширенные настройки</b>\n"
            "Тихие часы, дайджест, интеграции\n\n"
            "🔍 <b>Диагностика</b>\n"
            "Статус фильтров, ошибки, последние уведомления\n\n"
            "🗑 <b>Очистка истории</b>\n"
            "Удаление старых тендеров по возрасту",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в open_settings: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


# ============================================
# ДИАГНОСТИКА ФИЛЬТРОВ
# ============================================

@router.callback_query(StateFilter("*"), F.data == "filter_diagnostics")
async def filter_diagnostics_callback(callback: CallbackQuery, state: FSMContext):
    """Показать диагностику фильтров."""
    try:
        await callback.answer("⏳ Загрузка диагностики...")
        try:
            await callback.message.edit_text(
                "🔍 <b>Диагностика фильтров</b>\n\n⏳ Загрузка...",
                parse_mode="HTML"
            )
        except Exception:
            pass

        from tender_sniper.database import get_sniper_db
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.message.edit_text("❌ Пользователь не найден")
            return

        diagnostics = await db.get_filter_diagnostics(user['id'])

        if not diagnostics:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="open_settings")]
            ])
            await callback.message.edit_text(
                "🔍 <b>Диагностика фильтров</b>\n\n"
                "У вас нет фильтров.",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            return

        text = "🔍 <b>ДИАГНОСТИКА ФИЛЬТРОВ</b>\n\n"

        for d in diagnostics:
            status = "🟢 Активен" if d['is_active'] else "🔴 Неактивен"
            errors = f"⚠️ Ошибок: {d['error_count']}" if d['error_count'] > 0 else "✅ Без ошибок"
            ai = "🤖 AI" if d['has_ai_intent'] else "❌ Нет AI"

            last_notif = "—"
            if d['last_notification_at']:
                last_dt = d['last_notification_at']
                last_notif = last_dt.strftime('%d.%m.%Y %H:%M')

            created = d['created_at'].strftime('%d.%m.%Y') if d['created_at'] else "?"

            keywords_str = ', '.join(d['keywords'][:3])

            text += (
                f"<b>#{d['id']} {d['name']}</b>\n"
                f"   {status} | {errors} | {ai}\n"
                f"   🔑 {keywords_str}\n"
                f"   📬 Уведомлений: {d['notification_count']}\n"
                f"   📅 Последнее: {last_notif}\n"
                f"   📆 Создан: {created}\n\n"
            )

        # Проверяем статус автомониторинга
        is_monitoring = await db.get_monitoring_status(callback.from_user.id)
        monitoring_text = "🟢 Автомониторинг <b>ВКЛЮЧЁН</b>" if is_monitoring else "🔴 Автомониторинг <b>ВЫКЛЮЧЕН</b>"
        text += f"\n{monitoring_text}\n"

        # Кнопки тестового поиска для каждого фильтра
        test_buttons = []
        for d in diagnostics:
            if d['is_active']:
                test_buttons.append([InlineKeyboardButton(
                    text=f"🧪 Тест #{d['id']} {d['name'][:20]}",
                    callback_data=f"diag_test_{d['id']}"
                )])

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            *test_buttons,
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="filter_diagnostics")],
            [InlineKeyboardButton(text="« Настройки", callback_data="open_settings")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка в filter_diagnostics: {e}", exc_info=True)
        try:
            await callback.message.edit_text(
                f"❌ <b>Ошибка диагностики</b>\n\n{str(e)[:300]}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="« Назад", callback_data="open_settings")]
                ])
            )
        except Exception:
            await callback.answer("❌ Ошибка", show_alert=True)


# ============================================
# ТЕСТОВЫЙ ПОИСК ДЛЯ ДИАГНОСТИКИ
# ============================================

@router.callback_query(StateFilter("*"), F.data.startswith("diag_test_"))
async def diagnostic_test_search(callback: CallbackQuery, state: FSMContext):
    """Тестовый поиск по фильтру для диагностики."""
    try:
        filter_id = int(callback.data.replace("diag_test_", ""))
        await callback.answer("⏳ Запускаю тестовый поиск...")

        await callback.message.edit_text(
            f"🧪 <b>Тестовый поиск фильтра #{filter_id}</b>\n\n⏳ Поиск на zakupki.gov.ru...",
            parse_mode="HTML"
        )

        from tender_sniper.database import get_sniper_db
        from tender_sniper.instant_search import InstantSearch

        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.message.edit_text("❌ Пользователь не найден")
            return

        filter_data = await db.get_filter_by_id(filter_id)
        if not filter_data:
            await callback.message.edit_text("❌ Фильтр не найден")
            return

        # Запускаем поиск
        searcher = InstantSearch()
        search_results = await searcher.search_by_filter(
            filter_data=filter_data,
            max_tenders=25,
            expanded_keywords=[],
            use_ai_check=False  # Без AI для скорости диагностики
        )

        matches = search_results.get('matches', [])
        total_from_rss = search_results.get('total_found', 0)

        # Проверяем сколько уже уведомлены
        already_notified = 0
        new_tenders = 0
        low_score = 0
        MIN_SCORE = 10

        tender_details = []
        for m in matches:
            tender_number = m.get('number', '')
            score = m.get('match_score', 0)
            name = m.get('name', '')[:60]

            if score < MIN_SCORE:
                low_score += 1
                tender_details.append(f"   ⬇️ {score}% | {name}")
                continue

            is_notified = await db.is_tender_notified(tender_number, user['id'])
            if is_notified:
                already_notified += 1
                tender_details.append(f"   ✅ {score}% | {name}")
            else:
                new_tenders += 1
                tender_details.append(f"   🆕 {score}% | {name}")

        # Формируем отчёт
        import json
        keywords_raw = filter_data.get('keywords', '[]')
        keywords = json.loads(keywords_raw) if isinstance(keywords_raw, str) else keywords_raw

        text = (
            f"🧪 <b>Тест фильтра #{filter_id}: {filter_data['name']}</b>\n\n"
            f"🔑 Ключевые слова: {', '.join(keywords[:5])}\n\n"
            f"📡 <b>Результаты RSS:</b>\n"
            f"   Всего от RSS: <b>{total_from_rss}</b>\n"
            f"   После скоринга: <b>{len(matches)}</b>\n\n"
            f"📊 <b>Анализ совпадений:</b>\n"
            f"   🆕 Новых (не уведомлены): <b>{new_tenders}</b>\n"
            f"   ✅ Уже отправлены: <b>{already_notified}</b>\n"
            f"   ⬇️ Низкий score (&lt;{MIN_SCORE}): <b>{low_score}</b>\n\n"
        )

        if new_tenders == 0 and already_notified > 0:
            text += "💡 <b>Вывод:</b> Все найденные тендеры уже были отправлены ранее. Новых тендеров по этим ключевым словам на zakupki.gov.ru пока нет.\n\n"
        elif new_tenders == 0 and total_from_rss == 0:
            text += "💡 <b>Вывод:</b> RSS не вернул результатов. Возможно, нет активных тендеров по этим ключевым словам.\n\n"
        elif new_tenders > 0:
            text += f"💡 <b>Вывод:</b> Есть {new_tenders} новых тендеров! Они должны прийти в ближайшем цикле мониторинга.\n\n"

        # Показываем детали (первые 10)
        if tender_details:
            text += "<b>Топ тендеров:</b>\n"
            for detail in tender_details[:10]:
                text += f"{detail}\n"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Диагностика", callback_data="filter_diagnostics")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        # Telegram limit: 4096 chars
        if len(text) > 4000:
            text = text[:3950] + "\n\n<i>...обрезано</i>"

        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка в diagnostic_test_search: {e}", exc_info=True)
        try:
            await callback.message.edit_text(
                f"❌ <b>Ошибка тестового поиска</b>\n\n{str(e)[:300]}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔍 Диагностика", callback_data="filter_diagnostics")]
                ])
            )
        except Exception:
            await callback.answer("❌ Ошибка", show_alert=True)


# ============================================
# ОЧИСТКА ИСТОРИИ ТЕНДЕРОВ
# ============================================

@router.callback_query(StateFilter("*"), F.data == "cleanup_history")
async def cleanup_history_callback(callback: CallbackQuery, state: FSMContext):
    """Меню очистки истории тендеров."""
    try:
        await callback.answer()

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Старше 30 дней", callback_data="cleanup_30")],
            [InlineKeyboardButton(text="🗑 Старше 60 дней", callback_data="cleanup_60")],
            [InlineKeyboardButton(text="🗑 Старше 90 дней", callback_data="cleanup_90")],
            [InlineKeyboardButton(text="🗑 Старше 120 дней", callback_data="cleanup_120")],
            [InlineKeyboardButton(text="« Назад", callback_data="open_settings")],
        ])

        await callback.message.edit_text(
            "🗑 <b>ОЧИСТКА ИСТОРИИ ТЕНДЕРОВ</b>\n\n"
            "Выберите возраст тендеров для удаления.\n\n"
            "⚠️ <b>Внимание:</b> удалённые тендеры нельзя восстановить. "
            "Избранные тендеры НЕ удаляются.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в cleanup_history: {e}", exc_info=True)
        await callback.answer("❌ Ошибка", show_alert=True)


@router.callback_query(StateFilter("*"), F.data.startswith("cleanup_"))
async def cleanup_execute_callback(callback: CallbackQuery, state: FSMContext):
    """Выполнить очистку истории тендеров по возрасту."""
    try:
        days_str = callback.data.replace("cleanup_", "")
        if days_str == "history":
            return  # Это сам пункт меню, обрабатывается выше

        days = int(days_str)

        await callback.answer(f"⏳ Удаление тендеров старше {days} дней...")

        from tender_sniper.database import get_sniper_db
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return

        deleted_count = await db.cleanup_old_notifications(user['id'], days)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Ещё очистка", callback_data="cleanup_history")],
            [InlineKeyboardButton(text="« Настройки", callback_data="open_settings")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        if deleted_count > 0:
            await callback.message.edit_text(
                f"✅ <b>Очистка завершена!</b>\n\n"
                f"🗑 Удалено тендеров: <b>{deleted_count}</b>\n"
                f"📅 Критерий: старше {days} дней",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                f"ℹ️ <b>Нечего удалять</b>\n\n"
                f"Тендеров старше {days} дней не найдено.",
                reply_markup=keyboard,
                parse_mode="HTML"
            )

    except ValueError:
        await callback.answer("❌ Некорректный параметр", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка в cleanup_execute: {e}", exc_info=True)
        await callback.answer("❌ Ошибка при очистке", show_alert=True)


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
