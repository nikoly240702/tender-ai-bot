"""
Команды для управления пользовательскими данными.

/favorites - избранные тендеры
/hidden - скрытые тендеры
/stats - статистика пользователя
/settings - настройки профиля
/setprofile - установка профиля компании
"""

import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from tender_sniper.database import get_sniper_db
from bot.utils.tender_notifications import format_favorites_list, format_stats
from bot.utils.tender_db_helpers import (
    get_user_favorites,
    get_user_hidden_tenders,
    get_user_stats,
    get_user_profile,
    create_or_update_profile
)

logger = logging.getLogger(__name__)
router = Router()


# ============================================
# FSM для установки профиля
# ============================================

class ProfileSetup(StatesGroup):
    specialization = State()
    regions = State()
    amount_range = State()


# ============================================
# ИЗБРАННЫЕ ТЕНДЕРЫ
# ============================================

@router.message(Command("favorites"))
async def favorites_command(message: Message):
    """Показывает список избранных тендеров."""
    try:
        # Получаем user_id
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(message.from_user.id)

        if not sniper_user:
            await message.answer("❌ Пользователь не найден в системе")
            return

        # Получаем избранные
        favorites = await get_user_favorites(sniper_user['id'], limit=50)

        if not favorites:
            await message.answer(
                "⭐ У вас пока нет избранных тендеров\n\n"
                "Используйте кнопку '⭐ В избранное' в уведомлениях о тендерах, "
                "чтобы добавить их в избранное."
            )
            return

        # Форматируем список
        favorites_text = format_favorites_list(favorites, message.from_user.username or "Пользователь")

        # Кнопка для получения HTML отчета
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📄 Получить HTML отчет", callback_data="html_favorites")]
        ])

        await message.answer(
            text=favorites_text,
            reply_markup=keyboard,
            parse_mode='HTML'
        )

    except Exception as e:
        logger.error(f"Ошибка команды /favorites: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка при получении избранного")


# ============================================
# СКРЫТЫЕ ТЕНДЕРЫ
# ============================================

@router.message(Command("hidden"))
async def hidden_command(message: Message):
    """Показывает список скрытых тендеров."""
    try:
        # Получаем user_id
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(message.from_user.id)

        if not sniper_user:
            await message.answer("❌ Пользователь не найден в системе")
            return

        # Получаем скрытые
        hidden = await get_user_hidden_tenders(sniper_user['id'])

        if not hidden:
            await message.answer(
                "👁 У вас нет скрытых тендеров\n\n"
                "Используйте кнопку '👎 Скрыть' в уведомлениях, "
                "чтобы скрыть неинтересные тендеры."
            )
            return

        # Форматируем список
        message_text = f"👎 <b>СКРЫТЫЕ ТЕНДЕРЫ</b> ({len(hidden)})\n\n"
        message_text += "Тендеры подобного типа будут показываться реже.\n\n"

        for i, tender in enumerate(hidden[:20], 1):
            message_text += f"{i}. №{tender['tender_number']}\n"

        if len(hidden) > 20:
            message_text += f"\n... и еще {len(hidden) - 20} тендеров"

        # Кнопка для сброса скрытых
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Сбросить все скрытые", callback_data="reset_hidden")]
        ])

        await message.answer(
            text=message_text,
            reply_markup=keyboard,
            parse_mode='HTML'
        )

    except Exception as e:
        logger.error(f"Ошибка команды /hidden: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка при получении скрытых тендеров")


# ============================================
# СТАТИСТИКА
# ============================================

@router.message(Command("stats"))
async def stats_command(message: Message):
    """Показывает статистику пользователя."""
    try:
        # Получаем user_id
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(message.from_user.id)

        if not sniper_user:
            await message.answer("❌ Пользователь не найден в системе")
            return

        # Получаем статистику
        stats = await get_user_stats(sniper_user['id'])

        # Форматируем
        stats_text = format_stats(stats)

        await message.answer(text=stats_text, parse_mode='HTML')

    except Exception as e:
        logger.error(f"Ошибка команды /stats: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка при получении статистики")


# ============================================
# НАСТРОЙКИ
# ============================================

@router.message(Command("settings"))
async def settings_command(message: Message):
    """Показывает настройки пользователя."""
    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔔 Уведомления", callback_data="settings_notifications")],
            [InlineKeyboardButton(text="🎯 Мои фильтры", callback_data="sniper_my_filters")],
            [InlineKeyboardButton(text="⚙️ Расширенные настройки", callback_data="settings_advanced")],
        ])

        await message.answer(
            "⚙️ <b>НАСТРОЙКИ</b>\n\n"
            "Управляйте своими настройками:\n\n"
            "🔔 <b>Уведомления</b> — включить/выключить автомониторинг\n"
            "🎯 <b>Мои фильтры</b> — просмотр и редактирование фильтров\n"
            "⚙️ <b>Расширенные</b> — тихие часы, интеграции, профиль",
            reply_markup=keyboard,
            parse_mode='HTML'
        )

    except Exception as e:
        logger.error(f"Ошибка команды /settings: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка")


