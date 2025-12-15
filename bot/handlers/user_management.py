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
            [InlineKeyboardButton(text="🏢 Профиль компании", callback_data="settings_profile")],
            [InlineKeyboardButton(text="🎯 Критерии отбора", callback_data="settings_criteria")],
            [InlineKeyboardButton(text="🔔 Уведомления", callback_data="settings_notifications")]
        ])

        await message.answer(
            text="⚙️ <b>НАСТРОЙКИ</b>\n\nВыберите раздел:",
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

Для обновления профиля используйте команду /setprofile"""
        else:
            message_text = """🏢 <b>ПРОФИЛЬ КОМПАНИИ</b>

Профиль еще не настроен.

Укажите информацию о вашей компании для более точного анализа тендеров:

• Специализация (IT, строительство и т.д.)
• Регионы работы
• Диапазон сумм контрактов

Используйте команду /setprofile для настройки."""

        await callback_query.message.edit_text(
            text=message_text,
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
            [InlineKeyboardButton(text="➕ Создать фильтр", callback_data="sniper_create_filter")],
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
    """Показывает настройки уведомлений."""
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

        toggle_text = "⏸ Приостановить" if monitoring_enabled else "▶️ Возобновить"
        toggle_callback = "sniper_pause_monitoring" if monitoring_enabled else "sniper_resume_monitoring"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data=toggle_callback)],
            [InlineKeyboardButton(text="« Назад", callback_data="settings_back")]
        ])

        await callback.message.edit_text(
            f"🔔 <b>НАСТРОЙКИ УВЕДОМЛЕНИЙ</b>\n\n"
            f"<b>Автомониторинг:</b> {status_emoji} {status_text}\n"
            f"<b>Лимит уведомлений:</b> {notifications_limit}/день\n"
            f"<b>Отправлено сегодня:</b> {notifications_today}/{notifications_limit}\n\n"
            f"💡 Автомониторинг проверяет новые тендеры каждые 5 минут",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка настроек уведомлений: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "settings_back")
async def settings_back_handler(callback: CallbackQuery):
    """Возврат к настройкам."""
    await callback.answer()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏢 Профиль компании", callback_data="settings_profile")],
        [InlineKeyboardButton(text="🎯 Критерии отбора", callback_data="settings_criteria")],
        [InlineKeyboardButton(text="🔔 Уведомления", callback_data="settings_notifications")]
    ])

    await callback.message.edit_text(
        text="⚙️ <b>НАСТРОЙКИ</b>\n\nВыберите раздел:",
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


# Экспортируем router
__all__ = ['router']