@router.callback_query(F.data == "settings_profile")
async def settings_profile_handler(callback_query):
    """Показывает настройки профиля."""
    await callback_query.answer()

    try:
        # Получаем user_id
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(callback_query.from_user.id)

        if not sniper_user:
            await callback_query.message.answer("❌ Пользователь не найден")
            return

        # Получаем текущий профиль
        profile = await get_user_profile(sniper_user['id'])

        if profile:
            regions_str = ", ".join(profile['regions']) if profile['regions'] else "Не указаны"
            amount_range = f"{profile['amount_min']:,.0f} - {profile['amount_max']:,.0f} ₽" if profile['amount_min'] and profile['amount_max'] else "Не указан"

            message_text = f"""🏢 <b>ПРОФИЛЬ КОМПАНИИ</b>

<b>Специализация:</b> {profile['specialization'] or 'Не указана'}
<b>Регионы работы:</b> {regions_str}
<b>Диапазон сумм:</b> {amount_range}

━━━━━━━━━━━━━━━
<b>Зачем нужен профиль?</b>

• Более точный скоринг тендеров
• Персональные рекомендации
• Приоритет в выдаче релевантных закупок

Для обновления профиля: /setprofile"""
        else:
            message_text = """🏢 <b>ПРОФИЛЬ КОМПАНИИ</b>

❌ Профиль не настроен

━━━━━━━━━━━━━━━
<b>Что это такое?</b>

Профиль компании помогает боту лучше понять ваши потребности и показывать более релевантные тендеры.

<b>Что указать:</b>
• Специализация (IT, строительство, медицина...)
• Регионы присутствия
• Комфортный диапазон сумм контрактов

<b>Что это даёт:</b>
• Более точный скоринг тендеров
• Персональные рекомендации по фильтрам
• Приоритет в выдаче подходящих закупок

Для настройки: /setprofile"""

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📝 Настроить профиль", callback_data="start_setprofile")],
            [InlineKeyboardButton(text="« Назад", callback_data="settings_advanced")]
        ])

        await callback_query.message.edit_text(
            text=message_text,
            reply_markup=keyboard,
            parse_mode='HTML'
        )

    except Exception as e:
        logger.error(f"Ошибка показа профиля: {e}", exc_info=True)
        await callback_query.answer("❌ Произошла ошибка", show_alert=True)


# ============================================
# УСТАНОВКА ПРОФИЛЯ
# ============================================

@router.message(Command("setprofile"))
async def setprofile_command(message: Message, state: FSMContext):
    """Начинает процесс установки профиля."""
    await state.set_state(ProfileSetup.specialization)

    await message.answer(
        "🏢 <b>НАСТРОЙКА ПРОФИЛЯ КОМПАНИИ</b>\n\n"
        "Шаг 1/3: Укажите специализацию вашей компании\n\n"
        "<i>Например: IT оборудование, Строительство, Медицинское оборудование</i>",
        parse_mode='HTML'
    )


@router.callback_query(F.data == "start_setprofile")
async def start_setprofile_callback(callback: CallbackQuery, state: FSMContext):
    """Начинает процесс установки профиля через callback."""
    await callback.answer()
    await state.set_state(ProfileSetup.specialization)

    await callback.message.edit_text(
        "🏢 <b>НАСТРОЙКА ПРОФИЛЯ КОМПАНИИ</b>\n\n"
        "Шаг 1/3: Укажите специализацию вашей компании\n\n"
        "<i>Например: IT оборудование, Строительство, Медицинское оборудование</i>",
        parse_mode='HTML'
    )


@router.message(ProfileSetup.specialization)
async def process_specialization(message: Message, state: FSMContext):
    """Обрабатывает ввод специализации."""
    await state.update_data(specialization=message.text)
    await state.set_state(ProfileSetup.regions)

    await message.answer(
        "🏢 <b>НАСТРОЙКА ПРОФИЛЯ КОМПАНИИ</b>\n\n"
        "Шаг 2/3: Укажите регионы работы (через запятую)\n\n"
        "<i>Например: Москва, Санкт-Петербург, Московская область</i>",
        parse_mode='HTML'
    )


@router.message(ProfileSetup.regions)
async def process_regions(message: Message, state: FSMContext):
    """Обрабатывает ввод регионов."""
    regions = [r.strip() for r in message.text.split(',')]
    await state.update_data(regions=regions)
    await state.set_state(ProfileSetup.amount_range)

    await message.answer(
        "🏢 <b>НАСТРОЙКА ПРОФИЛЯ КОМПАНИИ</b>\n\n"
        "Шаг 3/3: Укажите диапазон сумм контрактов (через дефис, в рублях)\n\n"
        "<i>Например: 1000000-5000000</i>",
        parse_mode='HTML'
    )


@router.message(ProfileSetup.amount_range)
async def process_amount_range(message: Message, state: FSMContext):
    """Обрабатывает ввод диапазона сумм и сохраняет профиль."""
    try:
        # Парсим диапазон
        parts = message.text.replace(' ', '').split('-')
        if len(parts) != 2:
            await message.answer(
                "❌ Неверный формат. Используйте формат: 1000000-5000000\n\n"
                "Попробуйте еще раз:"
            )
            return

        amount_min = float(parts[0])
        amount_max = float(parts[1])

        # Получаем сохраненные данные
        data = await state.get_data()

        # Получаем user_id
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(message.from_user.id)

        if not sniper_user:
            await message.answer("❌ Пользователь не найден")
            await state.clear()
            return

        # Сохраняем профиль
        success = await create_or_update_profile(
            user_id=sniper_user['id'],
            specialization=data['specialization'],
            regions=data['regions'],
            amount_min=amount_min,
            amount_max=amount_max
        )

        await state.clear()

        if success:
            await message.answer(
                "✅ <b>Профиль сохранен!</b>\n\n"
                "Теперь анализ тендеров будет более точным и персонализированным.\n\n"
                "Используйте /settings для просмотра или изменения профиля.",
                parse_mode='HTML'
            )
        else:
            await message.answer(
                "❌ Не удалось сохранить профиль. Попробуйте позже."
            )

    except ValueError:
        await message.answer(
            "❌ Неверный формат чисел. Используйте только цифры.\n\n"
            "Пример: 1000000-5000000\n\nПопробуйте еще раз:"
        )
    except Exception as e:
        logger.error(f"Ошибка сохранения профиля: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка при сохранении профиля")
        await state.clear()


# ============================================
# CALLBACK ОБРАБОТЧИКИ
# ============================================

@router.callback_query(F.data == "reset_hidden")
async def reset_hidden_callback(callback_query):
    """Сбрасывает все скрытые тендеры."""
    await callback_query.answer()

    try:
        # Получаем user_id
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(callback_query.from_user.id)

        if not sniper_user:
            await callback_query.answer("❌ Пользователь не найден", show_alert=True)
            return

        # Удаляем все скрытые
        from database import DatabaseSession, HiddenTender
        from sqlalchemy import delete

        async with DatabaseSession() as session:
            await session.execute(
                delete(HiddenTender).where(HiddenTender.user_id == sniper_user['id'])
            )

        await callback_query.message.edit_text(
            text="✅ Все скрытые тендеры удалены!",
            parse_mode='HTML'
        )

    except Exception as e:
        logger.error(f"Ошибка сброса скрытых: {e}", exc_info=True)
        await callback_query.answer("❌ Произошла ошибка", show_alert=True)


# ============================================
# ДОПОЛНИТЕЛЬНЫЕ ОБРАБОТЧИКИ НАСТРОЕК
# ============================================

@router.callback_query(F.data == "settings_criteria")
async def settings_criteria_handler(callback: CallbackQuery):
    """Показывает настройки критериев отбора."""
    await callback.answer()

    try:
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not sniper_user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        # Получаем фильтры пользователя
        filters = await db.get_user_filters(sniper_user['id'])
        active_filters = [f for f in filters if f.get('is_active')]

        if filters:
            filters_text = "\n".join([
                f"• <b>{f['name']}</b> {'✅' if f.get('is_active') else '⏸'}"
                for f in filters[:10]
            ])
        else:
            filters_text = "<i>У вас пока нет фильтров</i>"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать фильтр", callback_data="sniper_new_search")],
            [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
            [InlineKeyboardButton(text="« Назад", callback_data="settings_back")]
        ])

        await callback.message.edit_text(
            f"🎯 <b>КРИТЕРИИ ОТБОРА</b>\n\n"
            f"Фильтры определяют, какие тендеры вы будете получать.\n\n"
            f"<b>Ваши фильтры ({len(active_filters)} активных):</b>\n"
            f"{filters_text}\n\n"
            f"💡 Создайте фильтры для автоматического мониторинга тендеров",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка настроек критериев: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "settings_notifications")
async def settings_notifications_handler(callback: CallbackQuery):
    """Показывает базовые настройки уведомлений."""
    await callback.answer()

    try:
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not sniper_user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        monitoring_enabled = sniper_user.get('notifications_enabled', True)
        notifications_limit = sniper_user.get('notifications_limit', 15)
        notifications_today = sniper_user.get('notifications_sent_today', 0)

        status_emoji = "✅" if monitoring_enabled else "⏸"
        status_text = "Включен" if monitoring_enabled else "На паузе"

        toggle_text = "⏸ Приостановить мониторинг" if monitoring_enabled else "▶️ Возобновить мониторинг"
        toggle_callback = "sniper_pause_monitoring" if monitoring_enabled else "sniper_resume_monitoring"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data=toggle_callback)],
            [InlineKeyboardButton(text="⚙️ Расширенные настройки", callback_data="settings_advanced")],
            [InlineKeyboardButton(text="« Назад", callback_data="settings_back")]
        ])

        await callback.message.edit_text(
            f"🔔 <b>УВЕДОМЛЕНИЯ</b>\n\n"
            f"<b>Автомониторинг:</b> {status_emoji} {status_text}\n\n"
            f"<b>Лимит:</b> {notifications_limit} уведомлений в день\n"
            f"<b>Отправлено сегодня:</b> {notifications_today} из {notifications_limit}\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"<b>Как это работает:</b>\n"
            f"Бот проверяет новые тендеры каждые 5 минут и отправляет вам уведомления о подходящих.\n\n"
            f"💡 Для настройки тихих часов и интеграций перейдите в <b>Расширенные настройки</b>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка настроек уведомлений: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "settings_advanced")
async def settings_advanced_handler(callback: CallbackQuery):
    """Расширенные настройки с подробными описаниями."""
    await callback.answer()

    try:
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not sniper_user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        user_data = sniper_user.get('data', {}) or {}

        # Статусы всех функций
        quiet_hours_enabled = user_data.get('quiet_hours_enabled', False)
        quiet_start = user_data.get('quiet_hours_start', 22)
        quiet_end = user_data.get('quiet_hours_end', 8)
        digest_enabled = not user_data.get('digest_disabled', False)
        webhook_url = user_data.get('webhook_url', '')
        email_address = user_data.get('email_notifications', '')

        # Формируем статусы
        quiet_status = f"{quiet_start}:00-{quiet_end}:00" if quiet_hours_enabled else "выкл"
        digest_status = "вкл" if digest_enabled else "выкл"
        webhook_status = "настроен" if webhook_url else "не настроен"
        email_status = email_address[:15] + "..." if email_address else "не настроен"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🌙 Тихие часы ({quiet_status})", callback_data="settings_quiet_hours")],
            [InlineKeyboardButton(text=f"📬 Утренний дайджест ({digest_status})", callback_data="adv_digest")],
            [InlineKeyboardButton(text=f"🔗 Webhook CRM ({webhook_status})", callback_data="integration_webhook")],
            [InlineKeyboardButton(text=f"📧 Email ({email_status})", callback_data="integration_email")],
            [InlineKeyboardButton(text="📊 Google Sheets", callback_data="integration_sheets")],
            [InlineKeyboardButton(text="🏢 Профиль компании", callback_data="settings_profile")],
            [InlineKeyboardButton(text="« Назад к настройкам", callback_data="settings_back")]
        ])

        await callback.message.edit_text(
            "⚙️ <b>РАСШИРЕННЫЕ НАСТРОЙКИ</b>\n\n"
            "Настройте дополнительные функции для более комфортной работы:\n\n"
            "━━━━━━━━━━━━━━━\n"
            "🌙 <b>Тихие часы</b>\n"
            "<i>Отключает уведомления в ночное время. Пропущенные тендеры придут утром в дайджесте.</i>\n\n"
            "📬 <b>Утренний дайджест</b>\n"
            "<i>Ежедневная сводка в 9:00 МСК: сколько тендеров найдено, статистика и рекомендации.</i>\n\n"
            "🔗 <b>Webhook для CRM</b>\n"
            "<i>Автоматическая отправка тендеров в вашу CRM-систему (Bitrix24, amoCRM, 1C и др.)</i>\n\n"
            "📧 <b>Email-уведомления</b>\n"
            "<i>Дублирование важных тендеров (>1 млн ₽) на электронную почту.</i>\n\n"
            "📊 <b>Google Sheets</b>\n"
            "<i>Автоматический экспорт тендеров в Google-таблицу для работы в команде.</i>\n\n"
            "🏢 <b>Профиль компании</b>\n"
            "<i>Информация о вашей компании для персонализации поиска.</i>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка расширенных настроек: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "adv_digest")
async def advanced_digest_handler(callback: CallbackQuery):
    """Подробная настройка дайджеста."""
    await callback.answer()

    try:
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)
        user_data = sniper_user.get('data', {}) or {}
        digest_enabled = not user_data.get('digest_disabled', False)

        status_text = "✅ Включён" if digest_enabled else "❌ Выключен"
        toggle_text = "❌ Выключить дайджест" if digest_enabled else "✅ Включить дайджест"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data="toggle_digest")],
            [InlineKeyboardButton(text="« Назад", callback_data="settings_advanced")]
        ])

        await callback.message.edit_text(
            "📬 <b>УТРЕННИЙ ДАЙДЖЕСТ</b>\n\n"
            f"<b>Статус:</b> {status_text}\n\n"
            "━━━━━━━━━━━━━━━\n"
            "<b>Что это такое?</b>\n\n"
            "Каждое утро в <b>9:00 по Москве</b> вы получаете сводку:\n\n"
            "📊 Сколько тендеров найдено за вчера\n"
            "🎯 Количество активных фильтров\n"
            "⏱ Сколько времени вы сэкономили\n"
            "💡 Рекомендации по улучшению фильтров\n\n"
            "<b>Кому полезно:</b>\n"
            "• Тем, кто не хочет проверять бота вручную\n"
            "• Руководителям для контроля поиска\n"
            "• Всем, кто хочет видеть общую картину",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка настроек дайджеста: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "settings_quiet_hours")
async def settings_quiet_hours_handler(callback: CallbackQuery):
    """Настройка тихих часов."""
    await callback.answer()

    try:
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not sniper_user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        user_data = sniper_user.get('data', {}) or {}
        quiet_hours_enabled = user_data.get('quiet_hours_enabled', False)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="22:00 - 08:00", callback_data="quiet_22_8"),
                InlineKeyboardButton(text="23:00 - 07:00", callback_data="quiet_23_7"),
            ],
            [
                InlineKeyboardButton(text="21:00 - 09:00", callback_data="quiet_21_9"),
                InlineKeyboardButton(text="00:00 - 08:00", callback_data="quiet_0_8"),
            ],
            [InlineKeyboardButton(
                text="❌ Отключить тихие часы" if quiet_hours_enabled else "✅ Тихие часы отключены",
                callback_data="quiet_disable"
            )],
            [InlineKeyboardButton(text="« Назад", callback_data="settings_advanced")]
        ])

        current_status = f"Активны: {user_data.get('quiet_hours_start', 22)}:00 - {user_data.get('quiet_hours_end', 8)}:00" if quiet_hours_enabled else "Выключены"

        await callback.message.edit_text(
            "🌙 <b>ТИХИЕ ЧАСЫ</b>\n\n"
            f"<b>Статус:</b> {current_status}\n\n"
            "━━━━━━━━━━━━━━━\n"
            "<b>Что это такое?</b>\n\n"
            "В указанное время бот не будет присылать уведомления — "
            "чтобы не беспокоить вас ночью.\n\n"
            "<b>Как работает:</b>\n"
            "• Тендеры продолжают собираться\n"
            "• Уведомления накапливаются\n"
            "• Утром приходит дайджест со всеми пропущенными\n\n"
            "<b>Выберите интервал (МСК):</b>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка настроек тихих часов: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("quiet_"))
async def set_quiet_hours_handler(callback: CallbackQuery):
    """Устанавливает тихие часы."""
    await callback.answer()

    try:
        from database import DatabaseSession, SniperUser
        from sqlalchemy import select

        action = callback.data.replace("quiet_", "")

        async with DatabaseSession() as session:
            user = await session.scalar(
                select(SniperUser).where(SniperUser.telegram_id == callback.from_user.id)
            )

            if not user:
                await callback.message.answer("❌ Пользователь не найден")
                return

            current_data = user.data if isinstance(user.data, dict) else {}

            if action == "disable":
                current_data['quiet_hours_enabled'] = False
                message = "✅ Тихие часы отключены\n\nУведомления будут приходить круглосуточно."
            else:
                # Парсим формат "22_8" -> start=22, end=8
                parts = action.split("_")
                start_hour = int(parts[0])
                end_hour = int(parts[1])

                current_data['quiet_hours_enabled'] = True
                current_data['quiet_hours_start'] = start_hour
                current_data['quiet_hours_end'] = end_hour

                message = (
                    f"✅ Тихие часы установлены\n\n"
                    f"🌙 С {start_hour}:00 до {end_hour}:00 (МСК)\n"
                    f"уведомления приходить не будут."
                )

            user.data = current_data
            await session.commit()

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« К расширенным настройкам", callback_data="settings_advanced")]
        ])

        await callback.message.edit_text(message, reply_markup=keyboard, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка установки тихих часов: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "toggle_digest")
async def toggle_digest_handler(callback: CallbackQuery):
    """Переключает утренний дайджест."""
    await callback.answer()

    try:
        from database import DatabaseSession, SniperUser
        from sqlalchemy import select

        async with DatabaseSession() as session:
            user = await session.scalar(
                select(SniperUser).where(SniperUser.telegram_id == callback.from_user.id)
            )

            if not user:
                await callback.message.answer("❌ Пользователь не найден")
                return

            current_data = user.data if isinstance(user.data, dict) else {}
            digest_disabled = current_data.get('digest_disabled', False)

            # Переключаем
            current_data['digest_disabled'] = not digest_disabled
            user.data = current_data
            await session.commit()

            new_status = "выключен" if current_data['digest_disabled'] else "включён"

        await callback.answer(f"📬 Утренний дайджест {new_status}")

        # Возвращаемся к настройкам дайджеста
        await advanced_digest_handler(callback)

    except Exception as e:
        logger.error(f"Ошибка переключения дайджеста: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "settings_back")
async def settings_back_handler(callback: CallbackQuery):
    """Возврат к главному меню настроек."""
    await callback.answer()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Уведомления", callback_data="settings_notifications")],
        [InlineKeyboardButton(text="🎯 Мои фильтры", callback_data="sniper_my_filters")],
        [InlineKeyboardButton(text="⚙️ Расширенные настройки", callback_data="settings_advanced")],
    ])

    await callback.message.edit_text(
        "⚙️ <b>НАСТРОЙКИ</b>\n\n"
        "Управляйте своими настройками:\n\n"
        "🔔 <b>Уведомления</b> — включить/выключить автомониторинг\n"
        "🎯 <b>Мои фильтры</b> — просмотр и редактирование фильтров\n"
        "⚙️ <b>Расширенные</b> — тихие часы, интеграции, профиль",
        reply_markup=keyboard,
        parse_mode='HTML'
    )


@router.callback_query(F.data == "html_favorites")
async def html_favorites_handler(callback: CallbackQuery):
    """Генерация HTML отчета избранных тендеров."""
    await callback.answer("Генерирую отчет...")

    try:
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not sniper_user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        favorites = await get_user_favorites(sniper_user['id'])

        if not favorites:
            await callback.message.answer("❌ У вас нет избранных тендеров")
            return

        # Генерируем HTML отчет
        from tender_sniper.all_tenders_report import generate_all_tenders_html

        # Преобразуем формат данных
        tenders_for_report = []
        for fav in favorites:
            tenders_for_report.append({
                'number': fav.get('tender_number', ''),
                'name': fav.get('tender_name', ''),
                'price': fav.get('tender_price'),
                'url': fav.get('tender_url', ''),
                'filter_name': '⭐ Избранное',
                'score': 100,
                'region': '',
                'customer_name': ''
            })

        html_content = generate_all_tenders_html(
            tenders_for_report,
            username=callback.from_user.username or "Пользователь"
        )

        # Отправляем файл
        from aiogram.types import BufferedInputFile
        import io

        html_bytes = html_content.encode('utf-8')
        file = BufferedInputFile(html_bytes, filename="favorites_report.html")

        await callback.message.answer_document(
            file,
            caption=f"⭐ <b>Избранные тендеры</b>\n\nВсего: {len(favorites)} тендеров",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка генерации HTML избранных: {e}", exc_info=True)
        await callback.message.answer("❌ Произошла ошибка при генерации отчета")


# ============================================
# ИНТЕГРАЦИИ
# ============================================

class IntegrationSetup(StatesGroup):
    """Состояния для настройки интеграций."""
    webhook_url = State()
    email_address = State()
    google_sheet_id = State()


@router.callback_query(F.data == "settings_integrations")
async def settings_integrations_handler(callback: CallbackQuery):
    """Показывает настройки интеграций."""
    await callback.answer()

    try:
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not sniper_user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        user_data = sniper_user.get('data', {}) or {}

        # Получаем статусы интеграций
        webhook_url = user_data.get('webhook_url', '')
        email_address = user_data.get('email_notifications', '')
        google_sheet_id = user_data.get('google_sheet_id', '')

        webhook_status = "✅ Настроен" if webhook_url else "❌ Не настроен"
        email_status = "✅ " + email_address[:20] + "..." if email_address else "❌ Не настроен"
        sheets_status = "✅ Настроен" if google_sheet_id else "❌ Не настроен"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🔗 Webhook: {webhook_status}", callback_data="integration_webhook")],
            [InlineKeyboardButton(text=f"📧 Email: {email_status}", callback_data="integration_email")],
            [InlineKeyboardButton(text=f"📊 Google Sheets: {sheets_status}", callback_data="integration_sheets")],
            [InlineKeyboardButton(text="« Назад", callback_data="settings_back")]
        ])

        await callback.message.edit_text(
            "🔗 <b>ИНТЕГРАЦИИ</b>\n\n"
            "Подключите внешние сервисы для автоматической отправки тендеров:\n\n"
            f"<b>Webhook (CRM):</b> {webhook_status}\n"
            f"<b>Email:</b> {email_status}\n"
            f"<b>Google Sheets:</b> {sheets_status}\n\n"
            "💡 При появлении нового тендера данные будут автоматически отправляться в подключённые сервисы.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка настроек интеграций: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "integration_webhook")
async def integration_webhook_handler(callback: CallbackQuery, state: FSMContext):
    """Настройка webhook."""
    await callback.answer()

    try:
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)
        user_data = sniper_user.get('data', {}) or {}
        current_url = user_data.get('webhook_url', '')

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📝 Указать URL", callback_data="webhook_set_url")],
            [InlineKeyboardButton(text="🧪 Тест подключения", callback_data="webhook_test")] if current_url else [],
            [InlineKeyboardButton(text="❌ Удалить", callback_data="webhook_delete")] if current_url else [],
            [InlineKeyboardButton(text="« Назад", callback_data="settings_advanced")]
        ])
        # Remove empty rows
        keyboard.inline_keyboard = [row for row in keyboard.inline_keyboard if row]

        status_text = f"<code>{current_url}</code>" if current_url else "Не настроен"

        await callback.message.edit_text(
            "🔗 <b>WEBHOOK ДЛЯ CRM</b>\n\n"
            f"<b>Текущий URL:</b>\n{status_text}\n\n"
            "━━━━━━━━━━━━━━━\n"
            "<b>Что это такое?</b>\n\n"
            "При появлении нового тендера на ваш URL автоматически отправляется POST-запрос с данными.\n\n"
            "<b>Формат данных:</b>\n"
            "<code>{\n"
            '  "event": "new_tender",\n'
            '  "tender": {\n'
            '    "number": "...",\n'
            '    "name": "...",\n'
            '    "price": 1000000,\n'
            '    "customer": "...",\n'
            '    "deadline": "..."\n'
            '  }\n'
            "}</code>\n\n"
            "<b>Где применить:</b>\n"
            "• Bitrix24 — автосоздание сделок\n"
            "• amoCRM — добавление лидов\n"
            "• 1С — синхронизация данных\n"
            "• Make/Zapier — автоматизации\n"
            "• Ваша CRM — через API",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка webhook настроек: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "webhook_set_url")
async def webhook_set_url_handler(callback: CallbackQuery, state: FSMContext):
    """Запрос URL для webhook."""
    await callback.answer()
    await state.set_state(IntegrationSetup.webhook_url)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="integration_webhook")]
    ])

    await callback.message.edit_text(
        "🔗 <b>НАСТРОЙКА WEBHOOK</b>\n\n"
        "Отправьте URL вашего webhook.\n\n"
        "Примеры:\n"
        "• <code>https://yourcrm.com/api/webhook</code>\n"
        "• <code>https://hook.integromat.com/xxx</code>\n"
        "• <code>https://hooks.zapier.com/xxx</code>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.message(IntegrationSetup.webhook_url)
async def process_webhook_url(message: Message, state: FSMContext):
    """Обработка введённого URL webhook."""
    url = message.text.strip()

    # Валидация URL
    if not url.startswith(('http://', 'https://')):
        await message.answer(
            "❌ Неверный формат URL. URL должен начинаться с http:// или https://\n\n"
            "Попробуйте ещё раз:"
        )
        return

    try:
        from database import DatabaseSession, SniperUser
        from sqlalchemy import select
        from bot.integrations import get_integration_manager

        # Тестируем webhook
        manager = get_integration_manager()
        test_result = await manager.test_webhook(url)

        if test_result['success']:
            # Сохраняем URL
            async with DatabaseSession() as session:
                user = await session.scalar(
                    select(SniperUser).where(SniperUser.telegram_id == message.from_user.id)
                )
                if user:
                    current_data = user.data if isinstance(user.data, dict) else {}
                    current_data['webhook_url'] = url
                    user.data = current_data
                    await session.commit()

            await state.clear()
            await message.answer(
                f"✅ <b>Webhook настроен!</b>\n\n"
                f"URL: <code>{url}</code>\n"
                f"Тест: {test_result['message']} ({test_result['response_time']}ms)\n\n"
                "Теперь при появлении новых тендеров данные будут отправляться на этот URL.",
                parse_mode="HTML"
            )
        else:
            await message.answer(
                f"⚠️ <b>Webhook недоступен</b>\n\n"
                f"URL: <code>{url}</code>\n"
                f"Ошибка: {test_result['message']}\n\n"
                "Проверьте URL и попробуйте ещё раз:",
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"Ошибка сохранения webhook: {e}", exc_info=True)
        await state.clear()
        await message.answer("❌ Произошла ошибка при сохранении")


@router.callback_query(F.data == "webhook_test")
async def webhook_test_handler(callback: CallbackQuery):
    """Тест webhook."""
    await callback.answer("Тестирую подключение...")

    try:
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)
        user_data = sniper_user.get('data', {}) or {}
        webhook_url = user_data.get('webhook_url', '')

        if not webhook_url:
            await callback.message.answer("❌ Webhook URL не настроен")
            return

        from bot.integrations import get_integration_manager
        manager = get_integration_manager()
        result = await manager.test_webhook(webhook_url)

        if result['success']:
            await callback.message.answer(
                f"✅ <b>Webhook работает!</b>\n\n"
                f"Статус: {result['message']}\n"
                f"Время ответа: {result['response_time']}ms",
                parse_mode="HTML"
            )
        else:
            await callback.message.answer(
                f"❌ <b>Webhook недоступен</b>\n\n"
                f"Ошибка: {result['message']}",
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"Ошибка теста webhook: {e}", exc_info=True)
        await callback.message.answer("❌ Произошла ошибка при тестировании")


@router.callback_query(F.data == "webhook_delete")
async def webhook_delete_handler(callback: CallbackQuery):
    """Удаление webhook."""
    await callback.answer()

    try:
        from database import DatabaseSession, SniperUser
        from sqlalchemy import select

        async with DatabaseSession() as session:
            user = await session.scalar(
                select(SniperUser).where(SniperUser.telegram_id == callback.from_user.id)
            )
            if user:
                current_data = user.data if isinstance(user.data, dict) else {}
                current_data.pop('webhook_url', None)
                user.data = current_data
                await session.commit()

        await callback.message.edit_text(
            "✅ Webhook удалён",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« К расширенным настройкам", callback_data="settings_advanced")]
            ])
        )

    except Exception as e:
        logger.error(f"Ошибка удаления webhook: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "integration_email")
async def integration_email_handler(callback: CallbackQuery, state: FSMContext):
    """Настройка email уведомлений."""
    await callback.answer()

    try:
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)
        user_data = sniper_user.get('data', {}) or {}
        current_email = user_data.get('email_notifications', '')

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📝 Указать email", callback_data="email_set")],
            [InlineKeyboardButton(text="❌ Удалить", callback_data="email_delete")] if current_email else [],
            [InlineKeyboardButton(text="« Назад", callback_data="settings_advanced")]
        ])
        keyboard.inline_keyboard = [row for row in keyboard.inline_keyboard if row]

        status_text = f"<code>{current_email}</code>" if current_email else "Не настроен"

        await callback.message.edit_text(
            "📧 <b>EMAIL УВЕДОМЛЕНИЯ</b>\n\n"
            f"<b>Текущий email:</b>\n{status_text}\n\n"
            "━━━━━━━━━━━━━━━\n"
            "<b>Что это такое?</b>\n\n"
            "Важные тендеры дублируются на вашу почту — чтобы вы точно не пропустили.\n\n"
            "<b>Какие тендеры отправляются:</b>\n"
            "• С ценой более 1 000 000 ₽\n"
            "• С высоким рейтингом (score > 80)\n"
            "• Срочные (дедлайн менее 3 дней)\n\n"
            "<b>Кому полезно:</b>\n"
            "• Руководителям — контроль без Telegram\n"
            "• Тем, кто часто не у телефона\n"
            "• Для архивирования в корп. почте\n\n"
            "📬 Письмо содержит все данные тендера и прямую ссылку.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка email настроек: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "email_set")
async def email_set_handler(callback: CallbackQuery, state: FSMContext):
    """Запрос email."""
    await callback.answer()
    await state.set_state(IntegrationSetup.email_address)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="integration_email")]
    ])

    await callback.message.edit_text(
        "📧 <b>НАСТРОЙКА EMAIL</b>\n\n"
        "Отправьте ваш email адрес:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.message(IntegrationSetup.email_address)
async def process_email_address(message: Message, state: FSMContext):
    """Обработка введённого email."""
    import re
    email = message.text.strip().lower()

    # Простая валидация email
    if not re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email):
        await message.answer(
            "❌ Неверный формат email. Пример: user@example.com\n\n"
            "Попробуйте ещё раз:"
        )
        return

    try:
        from database import DatabaseSession, SniperUser
        from sqlalchemy import select

        async with DatabaseSession() as session:
            user = await session.scalar(
                select(SniperUser).where(SniperUser.telegram_id == message.from_user.id)
            )
            if user:
                current_data = user.data if isinstance(user.data, dict) else {}
                current_data['email_notifications'] = email
                user.data = current_data
                await session.commit()

        await state.clear()
        await message.answer(
            f"✅ <b>Email настроен!</b>\n\n"
            f"Адрес: <code>{email}</code>\n\n"
            "Теперь важные тендеры будут дублироваться на этот email.",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка сохранения email: {e}", exc_info=True)
        await state.clear()
        await message.answer("❌ Произошла ошибка при сохранении")


@router.callback_query(F.data == "email_delete")
async def email_delete_handler(callback: CallbackQuery):
    """Удаление email."""
    await callback.answer()

    try:
        from database import DatabaseSession, SniperUser
        from sqlalchemy import select

        async with DatabaseSession() as session:
            user = await session.scalar(
                select(SniperUser).where(SniperUser.telegram_id == callback.from_user.id)
            )
            if user:
                current_data = user.data if isinstance(user.data, dict) else {}
                current_data.pop('email_notifications', None)
                user.data = current_data
                await session.commit()

        await callback.message.edit_text(
            "✅ Email уведомления отключены",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« К расширенным настройкам", callback_data="settings_advanced")]
            ])
        )

    except Exception as e:
        logger.error(f"Ошибка удаления email: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "integration_sheets")
async def integration_sheets_handler(callback: CallbackQuery):
    """Настройка Google Sheets."""
    await callback.answer()

    await callback.message.edit_text(
        "📊 <b>GOOGLE SHEETS</b>\n\n"
        "⚠️ <b>В разработке</b>\n\n"
        "━━━━━━━━━━━━━━━\n"
        "<b>Что это такое?</b>\n\n"
        "Автоматическая выгрузка всех найденных тендеров в Google-таблицу.\n\n"
        "<b>Как будет работать:</b>\n"
        "1. Вы создаёте таблицу в Google Sheets\n"
        "2. Указываете её ID в настройках\n"
        "3. Каждый новый тендер автоматически добавляется строкой\n\n"
        "<b>Кому полезно:</b>\n"
        "• Командам — совместная работа\n"
        "• Аналитикам — построение отчётов\n"
        "• Для архива всех тендеров\n"
        "• Интеграция с Excel через import\n\n"
        "🔜 Функция появится в ближайшем обновлении!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« К расширенным настройкам", callback_data="settings_advanced")]
        ]),
        parse_mode="HTML"
    )


# Экспортируем router
__all__ = ['router']
