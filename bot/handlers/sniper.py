"""
Обработчики команд Tender Sniper - мониторинг и уведомления о тендерах.

Функционал:
- Управление фильтрами мониторинга
- Просмотр активных фильтров
- Статистика и квоты
- Управление подпиской
"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import sys
import logging
import re
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from bot.utils import safe_callback_data

# Простой кэш для статуса мониторинга (избегаем запросов к БД на каждое открытие меню)
_monitoring_status_cache: dict = {}  # {user_id: (status, timestamp)}
_CACHE_TTL = 60  # секунд

# Лимиты фильтров (защита от злоупотреблений)
MAX_KEYWORDS = 15           # ключевых слов на фильтр (основные)
MAX_EXCLUDE_KEYWORDS = 20   # слов-исключений на фильтр
MAX_CUSTOMER_KEYWORDS = 10  # ключевых слов по заказчику
MAX_PRIMARY_KEYWORDS = 5    # приоритетных слов
MAX_SECONDARY_KEYWORDS = 10 # дополнительных слов


def _get_cached_monitoring_status(user_id: int) -> bool | None:
    """Получить статус из кэша если не устарел."""
    if user_id in _monitoring_status_cache:
        status, timestamp = _monitoring_status_cache[user_id]
        if datetime.now() - timestamp < timedelta(seconds=_CACHE_TTL):
            return status
    return None


def _set_monitoring_status_cache(user_id: int, status: bool):
    """Сохранить статус в кэш."""
    _monitoring_status_cache[user_id] = (status, datetime.now())


# 🧪 БЕТА: Состояния для расширенных настроек
class ExtendedSettingsStates(StatesGroup):
    waiting_for_input = State()


async def build_filter_extended_options_view(filter_id: int, db) -> tuple:
    """
    Вспомогательная функция для построения UI расширенных настроек фильтра.
    Возвращает (settings_text, keyboard) или (None, None) если фильтр не найден.
    """
    filter_data = await db.get_filter_by_id(filter_id)

    if not filter_data:
        return None, None

    # Формируем информацию о текущих настройках
    settings_info = f"⚙️ <b>Настройки фильтра:</b> {filter_data['name']}\n\n"

    purchase_num = filter_data.get('purchase_number')
    settings_info += f"🔢 <b>Номер закупки:</b> {purchase_num or '—'}\n"

    customer_inns = filter_data.get('customer_inn', [])
    if customer_inns:
        settings_info += f"🏢 <b>ИНН заказчиков:</b> {', '.join(customer_inns[:3])}"
        if len(customer_inns) > 3:
            settings_info += f" (+{len(customer_inns)-3})"
        settings_info += "\n"
    else:
        settings_info += "🏢 <b>ИНН заказчиков:</b> —\n"

    excluded_inns = filter_data.get('excluded_customer_inns', [])
    excluded_keywords = filter_data.get('excluded_customer_keywords', [])
    blacklist_count = len(excluded_inns) + len(excluded_keywords)
    settings_info += f"🚫 <b>Черный список:</b> {blacklist_count} записей\n"

    pub_days = filter_data.get('publication_days')
    if pub_days:
        settings_info += f"📅 <b>Публикация:</b> за {pub_days} дней\n"
    else:
        settings_info += "📅 <b>Публикация:</b> без ограничений\n"

    primary_kw = filter_data.get('primary_keywords', [])
    secondary_kw = filter_data.get('secondary_keywords', [])
    if primary_kw or secondary_kw:
        settings_info += f"⭐ <b>Приоритет:</b> {len(primary_kw)} главных, {len(secondary_kw)} доп.\n"
    else:
        settings_info += "⭐ <b>Приоритет:</b> не настроен\n"

    notify_ids = filter_data.get('notify_chat_ids') or []
    if notify_ids:
        settings_info += f"📱 <b>Уведомления:</b> {len(notify_ids)} адресатов\n"
    else:
        settings_info += "📱 <b>Уведомления:</b> только в личку\n"

    settings_info += "\n<i>Выберите параметр для настройки:</i>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔢 Номер закупки", callback_data=f"ext_pnum_{filter_id}")],
        [InlineKeyboardButton(text="🏢 ИНН заказчиков", callback_data=f"ext_inn_{filter_id}")],
        [InlineKeyboardButton(text="🚫 Черный список", callback_data=f"ext_blacklist_{filter_id}")],
        [InlineKeyboardButton(text="📅 Дата публикации", callback_data=f"ext_pubdate_{filter_id}")],
        [InlineKeyboardButton(text="⭐ Приоритет ключевых слов", callback_data=f"ext_priority_{filter_id}")],
        [InlineKeyboardButton(text="📱 Куда уведомлять", callback_data=f"ext_notify_{filter_id}")],
        [InlineKeyboardButton(text="« Назад к списку", callback_data="sniper_extended_settings")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    return settings_info, keyboard


# Добавляем путь для импорта Tender Sniper
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tender_sniper.database import get_sniper_db, get_plan_limits
from tender_sniper.config import is_tender_sniper_enabled
from bot.utils.access_check import require_feature
from tender_sniper.all_tenders_report import generate_all_tenders_html

logger = logging.getLogger(__name__)
router = Router()


# SniperStates УДАЛЁН - используется FilterSearchStates из sniper_search.py
# Рефакторинг 2024-12-19: унификация FSM


# ============================================
# ГЛАВНОЕ МЕНЮ TENDER SNIPER
# ============================================

@router.message(Command("sniper"))
@router.message(F.text == "🎯 Tender Sniper")
async def cmd_sniper_menu(message: Message):
    """Главное меню Tender Sniper."""
    try:
        # Проверяем, включен ли Tender Sniper
        if not is_tender_sniper_enabled():
            await message.answer(
                "⚠️ <b>Tender Sniper временно недоступен</b>\n\n"
                "Функция находится в стадии внедрения. "
                "Используйте обычный поиск через /start",
                parse_mode="HTML"
            )
            return

        # Проверяем статус автомониторинга (с кэшированием)
        user_id = message.from_user.id
        is_monitoring_enabled = _get_cached_monitoring_status(user_id)

        if is_monitoring_enabled is None:
            db = await get_sniper_db()
            is_monitoring_enabled = await db.get_monitoring_status(user_id)
            _set_monitoring_status_cache(user_id, is_monitoring_enabled)

        # Кнопка паузы/возобновления
        if is_monitoring_enabled:
            monitoring_button = InlineKeyboardButton(text="⏸️ Пауза автомониторинга", callback_data="sniper_pause_monitoring")
            monitoring_status = "🟢 <b>Автомониторинг активен</b>"
        else:
            monitoring_button = InlineKeyboardButton(text="▶️ Возобновить автомониторинг", callback_data="sniper_resume_monitoring")
            monitoring_status = "🔴 <b>Автомониторинг на паузе</b>"

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
            [InlineKeyboardButton(text="🎁 Пригласить друга", callback_data="get_referral_link")],
            [InlineKeyboardButton(text="❓ Помощь", callback_data="sniper_help")],
        ])

        await message.answer(
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
    except Exception as e:
        logger.error(f"Ошибка в cmd_sniper_menu: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка. Попробуйте /start")


# НЕ используем декоратор - handler зарегистрирован в menu_priority.py
# @router.callback_query(F.data == "sniper_menu")
async def show_sniper_menu(callback: CallbackQuery):
    """Callback для возврата в главное меню Sniper."""
    try:
        # НЕ вызываем callback.answer() - уже вызван в menu_priority.py

        # Проверяем статус автомониторинга (с кэшированием)
        user_id = callback.from_user.id
        is_monitoring_enabled = _get_cached_monitoring_status(user_id)

        if is_monitoring_enabled is None:
            # Кэш пуст или устарел - загружаем из БД
            db = await get_sniper_db()
            is_monitoring_enabled = await db.get_monitoring_status(user_id)
            _set_monitoring_status_cache(user_id, is_monitoring_enabled)

        # Кнопка паузы/возобновления
        if is_monitoring_enabled:
            monitoring_button = InlineKeyboardButton(text="⏸️ Пауза автомониторинга", callback_data="sniper_pause_monitoring")
            monitoring_status = "🟢 <b>Автомониторинг активен</b>"
        else:
            monitoring_button = InlineKeyboardButton(text="▶️ Возобновить автомониторинг", callback_data="sniper_resume_monitoring")
            monitoring_status = "🔴 <b>Автомониторинг на паузе</b>"

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
            [InlineKeyboardButton(text="🎁 Пригласить друга", callback_data="get_referral_link")],
            [InlineKeyboardButton(text="❓ Помощь", callback_data="sniper_help")],
        ])

        await callback.message.edit_text(
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
    except Exception as e:
        logger.error(f"Ошибка в show_sniper_menu: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


# ============================================
# УПРАВЛЕНИЕ АВТОМОНИТОРИНГОМ
# ============================================

@router.callback_query(F.data == "sniper_pause_monitoring")
async def pause_monitoring(callback: CallbackQuery):
    """Приостановить автомониторинг."""
    await callback.answer()

    try:
        db = await get_sniper_db()
        await db.pause_monitoring(callback.from_user.id)

        # Обновляем кэш
        _set_monitoring_status_cache(callback.from_user.id, False)

        await callback.message.answer(
            "⏸️ <b>Автомониторинг приостановлен</b>\n\n"
            "Вы перестанете получать уведомления о новых тендерах.\n"
            "Все ваши фильтры сохранены и будут работать после возобновления.",
            parse_mode="HTML"
        )

        # Обновляем меню
        await show_sniper_menu(callback)

    except Exception as e:
        logger.error(f"Ошибка при паузе мониторинга: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка при паузе автомониторинга")


@router.callback_query(F.data == "sniper_resume_monitoring")
async def resume_monitoring(callback: CallbackQuery):
    """Возобновить автомониторинг."""
    await callback.answer()

    try:
        db = await get_sniper_db()
        await db.resume_monitoring(callback.from_user.id)

        # Обновляем кэш
        _set_monitoring_status_cache(callback.from_user.id, True)

        await callback.message.answer(
            "▶️ <b>Автомониторинг возобновлен</b>\n\n"
            "Вы снова будете получать уведомления о новых тендерах,\n"
            "соответствующих вашим фильтрам.",
            parse_mode="HTML"
        )

        # Обновляем меню
        await show_sniper_menu(callback)

    except Exception as e:
        logger.error(f"Ошибка при возобновлении мониторинга: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка при возобновлении автомониторинга")


# ============================================
# СТАТИСТИКА И КВОТЫ
# ============================================

@router.callback_query(F.data == "sniper_stats")
async def show_sniper_stats(callback: CallbackQuery):
    """Показать статистику пользователя."""
    await callback.answer()

    try:
        db = await get_sniper_db()

        # Получаем пользователя
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            # Создаем нового пользователя
            await db.create_or_update_user(
                telegram_id=callback.from_user.id,
                username=callback.from_user.username,
                first_name=callback.from_user.first_name,
                last_name=callback.from_user.last_name,
                subscription_tier='trial'
            )
            user = await db.get_user_by_telegram_id(callback.from_user.id)

        # Получаем статистику
        stats = await db.get_user_stats(user['id'])

        # Получаем лимиты тарифа
        tier = user['subscription_tier']
        max_filters = 3 if tier == 'trial' else (5 if tier == 'basic' else 20)

        # Определяем emoji для тарифа
        tier_emoji = {
            'trial': '🎁',
            'basic': '⭐',
            'premium': '💎'
        }.get(tier, '🎁')

        tier_name = {
            'trial': 'Пробный',
            'basic': 'Базовый',
            'premium': 'Премиум'
        }.get(tier, 'Пробный')

        stats_text = (
            f"📊 <b>Ваша статистика</b>\n\n"
            f"{tier_emoji} <b>Тариф:</b> {tier_name}\n\n"
            f"<b>Активность:</b>\n"
            f"• Активных фильтров: {stats['active_filters']}/{max_filters}\n"
            f"• Всего совпадений: {stats['total_matches']}\n\n"
            f"<b>Уведомления сегодня:</b>\n"
            f"• Отправлено: {stats['notifications_today']}/{stats['notifications_limit']}\n"
            f"• Осталось: {stats['notifications_limit'] - stats['notifications_today']}\n\n"
        )

        # Добавляем предупреждение если квота почти исчерпана
        if stats['notifications_today'] >= stats['notifications_limit'] * 0.8:
            stats_text += "⚠️ <i>Квота уведомлений почти исчерпана!</i>\n\n"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬆️ Улучшить тариф", callback_data="sniper_plans")],
            [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        await callback.message.edit_text(
            stats_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        await callback.message.answer(
            f"❌ Ошибка при получении статистики: {str(e)}"
        )


# ============================================
# ВСЕ МОИ ТЕНДЕРЫ
# ============================================

@router.callback_query(F.data == "sniper_all_tenders")
async def show_all_tenders(callback: CallbackQuery):
    """Генерация и отправка HTML отчета со всеми тендерами."""
    await callback.answer()

    try:
        db = await get_sniper_db()

        # Получаем пользователя
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        # Показываем прогресс
        progress_msg = await callback.message.answer(
            "🔄 <b>Генерирую отчет...</b>\n\n"
            "⏳ Собираю данные из базы...",
            parse_mode="HTML"
        )

        # Генерируем HTML отчет
        username = callback.from_user.first_name or callback.from_user.username or "Пользователь"
        report_path = await generate_all_tenders_html(
            user_id=user['id'],
            username=username,
            limit=100  # Последние 100 тендеров
        )

        # Получаем количество тендеров
        tenders = await db.get_user_tenders(user['id'], limit=100)
        tender_count = len(tenders)

        await progress_msg.edit_text(
            "✅ <b>Отчет готов!</b>\n\n"
            f"📊 Тендеров в отчете: {tender_count}\n"
            "📄 Отправляю файл...",
            parse_mode="HTML"
        )

        # Отправляем HTML файл
        if tender_count > 0:
            await callback.message.answer_document(
                document=FSInputFile(report_path),
                caption=(
                    f"📊 <b>Все ваши тендеры</b>\n\n"
                    f"Отображено: {tender_count} тендеров\n"
                    f"Сгенерировано: {progress_msg.date.strftime('%d.%m.%Y %H:%M')}\n\n"
                    f"Откройте HTML файл в браузере для просмотра."
                ),
                parse_mode="HTML"
            )

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
                [InlineKeyboardButton(text="🎯 Меню Sniper", callback_data="sniper_menu")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])

            await callback.message.answer(
                "✨ Готово! Откройте HTML файл для просмотра всех тендеров.",
                reply_markup=keyboard
            )
        else:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Новый поиск", callback_data="sniper_new_search")],
                [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
                [InlineKeyboardButton(text="🎯 Меню Sniper", callback_data="sniper_menu")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])

            await progress_msg.edit_text(
                "📭 <b>У вас пока нет тендеров</b>\n\n"
                "Создайте фильтры и включите автоматический мониторинг!\n"
                "Бот будет отправлять вам уведомления о новых подходящих тендерах.",
                reply_markup=keyboard,
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"Ошибка генерации отчета: {e}", exc_info=True)
        await callback.message.answer(
            f"❌ Ошибка при генерации отчета: {str(e)}"
        )


# ============================================
# ТАРИФНЫЕ ПЛАНЫ
# ============================================

@router.callback_query(F.data == "sniper_plans")
async def show_subscription_plans(callback: CallbackQuery):
    """Показать тарифные планы."""
    await callback.answer()

    plans_text = (
        "💎 <b>Тарифные планы Tender Sniper</b>\n\n"

        "🎁 <b>Пробный период (14 дней)</b>\n"
        "• 3 фильтра мониторинга\n"
        "• 20 уведомлений в день\n"
        "• Мгновенный поиск\n"
        "• Избранное\n\n"

        "⭐ <b>Basic — от 1 490 ₽/мес</b>\n"
        "• 5 фильтров мониторинга\n"
        "• 100 уведомлений в день\n"
        "• Экспорт в Excel\n"
        "• Напоминания о тендерах\n"
        "• Telegram-поддержка\n\n"

        "💎 <b>Premium — от 2 990 ₽/мес</b>\n"
        "• 20 фильтров мониторинга\n"
        "• Безлимит уведомлений\n"
        "• Архивный поиск\n"
        "• Настройки фильтров (ИНН, чёрный список)\n"
        "• Доступ к бета-функциям\n"
        "• Приоритетная поддержка\n\n"

        "💰 <b>Скидки:</b> 10% за 3 мес, 20% за 6 мес\n\n"

        "<i>Выберите тариф для просмотра цен:</i>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Выбрать Basic", callback_data="subscription_select_basic")],
        [InlineKeyboardButton(text="💎 Выбрать Premium", callback_data="subscription_select_premium")],
        [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await callback.message.edit_text(
        plans_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# Обработчик оплаты перенесён в subscriptions.py (subscription_pay_basic/premium)


# ============================================
# МОИ ФИЛЬТРЫ
# ============================================

# НЕ используем декоратор - handler зарегистрирован в menu_priority.py
# @router.callback_query(F.data == "sniper_my_filters")
async def show_my_filters(callback: CallbackQuery):
    """Показать список фильтров пользователя."""
    # НЕ вызываем callback.answer() здесь - он уже вызван в menu_priority.py

    try:
        db = await get_sniper_db()

        # Получаем или создаем пользователя
        user = await db.get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await db.create_or_update_user(
                telegram_id=callback.from_user.id,
                username=callback.from_user.username,
                first_name=callback.from_user.first_name,
                subscription_tier='trial'
            )
            user = await db.get_user_by_telegram_id(callback.from_user.id)

        # Получаем фильтры
        filters = await db.get_active_filters(user['id'])

        # Проверяем корзину
        deleted_filters = await db.get_deleted_filters(user['id'])
        trash_count = len(deleted_filters)

        if not filters:
            buttons = [
                [InlineKeyboardButton(text="➕ Создать первый фильтр", callback_data="sniper_new_search")],
            ]
            if trash_count > 0:
                buttons.append([InlineKeyboardButton(text=f"🗑 Корзина ({trash_count})", callback_data="sniper_trash_bin")])
            buttons.append([InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")])
            buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

            await callback.message.edit_text(
                "📋 <b>У вас пока нет фильтров</b>\n\n"
                "Создайте первый фильтр для автоматического мониторинга тендеров.",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            return

        # Формируем список фильтров
        filters_text = "📋 <b>Ваши фильтры мониторинга</b>\n\n"

        keyboard_buttons = []
        for i, f in enumerate(filters, 1):
            keywords = f.get('keywords', [])
            price_range = ""
            if f.get('price_min') or f.get('price_max'):
                price_min = f"{f['price_min']:,}" if f.get('price_min') else "0"
                price_max = f"{f['price_max']:,}" if f.get('price_max') else "∞"
                price_range = f"{price_min} - {price_max} ₽"

            filters_text += (
                f"{i}. <b>{f['name']}</b>\n"
                f"   🔑 {', '.join(keywords[:3])}\n"
            )
            if price_range:
                filters_text += f"   💰 {price_range}\n"

            filters_text += f"   📊 Совпадений: {f.get('match_count', 0)}\n\n"

            # Кнопки для каждого фильтра
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"📝 {f['name'][:20]}",
                    callback_data=f"sniper_filter_{f['id']}"
                )
            ])

        keyboard_buttons.append([
            InlineKeyboardButton(text="➕ Добавить фильтр", callback_data="sniper_new_search")
        ])
        keyboard_buttons.append([
            InlineKeyboardButton(text="🗑️ Удалить все фильтры", callback_data="confirm_delete_all_filters")
        ])
        if trash_count > 0:
            keyboard_buttons.append([
                InlineKeyboardButton(text=f"🗑 Корзина ({trash_count})", callback_data="sniper_trash_bin")
            ])
        keyboard_buttons.append([
            InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")
        ])
        keyboard_buttons.append([
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        await callback.message.edit_text(
            filters_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"❌ Ошибка в show_my_filters: {e}", exc_info=True)
        try:
            await callback.message.edit_text(
                f"❌ <b>Ошибка при получении фильтров</b>\n\n{str(e)[:200]}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Повторить", callback_data="sniper_my_filters")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
                ])
            )
        except Exception:
            await callback.message.answer(
                f"❌ Ошибка при получении фильтров: {str(e)[:200]}"
            )


async def show_my_filters_message(message: Message):
    """Показать список фильтров (для Message вместо Callback)."""
    try:
        db = await get_sniper_db()

        # Получаем или создаем пользователя
        user = await db.get_user_by_telegram_id(message.from_user.id)
        if not user:
            await db.create_or_update_user(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                subscription_tier='trial'
            )
            user = await db.get_user_by_telegram_id(message.from_user.id)

        # Получаем фильтры
        filters = await db.get_active_filters(user['id'])

        # Проверяем корзину
        deleted_filters = await db.get_deleted_filters(user['id'])
        trash_count = len(deleted_filters)

        if not filters:
            buttons = [
                [InlineKeyboardButton(text="➕ Создать первый фильтр", callback_data="sniper_new_search")],
            ]
            if trash_count > 0:
                buttons.append([InlineKeyboardButton(text=f"🗑 Корзина ({trash_count})", callback_data="sniper_trash_bin")])
            buttons.append([InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")])
            buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

            await message.answer(
                "📋 <b>У вас пока нет фильтров</b>\n\n"
                "Создайте первый фильтр для автоматического мониторинга тендеров.",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            return

        # Формируем список фильтров
        filters_text = "📋 <b>Ваши фильтры мониторинга</b>\n\n"

        keyboard_buttons = []
        for i, f in enumerate(filters, 1):
            keywords = f.get('keywords', [])
            price_range = ""
            if f.get('price_min') or f.get('price_max'):
                price_min = f"{f['price_min']:,}" if f.get('price_min') else "0"
                price_max = f"{f['price_max']:,}" if f.get('price_max') else "∞"
                price_range = f"{price_min} - {price_max} ₽"

            filters_text += (
                f"{i}. <b>{f['name']}</b>\n"
                f"   🔑 {', '.join(keywords[:3])}\n"
            )
            if price_range:
                filters_text += f"   💰 {price_range}\n"

            filters_text += f"   📊 Совпадений: {f.get('match_count', 0)}\n\n"

            # Кнопки для каждого фильтра
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"📝 {f['name'][:20]}",
                    callback_data=f"sniper_filter_{f['id']}"
                )
            ])

        keyboard_buttons.append([
            InlineKeyboardButton(text="➕ Добавить фильтр", callback_data="sniper_new_search")
        ])
        keyboard_buttons.append([
            InlineKeyboardButton(text="🗑️ Удалить все фильтры", callback_data="confirm_delete_all_filters")
        ])
        if trash_count > 0:
            keyboard_buttons.append([
                InlineKeyboardButton(text=f"🗑 Корзина ({trash_count})", callback_data="sniper_trash_bin")
            ])
        keyboard_buttons.append([
            InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")
        ])
        keyboard_buttons.append([
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        await message.answer(
            filters_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        await message.answer(
            f"❌ Ошибка при получении фильтров: {str(e)}"
        )


# ============================================
# СОЗДАНИЕ ФИЛЬТРА - УДАЛЕНО
# Используется sniper_search.py с FilterSearchStates
# Рефакторинг 2024-12-19: унификация FSM
# ============================================


# ============================================
# ПОМОЩЬ
# ============================================

@router.callback_query(F.data == "sniper_help")
async def show_sniper_help(callback: CallbackQuery):
    """Показать главную страницу справки по Tender Sniper."""
    await callback.answer()

    from bot.utils.help_messages import HELP_MAIN

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Быстрый старт", callback_data="help_quick_start")],
        [InlineKeyboardButton(text="🎯 Создание фильтров", callback_data="help_filters")],
        [InlineKeyboardButton(text="📊 Понимание результатов", callback_data="help_results")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="help_settings")],
        [InlineKeyboardButton(text="❓ FAQ", callback_data="help_faq")],
        [InlineKeyboardButton(text="🔧 Troubleshooting", callback_data="help_troubleshooting")],
        [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")],
    ])

    await callback.message.edit_text(
        HELP_MAIN,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "help_quick_start")
async def show_help_quick_start(callback: CallbackQuery):
    """Показать раздел 'Быстрый старт'."""
    await callback.answer()

    from bot.utils.help_messages import HELP_QUICK_START

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Начать обучение", callback_data="start_onboarding")],
        [InlineKeyboardButton(text="« Назад к справке", callback_data="sniper_help")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await callback.message.edit_text(
        HELP_QUICK_START,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "help_filters")
async def show_help_filters(callback: CallbackQuery):
    """Показать раздел 'Создание фильтров'."""
    await callback.answer()

    from bot.utils.help_messages import HELP_CREATING_FILTERS

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Создать фильтр", callback_data="sniper_new_search")],
        [InlineKeyboardButton(text="« Назад к справке", callback_data="sniper_help")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await callback.message.edit_text(
        HELP_CREATING_FILTERS,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "help_results")
async def show_help_results(callback: CallbackQuery):
    """Показать раздел 'Понимание результатов'."""
    await callback.answer()

    from bot.utils.help_messages import HELP_UNDERSTANDING_RESULTS

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад к справке", callback_data="sniper_help")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await callback.message.edit_text(
        HELP_UNDERSTANDING_RESULTS,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "help_settings")
async def show_help_settings(callback: CallbackQuery):
    """Показать раздел 'Настройки'."""
    await callback.answer()

    from bot.utils.help_messages import HELP_SETTINGS

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Посмотреть тарифы", callback_data="sniper_plans")],
        [InlineKeyboardButton(text="« Назад к справке", callback_data="sniper_help")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await callback.message.edit_text(
        HELP_SETTINGS,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "help_faq")
async def show_help_faq(callback: CallbackQuery):
    """Показать раздел FAQ."""
    await callback.answer()

    from bot.utils.help_messages import HELP_FAQ

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад к справке", callback_data="sniper_help")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await callback.message.edit_text(
        HELP_FAQ,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "help_troubleshooting")
async def show_help_troubleshooting(callback: CallbackQuery):
    """Показать раздел Troubleshooting."""
    await callback.answer()

    from bot.utils.help_messages import HELP_TROUBLESHOOTING

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад к справке", callback_data="sniper_help")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await callback.message.edit_text(
        HELP_TROUBLESHOOTING,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# ============================================
# ПРОСМОТР И РЕДАКТИРОВАНИЕ ФИЛЬТРА
# ============================================

async def get_filter_statistics(filter_id: int, user_id: int) -> dict:
    """
    Получить статистику эффективности фильтра.

    Returns:
        dict: {total_found, favorites_added, hidden, effectiveness, recommendations}
    """
    from database import DatabaseSession, SniperNotification, TenderFavorite, HiddenTender
    from sqlalchemy import select, func, and_

    stats = {
        'total_found': 0,
        'favorites_added': 0,
        'hidden': 0,
        'effectiveness': 0,
        'recommendations': []
    }

    try:
        async with DatabaseSession() as session:
            # Количество найденных тендеров по этому фильтру
            stats['total_found'] = await session.scalar(
                select(func.count(SniperNotification.id)).where(
                    SniperNotification.filter_id == filter_id
                )
            ) or 0

            # Количество добавленных в избранное
            # Получаем tender_numbers из notifications для этого фильтра
            notifications_result = await session.execute(
                select(SniperNotification.tender_number).where(
                    SniperNotification.filter_id == filter_id
                )
            )
            tender_numbers = [r[0] for r in notifications_result.all() if r[0]]

            if tender_numbers:
                stats['favorites_added'] = await session.scalar(
                    select(func.count(TenderFavorite.id)).where(
                        and_(
                            TenderFavorite.user_id == user_id,
                            TenderFavorite.tender_number.in_(tender_numbers)
                        )
                    )
                ) or 0

                stats['hidden'] = await session.scalar(
                    select(func.count(HiddenTender.id)).where(
                        and_(
                            HiddenTender.user_id == user_id,
                            HiddenTender.tender_number.in_(tender_numbers)
                        )
                    )
                ) or 0

            # Расчёт эффективности
            if stats['total_found'] > 0:
                positive = stats['favorites_added']
                negative = stats['hidden']
                stats['effectiveness'] = int((positive / (positive + negative + 1)) * 100) if (positive + negative) > 0 else 50

            # Рекомендации
            if stats['total_found'] == 0:
                stats['recommendations'].append("Расширьте ключевые слова или увеличьте ценовой диапазон")
            elif stats['total_found'] > 50 and stats['favorites_added'] < 5:
                stats['recommendations'].append("Добавьте более точные ключевые слова")
                stats['recommendations'].append("Сузьте ценовой диапазон")
            elif stats['hidden'] > stats['favorites_added'] * 2:
                stats['recommendations'].append("Много неподходящих тендеров - уточните критерии")
            elif stats['effectiveness'] > 70:
                stats['recommendations'].append("Фильтр работает отлично!")

    except Exception as e:
        logger.error(f"Error getting filter stats: {e}")

    return stats


@router.callback_query(F.data.startswith("sniper_filter_"))
async def show_filter_details(callback: CallbackQuery):
    """Показать детальную информацию о фильтре."""
    await callback.answer()

    try:
        # Извлекаем ID фильтра
        filter_id = int(callback.data.replace("sniper_filter_", ""))

        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await callback.message.answer("❌ Фильтр не найден")
            return

        # Получаем user_id
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)
        user_id = sniper_user['id'] if sniper_user else 0

        # Формируем текст с информацией о фильтре
        keywords = filter_data.get('keywords', [])
        exclude_keywords = filter_data.get('exclude_keywords', [])
        price_min = filter_data.get('price_min')
        price_max = filter_data.get('price_max')
        regions = filter_data.get('regions', [])
        law_type = filter_data.get('law_type')
        tender_types = filter_data.get('tender_types', [])
        is_active = filter_data.get('is_active', True)

        status_emoji = "✅" if is_active else "⏸️"
        status_text = "Активен" if is_active else "Приостановлен"

        text = f"📋 <b>Фильтр: {filter_data['name']}</b>\n\n"
        text += f"Статус: {status_emoji} {status_text}\n\n"

        if keywords:
            text += f"🔑 <b>Ключевые слова:</b>\n{', '.join(keywords)}\n\n"

        if exclude_keywords:
            text += f"🚫 <b>Исключить:</b>\n{', '.join(exclude_keywords)}\n\n"

        if price_min or price_max:
            price_min_str = f"{price_min:,}" if price_min else "0"
            price_max_str = f"{price_max:,}" if price_max else "∞"
            text += f"💰 <b>Цена:</b> {price_min_str} - {price_max_str} ₽\n\n"

        if regions:
            text += f"📍 <b>Регионы:</b> {', '.join(regions[:3])}"
            if len(regions) > 3:
                text += f" и еще {len(regions) - 3}"
            text += "\n\n"

        if law_type:
            text += f"📜 <b>Закон:</b> {law_type}\n\n"

        if tender_types:
            text += f"📦 <b>Тип закупки:</b> {', '.join(tender_types)}\n\n"

        # Добавляем статистику фильтра
        stats = await get_filter_statistics(filter_id, user_id)

        text += "━━━━━━━━━━━━━━━\n"
        text += "📊 <b>СТАТИСТИКА</b>\n\n"
        text += f"📬 Найдено тендеров: <b>{stats['total_found']}</b>\n"
        text += f"⭐ В избранном: <b>{stats['favorites_added']}</b>\n"
        text += f"👎 Скрыто: <b>{stats['hidden']}</b>\n"

        # Индикатор эффективности
        eff = stats['effectiveness']
        if eff >= 70:
            eff_emoji = "🟢"
        elif eff >= 40:
            eff_emoji = "🟡"
        else:
            eff_emoji = "🔴"
        text += f"{eff_emoji} Эффективность: <b>{eff}%</b>\n\n"

        # Рекомендации
        if stats['recommendations']:
            text += "💡 <b>Рекомендации:</b>\n"
            for rec in stats['recommendations'][:2]:
                text += f"• {rec}\n"

        # Кнопки управления фильтром
        keyboard_buttons = [
            [InlineKeyboardButton(
                text="✏️ Редактировать",
                callback_data=f"edit_filter_menu_{filter_id}"
            )],
            [InlineKeyboardButton(
                text="📋 Дублировать фильтр",
                callback_data=f"duplicate_filter_{filter_id}"
            )],
            [InlineKeyboardButton(
                text="⏸️ Приостановить" if is_active else "▶️ Возобновить",
                callback_data=f"toggle_filter_{filter_id}"
            )],
            [InlineKeyboardButton(
                text="🗑️ Удалить фильтр",
                callback_data=f"delete_filter_{filter_id}"
            )],
            [InlineKeyboardButton(text="« Назад к фильтрам", callback_data="sniper_my_filters")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ]

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {str(e)}")


# Добавляем FSM state для редактирования цены
class EditFilterStates(StatesGroup):
    """Состояния для редактирования фильтра."""
    waiting_for_new_price_range = State()
    waiting_for_new_keywords = State()
    waiting_for_new_exclude_keywords = State()
    waiting_for_new_customer_keywords = State()
    selecting_regions = State()
    selecting_tender_types = State()


@router.callback_query(F.data.startswith("edit_filter_price_"))
async def start_edit_filter_price(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование ценового диапазона фильтра."""
    await callback.answer()

    filter_id = int(callback.data.replace("edit_filter_price_", ""))

    await state.update_data(editing_filter_id=filter_id)
    await state.set_state(EditFilterStates.waiting_for_new_price_range)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Отмена", callback_data=f"sniper_filter_{filter_id}")]
    ])

    await callback.message.edit_text(
        "✏️ <b>Редактирование ценового диапазона</b>\n\n"
        "Введите новый диапазон цен в формате:\n"
        "<code>мин макс</code>\n\n"
        "Пример: <code>100000 5000000</code>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.message(EditFilterStates.waiting_for_new_price_range)
async def process_edit_filter_price(message: Message, state: FSMContext):
    """Обработка нового ценового диапазона."""
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            await message.answer(
                "⚠️ Неверный формат. Введите два числа через пробел:\n"
                "Пример: <code>100000 5000000</code>",
                parse_mode="HTML"
            )
            return

        price_min = int(parts[0])
        price_max = int(parts[1])

        if price_min >= price_max:
            await message.answer("⚠️ Минимальная цена должна быть меньше максимальной")
            return

        # Получаем ID фильтра из state
        data = await state.get_data()
        filter_id = data.get('editing_filter_id')

        if not filter_id:
            await message.answer("❌ Ошибка: ID фильтра не найден")
            await state.clear()
            return

        # Обновляем фильтр в базе
        db = await get_sniper_db()
        await db.update_filter(
            filter_id=filter_id,
            price_min=price_min,
            price_max=price_max
        )

        await state.clear()

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Продолжить", callback_data=f"edit_filter_menu_{filter_id}")],
            [InlineKeyboardButton(text="📋 К фильтру", callback_data=f"sniper_filter_{filter_id}")]
        ])

        await message.answer(
            f"✅ <b>Ценовой диапазон обновлен!</b>\n\n"
            f"💰 Новая цена: {price_min:,} - {price_max:,} ₽",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except ValueError:
        await message.answer(
            "⚠️ Введите корректные числа.\n"
            "Пример: <code>100000 5000000</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка при обновлении фильтра: {str(e)}")
        await state.clear()


# ============================================
# ПОДМЕНЮ РЕДАКТИРОВАНИЯ ФИЛЬТРА
# ============================================

@router.callback_query(F.data.startswith("edit_filter_menu_"))
async def show_edit_filter_menu(callback: CallbackQuery, state: FSMContext):
    """Показать подменю редактирования фильтра со всеми полями."""
    await callback.answer()
    await state.clear()

    try:
        filter_id = int(callback.data.replace("edit_filter_menu_", ""))
        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await callback.message.edit_text("❌ Фильтр не найден", parse_mode="HTML")
            return

        keywords = filter_data.get('keywords', []) or []
        exclude_keywords = filter_data.get('exclude_keywords', []) or []
        price_min = filter_data.get('price_min')
        price_max = filter_data.get('price_max')
        regions = filter_data.get('regions', []) or []
        tender_types = filter_data.get('tender_types', []) or []
        law_type = filter_data.get('law_type')
        customer_keywords = filter_data.get('customer_keywords', []) or []

        text = f"✏️ <b>Редактирование фильтра «{filter_data['name']}»</b>\n\n"

        text += f"🔑 <b>Ключевые слова:</b> {', '.join(keywords) if keywords else '(не заданы)'}\n"
        text += f"🚫 <b>Исключения:</b> {', '.join(exclude_keywords) if exclude_keywords else '(не заданы)'}\n"

        if price_min or price_max:
            p_min = f"{price_min:,}" if price_min else "0"
            p_max = f"{price_max:,}" if price_max else "∞"
            text += f"💰 <b>Цена:</b> {p_min} — {p_max} ₽\n"
        else:
            text += "💰 <b>Цена:</b> (не задана)\n"

        if regions:
            r_text = ', '.join(regions[:3])
            if len(regions) > 3:
                r_text += f" (+{len(regions) - 3})"
            text += f"📍 <b>Регионы:</b> {r_text}\n"
        else:
            text += "📍 <b>Регионы:</b> (все)\n"

        text += f"📦 <b>Тип:</b> {', '.join(tender_types) if tender_types else 'Любые'}\n"
        text += f"📜 <b>Закон:</b> {law_type if law_type else 'Любой'}\n"
        text += f"🏢 <b>Заказчик:</b> {', '.join(customer_keywords) if customer_keywords else '(не задано)'}\n"

        buttons = [
            [InlineKeyboardButton(text="🔑 Ключевые слова", callback_data=f"edit_fkw_{filter_id}")],
            [InlineKeyboardButton(text="🚫 Исключения", callback_data=f"edit_fex_{filter_id}")],
            [InlineKeyboardButton(text="💰 Цена", callback_data=f"edit_filter_price_{filter_id}")],
            [InlineKeyboardButton(text="📍 Регионы", callback_data=f"edit_frg_{filter_id}")],
            [InlineKeyboardButton(text="📦 Тип закупки", callback_data=f"edit_ftt_{filter_id}")],
            [InlineKeyboardButton(text="📜 Закон", callback_data=f"edit_flw_{filter_id}")],
            [InlineKeyboardButton(text="🏢 Заказчик", callback_data=f"edit_fck_{filter_id}")],
            [InlineKeyboardButton(text="« Назад к фильтру", callback_data=f"sniper_filter_{filter_id}")]
        ]

        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка отображения меню редактирования: {e}", exc_info=True)
        await callback.message.edit_text("❌ Произошла ошибка", parse_mode="HTML")


# --- Редактирование ключевых слов ---

@router.callback_query(F.data.startswith("edit_fkw_"))
async def start_edit_keywords(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование ключевых слов."""
    await callback.answer()
    filter_id = int(callback.data.replace("edit_fkw_", ""))
    db = await get_sniper_db()
    filter_data = await db.get_filter_by_id(filter_id)
    current = ', '.join(filter_data.get('keywords', []) or []) if filter_data else ''

    await state.update_data(editing_filter_id=filter_id)
    await state.set_state(EditFilterStates.waiting_for_new_keywords)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Отмена", callback_data=f"edit_filter_menu_{filter_id}")]
    ])

    await callback.message.edit_text(
        f"🔑 <b>Редактирование ключевых слов</b>\n\n"
        f"Текущие: <code>{current or '(не заданы)'}</code>\n\n"
        f"Введите новые ключевые слова через запятую:\n"
        f"Пример: <code>бумага, картон, канцелярия</code>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.message(EditFilterStates.waiting_for_new_keywords)
async def process_edit_keywords(message: Message, state: FSMContext):
    """Обработка новых ключевых слов."""
    try:
        keywords = [kw.strip() for kw in message.text.split(',') if kw.strip()]

        if not keywords:
            await message.answer("⚠️ Введите хотя бы одно ключевое слово")
            return

        if len(keywords) > MAX_KEYWORDS:
            await message.answer(
                f"⚠️ Слишком много ключевых слов: <b>{len(keywords)}</b>.\n"
                f"Максимум — <b>{MAX_KEYWORDS}</b>.\n\n"
                f"Оставьте самые важные — качество важнее количества.",
                parse_mode="HTML"
            )
            return

        data = await state.get_data()
        filter_id = data.get('editing_filter_id')
        if not filter_id:
            await message.answer("❌ Ошибка: ID фильтра не найден")
            await state.clear()
            return

        db = await get_sniper_db()
        await db.update_filter(filter_id=filter_id, keywords=keywords)

        # Перегенерация ai_intent при изменении ключевых слов
        try:
            filter_data = await db.get_filter_by_id(filter_id)
            if filter_data:
                from tender_sniper.ai_relevance_checker import generate_intent
                ai_intent = await generate_intent(
                    filter_name=filter_data.get('name', ''),
                    keywords=keywords,
                    exclude_keywords=filter_data.get('exclude_keywords', [])
                )
                if ai_intent:
                    await db.update_filter(filter_id=filter_id, ai_intent=ai_intent)
                    logger.info(f"🔄 ai_intent обновлён для фильтра {filter_id}")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось обновить ai_intent: {e}")

        await state.clear()

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Продолжить", callback_data=f"edit_filter_menu_{filter_id}")],
            [InlineKeyboardButton(text="📋 К фильтру", callback_data=f"sniper_filter_{filter_id}")]
        ])

        await message.answer(
            f"✅ <b>Ключевые слова обновлены!</b>\n\n"
            f"🔑 {', '.join(keywords)}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
        await state.clear()


# --- Редактирование исключений ---

@router.callback_query(F.data.startswith("edit_fex_"))
async def start_edit_exclude_keywords(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование слов-исключений."""
    await callback.answer()
    filter_id = int(callback.data.replace("edit_fex_", ""))
    db = await get_sniper_db()
    filter_data = await db.get_filter_by_id(filter_id)
    current = ', '.join(filter_data.get('exclude_keywords', []) or []) if filter_data else ''

    await state.update_data(editing_filter_id=filter_id)
    await state.set_state(EditFilterStates.waiting_for_new_exclude_keywords)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Отмена", callback_data=f"edit_filter_menu_{filter_id}")]
    ])

    await callback.message.edit_text(
        f"🚫 <b>Редактирование исключений</b>\n\n"
        f"Текущие: <code>{current or '(не заданы)'}</code>\n\n"
        f"Введите слова-исключения через запятую:\n"
        f"Пример: <code>ремонт, монтаж</code>\n\n"
        f"Отправьте <code>-</code> чтобы очистить.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.message(EditFilterStates.waiting_for_new_exclude_keywords)
async def process_edit_exclude_keywords(message: Message, state: FSMContext):
    """Обработка новых слов-исключений."""
    try:
        data = await state.get_data()
        filter_id = data.get('editing_filter_id')
        if not filter_id:
            await message.answer("❌ Ошибка: ID фильтра не найден")
            await state.clear()
            return

        text = message.text.strip()
        if text == '-':
            exclude_keywords = []
        else:
            exclude_keywords = [kw.strip() for kw in text.split(',') if kw.strip()]
            if len(exclude_keywords) > MAX_EXCLUDE_KEYWORDS:
                await message.answer(
                    f"⚠️ Слишком много слов-исключений: <b>{len(exclude_keywords)}</b>.\n"
                    f"Максимум — <b>{MAX_EXCLUDE_KEYWORDS}</b>.",
                    parse_mode="HTML"
                )
                return

        db = await get_sniper_db()
        await db.update_filter(filter_id=filter_id, exclude_keywords=exclude_keywords)

        # Перегенерация ai_intent при изменении исключений
        try:
            filter_data = await db.get_filter_by_id(filter_id)
            if filter_data:
                from tender_sniper.ai_relevance_checker import generate_intent
                ai_intent = await generate_intent(
                    filter_name=filter_data.get('name', ''),
                    keywords=filter_data.get('keywords', []),
                    exclude_keywords=exclude_keywords
                )
                if ai_intent:
                    await db.update_filter(filter_id=filter_id, ai_intent=ai_intent)
                    logger.info(f"🔄 ai_intent обновлён для фильтра {filter_id}")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось обновить ai_intent: {e}")

        await state.clear()

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Продолжить", callback_data=f"edit_filter_menu_{filter_id}")],
            [InlineKeyboardButton(text="📋 К фильтру", callback_data=f"sniper_filter_{filter_id}")]
        ])

        result_text = ', '.join(exclude_keywords) if exclude_keywords else 'очищены'
        await message.answer(
            f"✅ <b>Исключения обновлены!</b>\n\n"
            f"🚫 {result_text}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
        await state.clear()


# --- Редактирование заказчика ---

@router.callback_query(F.data.startswith("edit_fck_"))
async def start_edit_customer_keywords(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование ключевых слов заказчика."""
    await callback.answer()
    filter_id = int(callback.data.replace("edit_fck_", ""))
    db = await get_sniper_db()
    filter_data = await db.get_filter_by_id(filter_id)
    current = ', '.join(filter_data.get('customer_keywords', []) or []) if filter_data else ''

    await state.update_data(editing_filter_id=filter_id)
    await state.set_state(EditFilterStates.waiting_for_new_customer_keywords)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Отмена", callback_data=f"edit_filter_menu_{filter_id}")]
    ])

    await callback.message.edit_text(
        f"🏢 <b>Редактирование заказчика</b>\n\n"
        f"Текущие: <code>{current or '(не задано)'}</code>\n\n"
        f"Введите ключевые слова заказчика через запятую:\n"
        f"Пример: <code>университет, больница</code>\n\n"
        f"Отправьте <code>-</code> чтобы очистить.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.message(EditFilterStates.waiting_for_new_customer_keywords)
async def process_edit_customer_keywords(message: Message, state: FSMContext):
    """Обработка новых ключевых слов заказчика."""
    try:
        data = await state.get_data()
        filter_id = data.get('editing_filter_id')
        if not filter_id:
            await message.answer("❌ Ошибка: ID фильтра не найден")
            await state.clear()
            return

        text = message.text.strip()
        if text == '-':
            customer_keywords = []
        else:
            customer_keywords = [kw.strip() for kw in text.split(',') if kw.strip()]
            if len(customer_keywords) > MAX_CUSTOMER_KEYWORDS:
                await message.answer(
                    f"⚠️ Слишком много ключевых слов заказчика: <b>{len(customer_keywords)}</b>.\n"
                    f"Максимум — <b>{MAX_CUSTOMER_KEYWORDS}</b>.",
                    parse_mode="HTML"
                )
                return

        db = await get_sniper_db()
        await db.update_filter(filter_id=filter_id, customer_keywords=customer_keywords)
        await state.clear()

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Продолжить", callback_data=f"edit_filter_menu_{filter_id}")],
            [InlineKeyboardButton(text="📋 К фильтру", callback_data=f"sniper_filter_{filter_id}")]
        ])

        result_text = ', '.join(customer_keywords) if customer_keywords else 'очищено'
        await message.answer(
            f"✅ <b>Заказчик обновлен!</b>\n\n"
            f"🏢 {result_text}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
        await state.clear()


# --- Редактирование регионов (инлайн-тоглы по ФО) ---

@router.callback_query(F.data.startswith("edit_frg_"))
async def start_edit_regions(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование регионов фильтра."""
    await callback.answer()
    filter_id = int(callback.data.replace("edit_frg_", ""))

    db = await get_sniper_db()
    filter_data = await db.get_filter_by_id(filter_id)
    if not filter_data:
        await callback.message.edit_text("❌ Фильтр не найден", parse_mode="HTML")
        return

    current_regions = filter_data.get('regions', []) or []

    # Определяем выбранные ФО по текущим регионам
    from tender_sniper.regions import REGION_TO_DISTRICT, get_all_federal_districts, get_regions_by_district
    selected_districts = set()
    for region in current_regions:
        district = REGION_TO_DISTRICT.get(region.lower())
        if district:
            selected_districts.add(district)

    await state.update_data(
        editing_filter_id=filter_id,
        edit_selected_districts=list(selected_districts)
    )
    await state.set_state(EditFilterStates.selecting_regions)

    await _show_edit_regions_keyboard(callback.message, filter_id, selected_districts, state)


async def _show_edit_regions_keyboard(message, filter_id: int, selected_districts: set, state: FSMContext = None):
    """Показать клавиатуру выбора регионов по ФО."""
    from tender_sniper.regions import get_all_federal_districts, get_regions_by_district

    districts = get_all_federal_districts()
    buttons = []

    for d in districts:
        check = "✅" if d['name'] in selected_districts else "☐"
        regions_count = d['regions_count']
        buttons.append([InlineKeyboardButton(
            text=f"{check} {d['name']} ({regions_count})",
            callback_data=f"edf_rg_toggle:{d['name'][:20]}"
        )])

    buttons.append([
        InlineKeyboardButton(text="🗑 Очистить", callback_data=f"edf_rg_clear"),
        InlineKeyboardButton(text="✅ Сохранить", callback_data=f"edf_rg_save")
    ])
    buttons.append([InlineKeyboardButton(text="« Отмена", callback_data=f"edit_filter_menu_{filter_id}")])

    count = sum(len(get_regions_by_district(d)) for d in selected_districts)
    text = (
        f"📍 <b>Редактирование регионов</b>\n\n"
        f"Выбрано округов: {len(selected_districts)}, регионов: {count}\n\n"
        f"Нажмите на округ чтобы вкл/выкл:"
    )

    try:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data.startswith("edf_rg_toggle:"), EditFilterStates.selecting_regions)
async def edit_regions_toggle(callback: CallbackQuery, state: FSMContext):
    """Тогл федерального округа при редактировании."""
    await callback.answer()

    district_prefix = callback.data.split(":", 1)[1]

    # Находим полное имя округа
    from tender_sniper.regions import get_all_federal_districts
    districts = get_all_federal_districts()
    district_name = None
    for d in districts:
        if d['name'].startswith(district_prefix):
            district_name = d['name']
            break

    if not district_name:
        return

    data = await state.get_data()
    filter_id = data.get('editing_filter_id')
    selected = set(data.get('edit_selected_districts', []))

    if district_name in selected:
        selected.discard(district_name)
    else:
        selected.add(district_name)

    await state.update_data(edit_selected_districts=list(selected))
    await _show_edit_regions_keyboard(callback.message, filter_id, selected, state)


@router.callback_query(F.data == "edf_rg_clear", EditFilterStates.selecting_regions)
async def edit_regions_clear(callback: CallbackQuery, state: FSMContext):
    """Очистить все регионы."""
    await callback.answer("Регионы очищены")
    data = await state.get_data()
    filter_id = data.get('editing_filter_id')
    await state.update_data(edit_selected_districts=[])
    await _show_edit_regions_keyboard(callback.message, filter_id, set(), state)


@router.callback_query(F.data == "edf_rg_save", EditFilterStates.selecting_regions)
async def edit_regions_save(callback: CallbackQuery, state: FSMContext):
    """Сохранить выбранные регионы."""
    await callback.answer()
    from tender_sniper.regions import get_regions_by_district

    data = await state.get_data()
    filter_id = data.get('editing_filter_id')
    selected_districts = data.get('edit_selected_districts', [])

    # Собираем все регионы из выбранных округов
    regions = []
    for district in selected_districts:
        regions.extend(get_regions_by_district(district))

    db = await get_sniper_db()
    await db.update_filter(filter_id=filter_id, regions=regions if regions else [])
    await state.clear()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Продолжить", callback_data=f"edit_filter_menu_{filter_id}")],
        [InlineKeyboardButton(text="📋 К фильтру", callback_data=f"sniper_filter_{filter_id}")]
    ])

    count_text = f"{len(regions)} регионов из {len(selected_districts)} округов" if regions else "все регионы (без ограничений)"
    await callback.message.edit_text(
        f"✅ <b>Регионы обновлены!</b>\n\n"
        f"📍 {count_text}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# --- Редактирование типа закупки (инлайн-тоглы) ---

@router.callback_query(F.data.startswith("edit_ftt_"))
async def start_edit_tender_types(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование типа закупки."""
    await callback.answer()
    filter_id = int(callback.data.replace("edit_ftt_", ""))

    db = await get_sniper_db()
    filter_data = await db.get_filter_by_id(filter_id)
    if not filter_data:
        await callback.message.edit_text("❌ Фильтр не найден", parse_mode="HTML")
        return

    current_types = filter_data.get('tender_types', []) or []

    # Маппинг значений на коды
    from bot.handlers.sniper_wizard_new import TENDER_TYPES
    selected_codes = []
    for code, info in TENDER_TYPES.items():
        if code == 'any':
            continue
        if info['value'] in current_types:
            selected_codes.append(code)

    await state.update_data(
        editing_filter_id=filter_id,
        edit_selected_types=selected_codes
    )
    await state.set_state(EditFilterStates.selecting_tender_types)

    await _show_edit_tender_types_keyboard(callback.message, filter_id, selected_codes)


async def _show_edit_tender_types_keyboard(message, filter_id: int, selected_codes: list):
    """Показать клавиатуру выбора типов закупки."""
    from bot.handlers.sniper_wizard_new import TENDER_TYPES

    buttons = []
    for code, info in TENDER_TYPES.items():
        if code == 'any':
            continue
        check = "✅" if code in selected_codes else "☐"
        buttons.append([InlineKeyboardButton(
            text=f"{check} {info['icon']} {info['name']}",
            callback_data=f"edf_tt_toggle:{code}"
        )])

    buttons.append([InlineKeyboardButton(text="✅ Сохранить", callback_data="edf_tt_save")])
    buttons.append([InlineKeyboardButton(text="« Отмена", callback_data=f"edit_filter_menu_{filter_id}")])

    text = (
        f"📦 <b>Редактирование типа закупки</b>\n\n"
        f"Выберите типы (или оставьте пустым — любые):"
    )

    try:
        await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data.startswith("edf_tt_toggle:"), EditFilterStates.selecting_tender_types)
async def edit_tender_type_toggle(callback: CallbackQuery, state: FSMContext):
    """Тогл типа закупки при редактировании."""
    await callback.answer()
    type_code = callback.data.split(":", 1)[1]

    data = await state.get_data()
    filter_id = data.get('editing_filter_id')
    selected = data.get('edit_selected_types', [])

    if type_code in selected:
        selected.remove(type_code)
    else:
        selected.append(type_code)

    await state.update_data(edit_selected_types=selected)
    await _show_edit_tender_types_keyboard(callback.message, filter_id, selected)


@router.callback_query(F.data == "edf_tt_save", EditFilterStates.selecting_tender_types)
async def edit_tender_types_save(callback: CallbackQuery, state: FSMContext):
    """Сохранить выбранные типы закупки."""
    await callback.answer()
    from bot.handlers.sniper_wizard_new import TENDER_TYPES

    data = await state.get_data()
    filter_id = data.get('editing_filter_id')
    selected_codes = data.get('edit_selected_types', [])

    tender_types_list = [TENDER_TYPES[code]['value'] for code in selected_codes if TENDER_TYPES[code].get('value')]

    db = await get_sniper_db()
    await db.update_filter(filter_id=filter_id, tender_types=tender_types_list if tender_types_list else [])
    await state.clear()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Продолжить", callback_data=f"edit_filter_menu_{filter_id}")],
        [InlineKeyboardButton(text="📋 К фильтру", callback_data=f"sniper_filter_{filter_id}")]
    ])

    type_names = [TENDER_TYPES[code]['name'] for code in selected_codes]
    result_text = ', '.join(type_names) if type_names else 'Любые'
    await callback.message.edit_text(
        f"✅ <b>Тип закупки обновлен!</b>\n\n"
        f"📦 {result_text}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# --- Редактирование закона (одноклик, без FSM) ---

@router.callback_query(F.data.startswith("edit_flw_"))
async def start_edit_law_type(callback: CallbackQuery):
    """Показать выбор закона для фильтра."""
    await callback.answer()
    filter_id = int(callback.data.replace("edit_flw_", ""))

    from bot.handlers.sniper_wizard_new import LAW_TYPES

    buttons = []
    for law_code, law_info in LAW_TYPES.items():
        buttons.append([InlineKeyboardButton(
            text=f"{law_info['icon']} {law_info['name']}",
            callback_data=f"edf_lw_{filter_id}:{law_code}"
        )])

    buttons.append([InlineKeyboardButton(text="« Отмена", callback_data=f"edit_filter_menu_{filter_id}")])

    await callback.message.edit_text(
        "📜 <b>Выберите закон</b>\n\n"
        "Нажмите для выбора:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("edf_lw_"))
async def edit_law_type_select(callback: CallbackQuery):
    """Сохранить выбранный закон."""
    await callback.answer()
    from bot.handlers.sniper_wizard_new import LAW_TYPES

    # edf_lw_{filter_id}:{law_code}
    parts = callback.data.replace("edf_lw_", "").split(":")
    filter_id = int(parts[0])
    law_code = parts[1]

    law_info = LAW_TYPES.get(law_code, LAW_TYPES['any'])
    law_value = law_info['value']  # None для "Любой"

    db = await get_sniper_db()
    await db.update_filter(filter_id=filter_id, law_type=law_value)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Продолжить", callback_data=f"edit_filter_menu_{filter_id}")],
        [InlineKeyboardButton(text="📋 К фильтру", callback_data=f"sniper_filter_{filter_id}")]
    ])

    await callback.message.edit_text(
        f"✅ <b>Закон обновлен!</b>\n\n"
        f"📜 {law_info['name']}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("toggle_filter_"))
async def toggle_filter_status(callback: CallbackQuery):
    """Переключить статус фильтра (активен/приостановлен)."""
    await callback.answer()

    # Проверяем admin-гард для групповых чатов
    chat = callback.message.chat if callback.message else None
    if chat and chat.type in ('group', 'supergroup'):
        from bot.handlers.group_chat import is_group_admin
        if not await is_group_admin(callback.bot, chat.id, callback.from_user.id):
            await callback.answer("Только администратор группы", show_alert=True)
            return

    try:
        filter_id = int(callback.data.replace("toggle_filter_", ""))

        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await callback.message.answer("❌ Фильтр не найден")
            return

        # Переключаем статус
        new_status = not filter_data.get('is_active', True)

        await db.update_filter(
            filter_id=filter_id,
            is_active=new_status
        )

        status_text = "возобновлен ▶️" if new_status else "приостановлен ⏸️"

        # Track filter toggle
        import asyncio
        try:
            from bot.analytics import track_filter_action
            asyncio.create_task(track_filter_action(
                callback.from_user.id, 'toggled',
                filter_name=filter_data.get('name'), filter_id=filter_id
            ))
        except Exception:
            pass

        await callback.answer(f"Фильтр {status_text}", show_alert=True)

        # Обновляем отображение фильтра
        await show_filter_details(callback)

    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {str(e)}")


@router.callback_query(F.data.startswith("duplicate_filter_"))
async def duplicate_filter_handler(callback: CallbackQuery):
    """Дублировать фильтр."""
    await callback.answer()

    try:
        filter_id = int(callback.data.replace("duplicate_filter_", ""))

        db = await get_sniper_db()

        # Проверяем лимит фильтров пользователя
        user = await db.get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        current_filters = await db.get_user_filters(user['id'], active_only=False)
        max_filters = user.get('filters_limit', 3)

        if len(current_filters) >= max_filters:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Улучшить тариф", callback_data="sniper_plans")],
                [InlineKeyboardButton(text="« Назад", callback_data=f"sniper_filter_{filter_id}")]
            ])
            await callback.message.edit_text(
                f"⚠️ <b>Достигнут лимит фильтров</b>\n\n"
                f"У вас уже {len(current_filters)} из {max_filters} фильтров.\n\n"
                f"Для создания новых фильтров улучшите тарифный план или удалите ненужные.",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            return

        # Дублируем фильтр
        new_filter_id = await db.duplicate_filter(filter_id)

        if not new_filter_id:
            await callback.message.answer("❌ Исходный фильтр не найден")
            return

        new_filter = await db.get_filter_by_id(new_filter_id)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📝 Открыть копию", callback_data=f"sniper_filter_{new_filter_id}")],
            [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        await callback.message.edit_text(
            f"✅ <b>Фильтр дублирован!</b>\n\n"
            f"Создана копия: <b>{new_filter['name']}</b>\n\n"
            f"Вы можете отредактировать копию по своему усмотрению.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка дублирования фильтра: {e}", exc_info=True)
        await callback.message.answer(f"❌ Ошибка: {str(e)}")


@router.callback_query(F.data.startswith("delete_filter_"))
async def delete_filter(callback: CallbackQuery):
    """Подтверждение удаления фильтра."""
    await callback.answer()

    # Проверяем admin-гард для групповых чатов
    chat = callback.message.chat if callback.message else None
    if chat and chat.type in ('group', 'supergroup'):
        from bot.handlers.group_chat import is_group_admin
        if not await is_group_admin(callback.bot, chat.id, callback.from_user.id):
            await callback.answer("Только администратор группы", show_alert=True)
            return

    try:
        filter_id = int(callback.data.replace("delete_filter_", ""))

        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await callback.message.answer("❌ Фильтр не найден")
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_filter_{filter_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"filter_detail_{filter_id}")]
        ])

        await callback.message.edit_text(
            f"⚠️ <b>Удалить фильтр?</b>\n\n"
            f"Фильтр «{filter_data['name']}» будет перемещён в корзину.\n"
            f"Вы сможете восстановить его позже.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {str(e)}")


@router.callback_query(F.data.startswith("confirm_delete_filter_"))
async def confirm_delete_filter(callback: CallbackQuery):
    """Удалить фильтр после подтверждения."""
    await callback.answer()

    # Проверяем admin-гард для групповых чатов
    chat = callback.message.chat if callback.message else None
    if chat and chat.type in ('group', 'supergroup'):
        from bot.handlers.group_chat import is_group_admin
        if not await is_group_admin(callback.bot, chat.id, callback.from_user.id):
            await callback.answer("Только администратор группы", show_alert=True)
            return

    try:
        filter_id = int(callback.data.replace("confirm_delete_filter_", ""))

        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await callback.message.answer("❌ Фильтр не найден")
            return

        # Удаляем фильтр
        await db.delete_filter(filter_id)

        # Track filter deletion
        import asyncio
        try:
            from bot.analytics import track_filter_action
            asyncio.create_task(track_filter_action(
                callback.from_user.id, 'deleted',
                filter_name=filter_data.get('name'), filter_id=filter_id
            ))
        except Exception:
            pass

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
            [InlineKeyboardButton(text="🗑 Корзина", callback_data="sniper_trash_bin")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        await callback.message.edit_text(
            f"🗑 <b>Фильтр перемещён в корзину</b>\n\n"
            f"Фильтр «{filter_data['name']}» перемещён в корзину.\n"
            f"Вы можете восстановить его в любое время.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {str(e)}")


@router.callback_query(F.data == "confirm_delete_all_filters")
async def confirm_delete_all_filters(callback: CallbackQuery):
    """Запрос подтверждения удаления всех фильтров."""
    await callback.answer()

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        # Получаем количество фильтров
        filters = await db.get_active_filters(user['id'])
        filters_count = len(filters)

        if filters_count == 0:
            await callback.message.edit_text(
                "📋 <b>У вас нет фильтров для удаления</b>",
                parse_mode="HTML"
            )
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить все", callback_data="delete_all_filters_confirmed")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="sniper_my_filters")]
        ])

        await callback.message.edit_text(
            f"⚠️ <b>Удалить все фильтры?</b>\n\n"
            f"Все {filters_count} фильтр(ов) будут перемещены в корзину.\n"
            f"Вы сможете восстановить их позже.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {str(e)}")


@router.callback_query(F.data == "delete_all_filters_confirmed")
async def delete_all_filters_confirmed(callback: CallbackQuery):
    """Удалить все фильтры пользователя."""
    await callback.answer()

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        # Получаем все фильтры пользователя
        filters = await db.get_user_filters(user['id'], active_only=False)

        if not filters:
            await callback.message.edit_text(
                "📋 <b>У вас нет фильтров для удаления</b>",
                parse_mode="HTML"
            )
            return

        # Удаляем все фильтры
        deleted_count = 0
        for filter_data in filters:
            try:
                await db.delete_filter(filter_data['id'])
                deleted_count += 1
            except Exception as e:
                logger.error(f"Ошибка при удалении фильтра {filter_data['id']}: {e}")

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать новый фильтр", callback_data="sniper_new_search")],
            [InlineKeyboardButton(text="🗑 Корзина", callback_data="sniper_trash_bin")],
            [InlineKeyboardButton(text="🎯 Меню Sniper", callback_data="sniper_menu")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        await callback.message.edit_text(
            f"🗑 <b>Фильтры перемещены в корзину</b>\n\n"
            f"Перемещено фильтров: {deleted_count}\n\n"
            f"Вы можете восстановить их из корзины или создать новые.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при удалении фильтров: {str(e)}")


# ============================================
# КОРЗИНА (TRASH BIN)
# ============================================

@router.callback_query(F.data == "sniper_trash_bin")
async def show_trash_bin(callback: CallbackQuery):
    """Показать список удалённых фильтров (корзина)."""
    await callback.answer()

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        deleted_filters = await db.get_deleted_filters(user['id'])

        if not deleted_filters:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])

            await callback.message.edit_text(
                "🗑 <b>Корзина пуста</b>\n\n"
                "Удалённых фильтров нет.",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            return

        text = "🗑 <b>Корзина</b>\n\n"
        keyboard_buttons = []

        for i, f in enumerate(deleted_filters, 1):
            deleted_at = f.get('deleted_at', '')
            if deleted_at:
                try:
                    dt = datetime.fromisoformat(deleted_at)
                    date_str = dt.strftime("%d.%m.%Y %H:%M")
                except (ValueError, TypeError):
                    date_str = "—"
            else:
                date_str = "—"

            keywords = f.get('keywords', [])
            text += (
                f"{i}. <b>{f['name']}</b>\n"
                f"   🔑 {', '.join(keywords[:3]) if keywords else '—'}\n"
                f"   🕐 Удалён: {date_str}\n\n"
            )

            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"🗑 {f['name'][:20]}",
                    callback_data=f"trash_filter_{f['id']}"
                )
            ])

        keyboard_buttons.append([
            InlineKeyboardButton(text="🧹 Очистить корзину", callback_data="confirm_empty_trash")
        ])
        keyboard_buttons.append([
            InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")
        ])
        keyboard_buttons.append([
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка в show_trash_bin: {e}", exc_info=True)
        await callback.message.answer(f"❌ Ошибка: {str(e)[:200]}")


@router.callback_query(F.data.startswith("trash_filter_"))
async def show_trash_filter_detail(callback: CallbackQuery):
    """Детали удалённого фильтра с кнопками восстановления/удаления."""
    await callback.answer()

    try:
        filter_id = int(callback.data.replace("trash_filter_", ""))

        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data or not filter_data.get('deleted_at'):
            await callback.message.answer("❌ Фильтр не найден в корзине")
            return

        keywords = filter_data.get('keywords', [])
        deleted_at = filter_data.get('deleted_at', '')
        if deleted_at:
            try:
                dt = datetime.fromisoformat(deleted_at)
                date_str = dt.strftime("%d.%m.%Y %H:%M")
            except (ValueError, TypeError):
                date_str = "—"
        else:
            date_str = "—"

        price_range = ""
        if filter_data.get('price_min') or filter_data.get('price_max'):
            price_min = f"{filter_data['price_min']:,}" if filter_data.get('price_min') else "0"
            price_max = f"{filter_data['price_max']:,}" if filter_data.get('price_max') else "∞"
            price_range = f"\n💰 Цена: {price_min} - {price_max} ₽"

        regions = filter_data.get('regions', [])
        regions_str = f"\n📍 Регионы: {', '.join(regions[:3])}" if regions else ""

        text = (
            f"🗑 <b>Удалённый фильтр</b>\n\n"
            f"📌 <b>{filter_data['name']}</b>\n"
            f"🔑 Ключевые слова: {', '.join(keywords[:5]) if keywords else '—'}"
            f"{price_range}{regions_str}\n"
            f"🕐 Удалён: {date_str}"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="♻️ Восстановить", callback_data=f"restore_filter_{filter_id}")],
            [InlineKeyboardButton(text="❌ Удалить навсегда", callback_data=f"perm_delete_filter_{filter_id}")],
            [InlineKeyboardButton(text="« Назад в корзину", callback_data="sniper_trash_bin")]
        ])

        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка в show_trash_filter_detail: {e}", exc_info=True)
        await callback.message.answer(f"❌ Ошибка: {str(e)[:200]}")


@router.callback_query(F.data.startswith("restore_filter_"))
async def restore_filter(callback: CallbackQuery):
    """Восстановить фильтр из корзины."""
    await callback.answer()

    try:
        filter_id = int(callback.data.replace("restore_filter_", ""))

        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        # Проверяем лимит фильтров
        active_filters = await db.get_user_filters(user['id'], active_only=True)
        tier = user['subscription_tier']
        max_filters = 3 if tier == 'trial' else (5 if tier == 'basic' else 20)

        if len(active_filters) >= max_filters:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
                [InlineKeyboardButton(text="« Назад в корзину", callback_data="sniper_trash_bin")]
            ])

            await callback.message.edit_text(
                f"⚠️ <b>Лимит фильтров достигнут</b>\n\n"
                f"Ваш тариф <b>{tier.title()}</b> позволяет максимум {max_filters} фильтров.\n"
                f"Удалите один из активных фильтров перед восстановлением.",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            return

        filter_data = await db.get_filter_by_id(filter_id)
        if not filter_data:
            await callback.message.answer("❌ Фильтр не найден")
            return

        await db.restore_filter(filter_id)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
            [InlineKeyboardButton(text="🗑 Корзина", callback_data="sniper_trash_bin")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        await callback.message.edit_text(
            f"♻️ <b>Фильтр восстановлен</b>\n\n"
            f"Фильтр «{filter_data['name']}» снова активен и будет участвовать в мониторинге.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка восстановления фильтра: {e}", exc_info=True)
        await callback.message.answer(f"❌ Ошибка: {str(e)[:200]}")


@router.callback_query(F.data.startswith("perm_delete_filter_"))
async def perm_delete_filter(callback: CallbackQuery):
    """Подтверждение безвозвратного удаления фильтра."""
    await callback.answer()

    try:
        filter_id = int(callback.data.replace("perm_delete_filter_", ""))

        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await callback.message.answer("❌ Фильтр не найден")
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Да, удалить навсегда", callback_data=f"confirm_perm_delete_{filter_id}")],
            [InlineKeyboardButton(text="« Отмена", callback_data=f"trash_filter_{filter_id}")]
        ])

        await callback.message.edit_text(
            f"⚠️ <b>Безвозвратное удаление</b>\n\n"
            f"Фильтр «{filter_data['name']}» будет удалён навсегда.\n\n"
            f"<i>Это действие нельзя отменить!</i>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {str(e)[:200]}")


@router.callback_query(F.data.startswith("confirm_perm_delete_"))
async def confirm_perm_delete(callback: CallbackQuery):
    """Безвозвратно удалить фильтр."""
    await callback.answer()

    try:
        filter_id = int(callback.data.replace("confirm_perm_delete_", ""))

        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)
        filter_name = filter_data['name'] if filter_data else "Неизвестный"

        await db.permanently_delete_filter(filter_id)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Корзина", callback_data="sniper_trash_bin")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        await callback.message.edit_text(
            f"✅ <b>Фильтр удалён навсегда</b>\n\n"
            f"Фильтр «{filter_name}» безвозвратно удалён.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка при безвозвратном удалении: {e}", exc_info=True)
        await callback.message.answer(f"❌ Ошибка: {str(e)[:200]}")


@router.callback_query(F.data == "confirm_empty_trash")
async def confirm_empty_trash(callback: CallbackQuery):
    """Подтверждение очистки корзины."""
    await callback.answer()

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        deleted_filters = await db.get_deleted_filters(user['id'])
        count = len(deleted_filters)

        if count == 0:
            await callback.message.edit_text(
                "🗑 <b>Корзина уже пуста</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")]
                ])
            )
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Да, очистить", callback_data="empty_trash_confirmed")],
            [InlineKeyboardButton(text="« Отмена", callback_data="sniper_trash_bin")]
        ])

        await callback.message.edit_text(
            f"⚠️ <b>Очистить корзину?</b>\n\n"
            f"Будет безвозвратно удалено фильтров: {count}\n\n"
            f"<i>Это действие нельзя отменить!</i>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {str(e)[:200]}")


@router.callback_query(F.data == "empty_trash_confirmed")
async def empty_trash_confirmed(callback: CallbackQuery):
    """Очистить всю корзину."""
    await callback.answer()

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        deleted_count = await db.permanently_delete_all_deleted_filters(user['id'])

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        await callback.message.edit_text(
            f"✅ <b>Корзина очищена</b>\n\n"
            f"Безвозвратно удалено фильтров: {deleted_count}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка очистки корзины: {e}", exc_info=True)
        await callback.message.answer(f"❌ Ошибка: {str(e)[:200]}")


# ============================================
# ОБРАТНАЯ СВЯЗЬ ПО ТЕНДЕРАМ
# ============================================

@router.callback_query(F.data.startswith("interested_"))
async def mark_tender_interesting(callback: CallbackQuery):
    """Пользователь отметил тендер как интересный."""
    await callback.answer("👍 Отмечено как интересное")

    try:
        tender_number = callback.data.replace("interested_", "")

        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if user:
            # Получаем контекст из уведомления
            notification = await db.get_notification_by_tender(user['id'], tender_number)
            await db.save_user_feedback(
                user_id=user['id'],
                tender_number=tender_number,
                feedback_type='interesting',
                filter_id=notification.get('filter_id') if notification else None,
                tender_name=notification.get('tender_name', '') if notification else '',
                matched_keywords=notification.get('matched_keywords', []) if notification else [],
                original_score=notification.get('score') if notification else None,
            )

        logger.info(f"Пользователь {callback.from_user.id} отметил тендер {tender_number} как интересный")

        # Строим кнопки после отметки: подтверждение + доступные действия
        post_buttons = [[InlineKeyboardButton(text="✅ Отмечено как интересное", callback_data="noop")]]
        if notification:
            tender_url = notification.get('tender_url', '')
            row = []
            if tender_url:
                row.append(InlineKeyboardButton(text="🔗 Открыть на сайте", url=tender_url))
            row.append(InlineKeyboardButton(
                text="📊 В таблицу",
                callback_data=safe_callback_data("sheets", tender_number)
            ))
            if row:
                post_buttons.append(row)

        # Автогенерация документов если профиль заполнен
        if user:
            profile = await db.get_company_profile(user['id'])
            if profile and profile.get('is_complete'):
                post_buttons.append([InlineKeyboardButton(
                    text="📄 Генерируем документы...",
                    callback_data="noop"
                )])
                # Запуск генерации в фоне
                asyncio.create_task(
                    _generate_and_send_docs(callback, tender_number, user, profile, notification)
                )
            else:
                post_buttons.append([InlineKeyboardButton(
                    text="📄 Заполните профиль для автогенерации",
                    callback_data="company_profile"
                )])

        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=post_buttons)
        )

    except Exception as e:
        logger.error(f"Ошибка при отметке тендера: {e}", exc_info=True)


async def _generate_and_send_docs(
    callback: CallbackQuery,
    tender_number: str,
    user: dict,
    profile: dict,
    notification: dict | None,
):
    """Фоновая генерация и отправка документов в Telegram."""
    try:
        from tender_sniper.document_generator import DocumentGenerator
        from tender_sniper.document_generator.ai_proposal import AIProposalGenerator
        from aiogram.types import BufferedInputFile

        # Собираем данные тендера
        tender_data = {
            'number': tender_number,
            'name': notification.get('tender_name', '') if notification else '',
            'price': notification.get('tender_price') if notification else None,
            'url': notification.get('tender_url', '') if notification else '',
            'customer_name': notification.get('tender_customer', '') if notification else '',
            'region': notification.get('tender_region', '') if notification else '',
            'submission_deadline': notification.get('submission_deadline', '') if notification else '',
        }

        # Генерируем AI-текст техпредложения
        ai_gen = AIProposalGenerator()
        ai_text = await ai_gen.generate_proposal_text(
            tender_name=tender_data['name'],
            company_profile=profile,
        )

        # Генерируем пакет документов
        generator = DocumentGenerator()
        documents = await generator.generate_package(
            tender_data=tender_data,
            company_profile=profile,
            user_id=user['id'],
            ai_proposal_text=ai_text,
        )

        if not documents:
            await callback.message.answer("⚠️ Не удалось сгенерировать документы.")
            return

        # Сохраняем записи в БД и отправляем
        db = await get_sniper_db()
        await callback.message.answer(
            f"📄 <b>Документы для тендера {tender_number}:</b>",
            parse_mode="HTML"
        )

        for doc_type, filename, doc_bytes in documents:
            try:
                # Сохраняем в БД
                await db.save_generated_document(
                    user_id=user['id'],
                    tender_number=tender_number,
                    doc_type=doc_type,
                    doc_name=filename,
                    status='ready',
                    ai_content=ai_text if doc_type == 'proposal' else None,
                )

                # Отправляем файл
                input_file = BufferedInputFile(doc_bytes.read(), filename=filename)
                await callback.message.answer_document(
                    document=input_file,
                    caption=f"📄 {filename}",
                )
            except Exception as e:
                logger.error(f"Error sending document {doc_type}: {e}", exc_info=True)

        await callback.message.answer(
            "✅ Все документы сгенерированы и отправлены!\n\n"
            "⚠️ Проверьте документы перед подачей на площадке.",
        )

    except Exception as e:
        logger.error(f"Error in document generation for {tender_number}: {e}", exc_info=True)
        try:
            await callback.message.answer(
                f"⚠️ Ошибка при генерации документов: {str(e)[:200]}"
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("skip_"))
async def mark_tender_skipped(callback: CallbackQuery):
    """Пользователь пропустил тендер - сохраняем для обучения."""
    await callback.answer("👎 Пропущено")

    try:
        tender_number = callback.data.replace("skip_", "")

        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if user:
            # Получаем контекст из уведомления
            notification = await db.get_notification_by_tender(user['id'], tender_number)
            tender_name = notification.get('tender_name', '') if notification else ''

            # Сохраняем в hidden_tenders (как раньше)
            await db.save_hidden_tender(
                user_id=user['id'],
                tender_number=tender_number,
                tender_name=tender_name,
                reason='skipped'
            )

            # Сохраняем в user_feedback для аналитики
            await db.save_user_feedback(
                user_id=user['id'],
                tender_number=tender_number,
                feedback_type='hidden',
                filter_id=notification.get('filter_id') if notification else None,
                tender_name=tender_name,
                matched_keywords=notification.get('matched_keywords', []) if notification else [],
                original_score=notification.get('score') if notification else None,
            )

            logger.info(f"Пользователь {callback.from_user.id} пропустил тендер {tender_number}: {tender_name[:50]}...")

        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="❌ Пропущено", callback_data="noop"),
                    InlineKeyboardButton(
                        text="↩️ Отменить",
                        callback_data=safe_callback_data("undo_skip", tender_number)
                    )
                ]
            ])
        )

    except Exception as e:
        logger.error(f"Ошибка при пропуске тендера: {e}", exc_info=True)


@router.callback_query(F.data.startswith("undo_skip_"))
async def undo_skip_tender(callback: CallbackQuery):
    """Пользователь отменяет пропуск — убираем из скрытых и восстанавливаем кнопки."""
    await callback.answer("↩️ Пропуск отменён")
    try:
        tender_number = callback.data.replace("undo_skip_", "")
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if user:
            await db.unhide_tender(user['id'], tender_number)
            logger.info(f"Пользователь {callback.from_user.id} отменил пропуск тендера {tender_number}")

        # Восстанавливаем кнопки — берём URL из уведомления
        notification = await db.get_notification_by_tender(user['id'], tender_number) if user else None
        tender_url = notification.get('tender_url', '') if notification else ''

        restored_buttons = []
        if tender_url:
            restored_buttons.append([InlineKeyboardButton(text="📄 Открыть на zakupki.gov.ru", url=tender_url)])
        restored_buttons.append([
            InlineKeyboardButton(text="✅ Интересно", callback_data=safe_callback_data("interested", tender_number)),
            InlineKeyboardButton(text="📊 В таблицу", callback_data=safe_callback_data("sheets", tender_number)),
            InlineKeyboardButton(text="❌ Пропустить", callback_data=safe_callback_data("skip", tender_number)),
        ])

        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=restored_buttons)
        )
    except Exception as e:
        logger.error(f"Ошибка undo_skip: {e}", exc_info=True)


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    """Handler для отключенных/информационных кнопок."""
    await callback.answer("✅ Уже отмечено")


# ============================================
# 🧪 БЕТА: РАСШИРЕННЫЕ НАСТРОЙКИ ФИЛЬТРОВ
# ============================================

@router.callback_query(F.data == "sniper_extended_settings")
async def show_extended_settings(callback: CallbackQuery):
    """Меню расширенных настроек фильтров (БЕТА)."""
    # Проверяем доступ к расширенным настройкам (только Premium)
    if not await require_feature(callback, 'extended_settings'):
        return

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.message.edit_text(
                "❌ Пользователь не найден. Попробуйте /start",
                parse_mode="HTML"
            )
            return

        # Получаем фильтры пользователя
        filters = await db.get_user_filters(user['id'], active_only=False)

        keyboard_buttons = []

        if filters:
            # Показываем кнопки для каждого фильтра
            for f in filters[:10]:  # Максимум 10 фильтров
                status = "🟢" if f['is_active'] else "🔴"
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"{status} {f['name'][:30]}",
                        callback_data=f"ext_filter_{f['id']}"
                    )
                ])

        # Кнопки навигации
        keyboard_buttons.extend([
            [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

        features_text = (
            "🎛 <b>НАСТРОЙКИ ФИЛЬТРОВ</b> 🧪 БЕТА\n\n"
            "Тонкая настройка ваших фильтров:\n\n"
            "━━━ <b>ДОСТУПНЫЕ ФУНКЦИИ</b> ━━━\n\n"
            "🔢 <b>Номер закупки</b> — поиск конкретного тендера\n"
            "🏢 <b>ИНН заказчика</b> — отслеживание конкретных организаций\n"
            "🚫 <b>Чёрный список</b> — исключение нежелательных заказчиков\n"
            "📅 <b>Дата публикации</b> — фильтр по свежести\n"
            "⭐ <b>Приоритет слов</b> — важные ключевые слова выше\n\n"
        )

        if filters:
            features_text += f"📋 <b>Ваши фильтры ({len(filters)}):</b>\n"
            features_text += "Выберите фильтр для настройки:"
        else:
            features_text += "📋 <i>У вас нет фильтров.</i>\n"
            features_text += "Сначала создайте фильтр через \"Новый поиск\"."

        await callback.message.edit_text(
            features_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка в show_extended_settings: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("ext_filter_"))
async def show_filter_extended_options(callback: CallbackQuery):
    """Показать расширенные опции для конкретного фильтра."""
    await callback.answer()

    try:
        filter_id = int(callback.data.replace("ext_filter_", ""))

        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await callback.message.edit_text(
                "❌ Фильтр не найден",
                parse_mode="HTML"
            )
            return

        # Формируем информацию о текущих настройках
        settings_info = f"⚙️ <b>Настройки фильтра:</b> {filter_data['name']}\n\n"

        # Номер закупки
        purchase_num = filter_data.get('purchase_number')
        settings_info += f"🔢 <b>Номер закупки:</b> {purchase_num or '—'}\n"

        # ИНН заказчиков
        customer_inns = filter_data.get('customer_inn', [])
        if customer_inns:
            settings_info += f"🏢 <b>ИНН заказчиков:</b> {', '.join(customer_inns[:3])}"
            if len(customer_inns) > 3:
                settings_info += f" (+{len(customer_inns)-3})"
            settings_info += "\n"
        else:
            settings_info += "🏢 <b>ИНН заказчиков:</b> —\n"

        # Черный список
        excluded_inns = filter_data.get('excluded_customer_inns', [])
        excluded_keywords = filter_data.get('excluded_customer_keywords', [])
        blacklist_count = len(excluded_inns) + len(excluded_keywords)
        settings_info += f"🚫 <b>Черный список:</b> {blacklist_count} записей\n"

        # Дата публикации
        pub_days = filter_data.get('publication_days')
        if pub_days:
            settings_info += f"📅 <b>Публикация:</b> за {pub_days} дней\n"
        else:
            settings_info += "📅 <b>Публикация:</b> без ограничений\n"

        # Приоритетные ключевые слова
        primary_kw = filter_data.get('primary_keywords', [])
        secondary_kw = filter_data.get('secondary_keywords', [])
        if primary_kw or secondary_kw:
            settings_info += f"⭐ <b>Приоритет:</b> {len(primary_kw)} главных, {len(secondary_kw)} доп.\n"
        else:
            settings_info += "⭐ <b>Приоритет:</b> не настроен\n"

        notify_ids = filter_data.get('notify_chat_ids') or []
        if notify_ids:
            settings_info += f"📱 <b>Уведомления:</b> {len(notify_ids)} адресатов\n"
        else:
            settings_info += "📱 <b>Уведомления:</b> только в личку\n"

        settings_info += "\n<i>Выберите параметр для настройки:</i>"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔢 Номер закупки", callback_data=f"ext_pnum_{filter_id}")],
            [InlineKeyboardButton(text="🏢 ИНН заказчиков", callback_data=f"ext_inn_{filter_id}")],
            [InlineKeyboardButton(text="🚫 Черный список", callback_data=f"ext_blacklist_{filter_id}")],
            [InlineKeyboardButton(text="📅 Дата публикации", callback_data=f"ext_pubdate_{filter_id}")],
            [InlineKeyboardButton(text="⭐ Приоритет ключевых слов", callback_data=f"ext_priority_{filter_id}")],
            [InlineKeyboardButton(text="📱 Куда уведомлять", callback_data=f"ext_notify_{filter_id}")],
            [InlineKeyboardButton(text="« Назад к списку", callback_data="sniper_extended_settings")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        await callback.message.edit_text(
            settings_info,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка в show_filter_extended_options: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("ext_pubdate_"))
async def show_publication_date_options(callback: CallbackQuery):
    """Выбор фильтра по дате публикации."""
    await callback.answer()

    try:
        filter_id = int(callback.data.replace("ext_pubdate_", ""))

        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await callback.message.edit_text("❌ Фильтр не найден")
            return

        current_days = filter_data.get('publication_days')

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'✅ ' if current_days == 3 else ''}3 дня",
                    callback_data=f"set_pubdays_{filter_id}_3"
                ),
                InlineKeyboardButton(
                    text=f"{'✅ ' if current_days == 7 else ''}7 дней",
                    callback_data=f"set_pubdays_{filter_id}_7"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{'✅ ' if current_days == 14 else ''}14 дней",
                    callback_data=f"set_pubdays_{filter_id}_14"
                ),
                InlineKeyboardButton(
                    text=f"{'✅ ' if current_days == 30 else ''}30 дней",
                    callback_data=f"set_pubdays_{filter_id}_30"
                )
            ],
            [InlineKeyboardButton(
                text=f"{'✅ ' if current_days is None else ''}Без ограничений",
                callback_data=f"set_pubdays_{filter_id}_0"
            )],
            [InlineKeyboardButton(text="« Назад", callback_data=f"ext_filter_{filter_id}")]
        ])

        await callback.message.edit_text(
            f"📅 <b>Фильтр по дате публикации</b> 🧪 БЕТА\n\n"
            f"Фильтр: <b>{filter_data['name']}</b>\n\n"
            f"Выберите, за сколько дней искать тендеры:\n\n"
            f"<i>Текущее значение: {f'{current_days} дней' if current_days else 'без ограничений'}</i>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка в show_publication_date_options: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("set_pubdays_"))
async def set_publication_days(callback: CallbackQuery):
    """Установить фильтр по дате публикации."""
    try:
        parts = callback.data.split("_")
        filter_id = int(parts[2])
        days = int(parts[3])

        db = await get_sniper_db()

        # Устанавливаем значение (0 означает None)
        pub_days = days if days > 0 else None
        await db.update_filter(filter_id, publication_days=pub_days)

        await callback.answer(
            f"✅ Установлено: {f'{days} дней' if days > 0 else 'без ограничений'}",
            show_alert=True
        )

        # Возвращаемся к настройкам фильтра
        settings_text, keyboard = await build_filter_extended_options_view(filter_id, db)
        if settings_text:
            await callback.message.edit_text(settings_text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await callback.message.edit_text("❌ Фильтр не найден", parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка в set_publication_days: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


# --- Номер закупки ---

@router.callback_query(F.data.startswith("ext_pnum_"))
async def show_purchase_number_input(callback: CallbackQuery, state: FSMContext):
    """Показать форму ввода номера закупки."""
    await callback.answer()

    try:
        filter_id = int(callback.data.replace("ext_pnum_", ""))

        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await callback.message.edit_text("❌ Фильтр не найден")
            return

        current_num = filter_data.get('purchase_number') or "не указан"

        # Сохраняем filter_id в состоянии и устанавливаем состояние ожидания ввода
        await state.update_data(ext_filter_id=filter_id, ext_setting='purchase_number')
        await state.set_state(ExtendedSettingsStates.waiting_for_input)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Очистить", callback_data=f"clear_pnum_{filter_id}")],
            [InlineKeyboardButton(text="« Отмена", callback_data=f"ext_cancel_{filter_id}")]
        ])

        await callback.message.edit_text(
            f"🔢 <b>Номер закупки</b> 🧪 БЕТА\n\n"
            f"Фильтр: <b>{filter_data['name']}</b>\n"
            f"Текущее значение: <code>{current_num}</code>\n\n"
            f"Введите номер закупки для поиска:\n"
            f"<i>Например: 0123456789012345</i>\n\n"
            f"💡 Это позволит искать конкретную закупку по её номеру.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка в show_purchase_number_input: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("clear_pnum_"))
async def clear_purchase_number(callback: CallbackQuery):
    """Очистить номер закупки."""
    try:
        filter_id = int(callback.data.replace("clear_pnum_", ""))

        db = await get_sniper_db()
        await db.update_filter(filter_id, purchase_number=None)

        await callback.answer("✅ Номер закупки очищен", show_alert=True)

        # Возвращаемся к настройкам фильтра
        settings_text, keyboard = await build_filter_extended_options_view(filter_id, db)
        if settings_text:
            await callback.message.edit_text(settings_text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await callback.message.edit_text("❌ Фильтр не найден", parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка в clear_purchase_number: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


# --- ИНН заказчиков ---

@router.callback_query(F.data.startswith("ext_inn_"))
async def show_customer_inn_input(callback: CallbackQuery, state: FSMContext):
    """Показать форму ввода ИНН заказчиков."""
    await callback.answer()

    try:
        filter_id = int(callback.data.replace("ext_inn_", ""))

        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await callback.message.edit_text("❌ Фильтр не найден")
            return

        current_inns = filter_data.get('customer_inn', [])
        inns_text = ", ".join(current_inns) if current_inns else "не указаны"

        await state.update_data(ext_filter_id=filter_id, ext_setting='customer_inn')
        await state.set_state(ExtendedSettingsStates.waiting_for_input)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Очистить все", callback_data=f"clear_inn_{filter_id}")],
            [InlineKeyboardButton(text="« Отмена", callback_data=f"ext_cancel_{filter_id}")]
        ])

        await callback.message.edit_text(
            f"🏢 <b>ИНН заказчиков</b> 🧪 БЕТА\n\n"
            f"Фильтр: <b>{filter_data['name']}</b>\n"
            f"Текущие ИНН: <code>{inns_text}</code>\n\n"
            f"Введите ИНН заказчиков через запятую:\n"
            f"<i>Например: 7707083893, 7710140679</i>\n\n"
            f"💡 ИНН должен содержать 10 или 12 цифр.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка в show_customer_inn_input: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("clear_inn_"))
async def clear_customer_inn(callback: CallbackQuery):
    """Очистить ИНН заказчиков."""
    try:
        filter_id = int(callback.data.replace("clear_inn_", ""))

        db = await get_sniper_db()
        await db.update_filter(filter_id, customer_inn=[])

        await callback.answer("✅ ИНН заказчиков очищены", show_alert=True)

        # Возвращаемся к настройкам фильтра
        settings_text, keyboard = await build_filter_extended_options_view(filter_id, db)
        if settings_text:
            await callback.message.edit_text(settings_text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await callback.message.edit_text("❌ Фильтр не найден", parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка в clear_customer_inn: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


# --- Черный список ---

@router.callback_query(F.data.startswith("ext_blacklist_"))
async def show_blacklist_menu(callback: CallbackQuery):
    """Показать меню черного списка."""
    await callback.answer()

    try:
        filter_id = int(callback.data.replace("ext_blacklist_", ""))

        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await callback.message.edit_text("❌ Фильтр не найден")
            return

        excluded_inns = filter_data.get('excluded_customer_inns', [])
        excluded_keywords = filter_data.get('excluded_customer_keywords', [])

        inns_text = ", ".join(excluded_inns[:5]) if excluded_inns else "—"
        if len(excluded_inns) > 5:
            inns_text += f" (+{len(excluded_inns)-5})"

        keywords_text = ", ".join(excluded_keywords[:5]) if excluded_keywords else "—"
        if len(excluded_keywords) > 5:
            keywords_text += f" (+{len(excluded_keywords)-5})"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏢 Добавить ИНН", callback_data=f"bl_add_inn_{filter_id}")],
            [InlineKeyboardButton(text="📝 Добавить ключевые слова", callback_data=f"bl_add_kw_{filter_id}")],
            [InlineKeyboardButton(text="🗑️ Очистить черный список", callback_data=f"bl_clear_{filter_id}")],
            [InlineKeyboardButton(text="« Назад", callback_data=f"ext_filter_{filter_id}")]
        ])

        await callback.message.edit_text(
            f"🚫 <b>Черный список заказчиков</b> 🧪 БЕТА\n\n"
            f"Фильтр: <b>{filter_data['name']}</b>\n\n"
            f"<b>Исключенные ИНН ({len(excluded_inns)}):</b>\n"
            f"<code>{inns_text}</code>\n\n"
            f"<b>Исключенные слова ({len(excluded_keywords)}):</b>\n"
            f"<code>{keywords_text}</code>\n\n"
            f"💡 Заказчики из черного списка будут исключены из результатов поиска.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка в show_blacklist_menu: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("bl_add_inn_"))
async def show_blacklist_inn_input(callback: CallbackQuery, state: FSMContext):
    """Показать форму добавления ИНН в черный список."""
    await callback.answer()

    try:
        filter_id = int(callback.data.replace("bl_add_inn_", ""))
        await state.update_data(ext_filter_id=filter_id, ext_setting='excluded_customer_inns')
        await state.set_state(ExtendedSettingsStates.waiting_for_input)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Отмена", callback_data=f"ext_cancel_{filter_id}")]
        ])

        await callback.message.edit_text(
            f"🚫 <b>Добавить ИНН в черный список</b>\n\n"
            f"Введите ИНН заказчиков через запятую:\n"
            f"<i>Например: 7707083893, 7710140679</i>\n\n"
            f"Эти заказчики будут исключены из результатов поиска.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("bl_add_kw_"))
async def show_blacklist_keywords_input(callback: CallbackQuery, state: FSMContext):
    """Показать форму добавления ключевых слов в черный список."""
    await callback.answer()

    try:
        filter_id = int(callback.data.replace("bl_add_kw_", ""))
        await state.update_data(ext_filter_id=filter_id, ext_setting='excluded_customer_keywords')
        await state.set_state(ExtendedSettingsStates.waiting_for_input)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Отмена", callback_data=f"ext_cancel_{filter_id}")]
        ])

        await callback.message.edit_text(
            f"🚫 <b>Добавить слова в черный список</b>\n\n"
            f"Введите ключевые слова через запятую:\n"
            f"<i>Например: Газпром, РЖД, Сбербанк</i>\n\n"
            f"Заказчики, содержащие эти слова в названии, будут исключены.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("bl_clear_"))
async def clear_blacklist(callback: CallbackQuery):
    """Очистить черный список."""
    try:
        filter_id = int(callback.data.replace("bl_clear_", ""))

        db = await get_sniper_db()
        await db.update_filter(filter_id, excluded_customer_inns=[], excluded_customer_keywords=[])

        await callback.answer("✅ Черный список очищен", show_alert=True)

        # Показываем обновленное меню черного списка
        filter_data = await db.get_filter_by_id(filter_id)
        if not filter_data:
            await callback.message.edit_text("❌ Фильтр не найден", parse_mode="HTML")
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏢 Добавить ИНН", callback_data=f"bl_add_inn_{filter_id}")],
            [InlineKeyboardButton(text="📝 Добавить ключевые слова", callback_data=f"bl_add_kw_{filter_id}")],
            [InlineKeyboardButton(text="🗑️ Очистить черный список", callback_data=f"bl_clear_{filter_id}")],
            [InlineKeyboardButton(text="« Назад", callback_data=f"ext_filter_{filter_id}")]
        ])

        await callback.message.edit_text(
            f"🚫 <b>Черный список заказчиков</b> 🧪 БЕТА\n\n"
            f"Фильтр: <b>{filter_data['name']}</b>\n\n"
            f"<b>Исключенные ИНН (0):</b>\n<code>—</code>\n\n"
            f"<b>Исключенные слова (0):</b>\n<code>—</code>\n\n"
            f"💡 Заказчики из черного списка будут исключены из результатов поиска.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка в clear_blacklist: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


# --- Приоритет ключевых слов ---

@router.callback_query(F.data.startswith("ext_priority_"))
async def show_priority_keywords_menu(callback: CallbackQuery, state: FSMContext):
    """Показать меню приоритета ключевых слов."""
    await callback.answer()

    try:
        filter_id = int(callback.data.replace("ext_priority_", ""))

        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await callback.message.edit_text("❌ Фильтр не найден")
            return

        primary_kw = filter_data.get('primary_keywords', [])
        secondary_kw = filter_data.get('secondary_keywords', [])

        primary_text = ", ".join(primary_kw[:5]) if primary_kw else "—"
        if len(primary_kw) > 5:
            primary_text += f" (+{len(primary_kw)-5})"

        secondary_text = ", ".join(secondary_kw[:5]) if secondary_kw else "—"
        if len(secondary_kw) > 5:
            secondary_text += f" (+{len(secondary_kw)-5})"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Главные (вес 2x)", callback_data=f"prio_primary_{filter_id}")],
            [InlineKeyboardButton(text="📌 Дополнительные (вес 1x)", callback_data=f"prio_secondary_{filter_id}")],
            [InlineKeyboardButton(text="🗑️ Очистить приоритеты", callback_data=f"prio_clear_{filter_id}")],
            [InlineKeyboardButton(text="« Назад", callback_data=f"ext_filter_{filter_id}")]
        ])

        await callback.message.edit_text(
            f"⭐ <b>Приоритет ключевых слов</b> 🧪 БЕТА\n\n"
            f"Фильтр: <b>{filter_data['name']}</b>\n\n"
            f"<b>Главные слова (вес 2x):</b>\n"
            f"<code>{primary_text}</code>\n\n"
            f"<b>Дополнительные (вес 1x):</b>\n"
            f"<code>{secondary_text}</code>\n\n"
            f"💡 Главные слова имеют приоритет при ранжировании результатов.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка в show_priority_keywords_menu: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("prio_primary_"))
async def show_primary_keywords_input(callback: CallbackQuery, state: FSMContext):
    """Показать форму ввода главных ключевых слов."""
    await callback.answer()

    try:
        filter_id = int(callback.data.replace("prio_primary_", ""))
        await state.update_data(ext_filter_id=filter_id, ext_setting='primary_keywords')
        await state.set_state(ExtendedSettingsStates.waiting_for_input)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Отмена", callback_data=f"ext_cancel_{filter_id}")]
        ])

        await callback.message.edit_text(
            f"⭐ <b>Главные ключевые слова</b>\n\n"
            f"Введите главные ключевые слова через запятую:\n"
            f"<i>Например: сервер, компьютер, ноутбук</i>\n\n"
            f"💡 Эти слова будут иметь повышенный вес (2x) при поиске.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("prio_secondary_"))
async def show_secondary_keywords_input(callback: CallbackQuery, state: FSMContext):
    """Показать форму ввода дополнительных ключевых слов."""
    await callback.answer()

    try:
        filter_id = int(callback.data.replace("prio_secondary_", ""))
        await state.update_data(ext_filter_id=filter_id, ext_setting='secondary_keywords')
        await state.set_state(ExtendedSettingsStates.waiting_for_input)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Отмена", callback_data=f"ext_cancel_{filter_id}")]
        ])

        await callback.message.edit_text(
            f"📌 <b>Дополнительные ключевые слова</b>\n\n"
            f"Введите дополнительные ключевые слова через запятую:\n"
            f"<i>Например: монитор, клавиатура, мышь</i>\n\n"
            f"💡 Эти слова будут иметь обычный вес (1x) при поиске.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("prio_clear_"))
async def clear_priority_keywords(callback: CallbackQuery):
    """Очистить приоритеты ключевых слов."""
    try:
        filter_id = int(callback.data.replace("prio_clear_", ""))

        db = await get_sniper_db()
        await db.update_filter(filter_id, primary_keywords=[], secondary_keywords=[])

        await callback.answer("✅ Приоритеты очищены", show_alert=True)

        # Показываем обновленное меню приоритетов
        filter_data = await db.get_filter_by_id(filter_id)
        if not filter_data:
            await callback.message.edit_text("❌ Фильтр не найден", parse_mode="HTML")
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Главные (вес 2x)", callback_data=f"prio_primary_{filter_id}")],
            [InlineKeyboardButton(text="📌 Дополнительные (вес 1x)", callback_data=f"prio_secondary_{filter_id}")],
            [InlineKeyboardButton(text="🗑️ Очистить приоритеты", callback_data=f"prio_clear_{filter_id}")],
            [InlineKeyboardButton(text="« Назад", callback_data=f"ext_filter_{filter_id}")]
        ])

        await callback.message.edit_text(
            f"⭐ <b>Приоритет ключевых слов</b> 🧪 БЕТА\n\n"
            f"Фильтр: <b>{filter_data['name']}</b>\n\n"
            f"<b>Главные слова (вес 2x):</b>\n<code>—</code>\n\n"
            f"<b>Дополнительные (вес 1x):</b>\n<code>—</code>\n\n"
            f"💡 Главные слова имеют приоритет при ранжировании результатов.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка в clear_priority_keywords: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


# --- Отмена ввода расширенных настроек ---

@router.callback_query(F.data.startswith("ext_cancel_"))
async def cancel_extended_input(callback: CallbackQuery, state: FSMContext):
    """Отменить ввод и вернуться к настройкам фильтра."""
    await callback.answer("↩️ Отменено")

    try:
        filter_id = int(callback.data.replace("ext_cancel_", ""))
        await state.clear()

        # Возвращаемся к настройкам фильтра - используем helper функцию
        db = await get_sniper_db()
        settings_text, keyboard = await build_filter_extended_options_view(filter_id, db)

        if settings_text:
            await callback.message.edit_text(settings_text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await callback.message.edit_text("❌ Фильтр не найден", parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка в cancel_extended_input: {e}", exc_info=True)
        await state.clear()
        await callback.message.edit_text(
            "❌ Произошла ошибка. Попробуйте ещё раз.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ]),
            parse_mode="HTML"
        )


# --- Обработка текстового ввода расширенных настроек ---

def validate_inn(inn: str) -> bool:
    """Проверить корректность ИНН (10 или 12 цифр)."""
    return inn.isdigit() and len(inn) in (10, 12)


@router.message(ExtendedSettingsStates.waiting_for_input)
async def process_extended_settings_input(message: Message, state: FSMContext):
    """Обработка текстового ввода для расширенных настроек."""
    try:
        data = await state.get_data()
        filter_id = data.get('ext_filter_id')
        setting = data.get('ext_setting')

        if not filter_id or not setting:
            await message.answer("❌ Ошибка: настройка не определена")
            await state.clear()
            return

        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await message.answer("❌ Фильтр не найден")
            await state.clear()
            return

        text = message.text.strip()
        update_data = {}
        success_message = ""

        # Обработка разных типов настроек
        if setting == 'purchase_number':
            # Номер закупки - одно значение
            update_data['purchase_number'] = text
            success_message = f"✅ Номер закупки установлен: <code>{text}</code>"

        elif setting == 'customer_inn':
            # ИНН заказчиков - список через запятую
            inns = [inn.strip() for inn in text.split(',') if inn.strip()]
            valid_inns = []
            invalid_inns = []

            for inn in inns:
                if validate_inn(inn):
                    valid_inns.append(inn)
                else:
                    invalid_inns.append(inn)

            if invalid_inns:
                await message.answer(
                    f"⚠️ Некорректные ИНН (должны быть 10 или 12 цифр):\n"
                    f"<code>{', '.join(invalid_inns)}</code>\n\n"
                    f"Введите ИНН заново или нажмите «Отмена».",
                    parse_mode="HTML"
                )
                return

            update_data['customer_inn'] = valid_inns
            success_message = f"✅ Добавлено ИНН: {len(valid_inns)}"

        elif setting == 'excluded_customer_inns':
            # Черный список ИНН - список через запятую
            inns = [inn.strip() for inn in text.split(',') if inn.strip()]
            valid_inns = []
            invalid_inns = []

            for inn in inns:
                if validate_inn(inn):
                    valid_inns.append(inn)
                else:
                    invalid_inns.append(inn)

            if invalid_inns:
                await message.answer(
                    f"⚠️ Некорректные ИНН (должны быть 10 или 12 цифр):\n"
                    f"<code>{', '.join(invalid_inns)}</code>\n\n"
                    f"Введите ИНН заново или нажмите «Отмена».",
                    parse_mode="HTML"
                )
                return

            # Добавляем к существующим
            existing = filter_data.get('excluded_customer_inns', []) or []
            combined = list(set(existing + valid_inns))
            update_data['excluded_customer_inns'] = combined
            success_message = f"✅ В черный список добавлено ИНН: {len(valid_inns)}"

        elif setting == 'excluded_customer_keywords':
            # Ключевые слова черного списка
            keywords = [kw.strip() for kw in text.split(',') if kw.strip()]
            existing = filter_data.get('excluded_customer_keywords', []) or []
            combined = list(set(existing + keywords))
            if len(combined) > MAX_EXCLUDE_KEYWORDS:
                await message.answer(
                    f"⚠️ Черный список слишком большой: {len(combined)} слов (максимум {MAX_EXCLUDE_KEYWORDS}).",
                    parse_mode="HTML"
                )
                await state.clear()
                return
            update_data['excluded_customer_keywords'] = combined
            success_message = f"✅ В черный список добавлено слов: {len(keywords)}"

        elif setting == 'primary_keywords':
            # Главные ключевые слова
            keywords = [kw.strip() for kw in text.split(',') if kw.strip()]
            if len(keywords) > MAX_PRIMARY_KEYWORDS:
                await message.answer(
                    f"⚠️ Максимум {MAX_PRIMARY_KEYWORDS} главных слов (сейчас: {len(keywords)}).",
                    parse_mode="HTML"
                )
                await state.clear()
                return
            update_data['primary_keywords'] = keywords
            success_message = f"✅ Главные слова установлены: {len(keywords)}"

        elif setting == 'secondary_keywords':
            # Дополнительные ключевые слова
            keywords = [kw.strip() for kw in text.split(',') if kw.strip()]
            if len(keywords) > MAX_SECONDARY_KEYWORDS:
                await message.answer(
                    f"⚠️ Максимум {MAX_SECONDARY_KEYWORDS} дополнительных слов (сейчас: {len(keywords)}).",
                    parse_mode="HTML"
                )
                await state.clear()
                return
            update_data['secondary_keywords'] = keywords
            success_message = f"✅ Дополнительные слова установлены: {len(keywords)}"

        else:
            await message.answer("❌ Неизвестный тип настройки")
            await state.clear()
            return

        # Обновляем фильтр в БД
        await db.update_filter(filter_id, **update_data)
        await state.clear()

        # Определяем куда вернуться
        if setting in ('excluded_customer_inns', 'excluded_customer_keywords'):
            back_callback = f"ext_blacklist_{filter_id}"
        elif setting in ('primary_keywords', 'secondary_keywords'):
            back_callback = f"ext_priority_{filter_id}"
        else:
            back_callback = f"ext_filter_{filter_id}"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад к настройкам", callback_data=back_callback)],
            [InlineKeyboardButton(text="🎯 Меню Sniper", callback_data="sniper_menu")]
        ])

        await message.answer(
            f"{success_message}\n\n"
            f"Фильтр: <b>{filter_data['name']}</b>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка в process_extended_settings_input: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка при сохранении")
        await state.clear()


# ============================================
# AI ФУНКЦИИ (PREMIUM)
# ============================================

@router.callback_query(F.data.startswith("ai_summary_"))
async def ai_summary_handler(callback: CallbackQuery):
    """
    Генерирует AI-резюме тендера (только Premium).
    """
    await callback.answer("🤖 Генерирую резюме...")

    try:
        tender_number = callback.data.replace("ai_summary_", "")

        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        subscription_tier = user.get('subscription_tier', 'trial')

        # Импортируем AI модули
        from tender_sniper.ai_features import AIFeatureGate, format_ai_feature_locked_message
        from tender_sniper.ai_summarizer import get_summarizer

        gate = AIFeatureGate(subscription_tier)

        if not gate.can_use('summarization'):
            # Показываем upsell
            await callback.message.answer(
                format_ai_feature_locked_message('summarization'),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⭐ Перейти на Premium", callback_data="upgrade_plan")],
                    [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")]
                ])
            )
            return

        # Получаем данные тендера (из кэша или API)
        # Пока используем базовую информацию из сообщения
        original_text = callback.message.text or ""

        summarizer = get_summarizer()
        summary, is_ai = await summarizer.summarize(
            tender_text=original_text,
            tender_data={'number': tender_number},
            subscription_tier=subscription_tier
        )

        await callback.message.answer(
            f"📝 <b>AI-резюме тендера {tender_number}</b>\n\n{summary}",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка AI-резюме: {e}", exc_info=True)
        await callback.message.answer("❌ Не удалось сгенерировать резюме")


@router.callback_query(F.data == "show_premium_ai")
async def show_premium_ai_features(callback: CallbackQuery):
    """Показывает информацию о Premium AI функциях."""
    await callback.answer()

    try:
        from tender_sniper.ai_features import get_ai_upgrade_message

        await callback.message.answer(
            get_ai_upgrade_message(),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⭐ Перейти на Premium", callback_data="upgrade_plan")],
                [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")]
            ])
        )

    except Exception as e:
        logger.error(f"Ошибка show_premium_ai: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("analyze_docs_"))
async def analyze_tender_documentation(callback: CallbackQuery):
    """
    Анализирует документацию тендера и извлекает структурированные данные (Premium).
    Использует общую функцию _run_ai_analysis() из webapp.py.
    """
    await callback.answer("🔍 Загружаю документацию...")

    try:
        tender_number = callback.data.replace("analyze_docs_", "")

        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        subscription_tier = user.get('subscription_tier', 'trial')

        # Импортируем AI модули
        from tender_sniper.ai_features import AIFeatureGate, format_ai_feature_locked_message

        gate = AIFeatureGate(subscription_tier)

        if not gate.can_use('document_extraction'):
            await callback.message.answer(
                format_ai_feature_locked_message('document_extraction'),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⭐ Перейти на Premium", callback_data="upgrade_plan")],
                    [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")]
                ])
            )
            return

        # Отправляем сообщение о начале анализа
        status_msg = await callback.message.answer(
            f"🔍 <b>Анализирую документацию тендера {tender_number}...</b>\n\n"
            f"Это может занять некоторое время.",
            parse_mode="HTML"
        )

        try:
            from bot.handlers.webapp import _run_ai_analysis

            formatted, is_ai = await _run_ai_analysis(tender_number, subscription_tier)

            await status_msg.edit_text(
                formatted,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="📄 Открыть на zakupki.gov.ru",
                        url=f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber={tender_number}"
                    )],
                    [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")]
                ])
            )

        except ImportError as ie:
            logger.error(f"Модуль не найден: {ie}")
            await status_msg.edit_text(
                "❌ Функция анализа документации временно недоступна.\n\n"
                "Необходимые модули не установлены.",
                parse_mode="HTML"
            )

        except RuntimeError as re_err:
            await status_msg.edit_text(
                f"❌ {re_err}\n\n"
                f"Тендер: {tender_number}\n"
                "Возможно, документы недоступны или тендер завершён.",
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"Ошибка анализа документации: {e}", exc_info=True)
        await callback.message.answer("❌ Не удалось проанализировать документацию")


# ============================================
# PER-FILTER NOTIFICATION TARGETS
# ============================================

async def _render_notify_targets(message, filter_id: int, user_tg_id: int, bot=None):
    """Отрисовка меню выбора адресатов уведомлений."""
    db = await get_sniper_db()
    filter_data = await db.get_filter_by_id(filter_id)

    if not filter_data:
        await message.edit_text("❌ Фильтр не найден", parse_mode="HTML")
        return

    current_targets = filter_data.get('notify_chat_ids') or []

    # Получаем все активные группы и проверяем членство юзера
    groups = []
    if bot:
        all_groups = await db.get_all_active_groups()
        for g in all_groups:
            try:
                member = await bot.get_chat_member(g['telegram_id'], user_tg_id)
                if member.status not in ('left', 'kicked'):
                    groups.append(g)
            except Exception:
                pass
    else:
        # Фоллбек на старый метод если bot не передан
        groups = await db.get_user_groups(user_tg_id)

    buttons = []

    # Личный чат
    personal_check = "✅" if user_tg_id in current_targets or not current_targets else "☐"
    buttons.append([InlineKeyboardButton(
        text=f"{personal_check} Мне в личку",
        callback_data=f"ext_ntgt_{filter_id}_{user_tg_id}"
    )])

    # Группы
    for group in groups:
        group_check = "✅" if group['telegram_id'] in current_targets else "☐"
        group_name = group['name'][:30]
        buttons.append([InlineKeyboardButton(
            text=f"{group_check} {group_name}",
            callback_data=f"ext_ntgt_{filter_id}_{group['telegram_id']}"
        )])

    if not groups:
        buttons.append([InlineKeyboardButton(
            text="ℹ️ Добавьте бота в группу",
            callback_data="noop"
        )])

    buttons.append([InlineKeyboardButton(
        text="« Назад к настройкам",
        callback_data=f"ext_filter_{filter_id}"
    )])

    text = (
        f"📱 <b>Куда уведомлять</b>\n\n"
        f"Фильтр: <b>{filter_data['name']}</b>\n\n"
        f"Выберите, куда отправлять уведомления.\n"
        f"Если не выбрано ничего — отправка только в личку."
    )

    await message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("ext_notify_"))
async def ext_notify_targets_handler(callback: CallbackQuery):
    """Показывает меню выбора адресатов уведомлений для фильтра."""
    await callback.answer()
    try:
        filter_id = int(callback.data.replace("ext_notify_", ""))
        await _render_notify_targets(callback.message, filter_id, callback.from_user.id, bot=callback.bot)
    except Exception as e:
        logger.error(f"Ошибка отображения целей уведомлений: {e}", exc_info=True)
        await callback.message.edit_text("❌ Произошла ошибка", parse_mode="HTML")


@router.callback_query(F.data.startswith("ext_ntgt_"))
async def ext_notify_toggle_target_handler(callback: CallbackQuery):
    """Тогл конкретного адресата уведомлений для фильтра."""
    await callback.answer()

    try:
        # ext_ntgt_{filter_id}_{chat_id} (chat_id может быть отрицательным)
        parts = callback.data.split("_")
        filter_id = int(parts[2])
        chat_id = int("_".join(parts[3:]))

        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await callback.message.edit_text("❌ Фильтр не найден", parse_mode="HTML")
            return

        # Проверяем что фильтр принадлежит текущему юзеру
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)
        if sniper_user and filter_data.get('user_id') != sniper_user['id']:
            await callback.answer("⚠️ Это не ваш фильтр", show_alert=True)
            return

        current_targets = list(filter_data.get('notify_chat_ids') or [])

        # Тоглим
        if chat_id in current_targets:
            current_targets.remove(chat_id)
        else:
            current_targets.append(chat_id)

        # Сохраняем
        await db.update_filter(filter_id, notify_chat_ids=current_targets if current_targets else None)

        # Перерисовываем клавиатуру
        await _render_notify_targets(callback.message, filter_id, callback.from_user.id, bot=callback.bot)

    except Exception as e:
        logger.error(f"Ошибка переключения цели уведомлений: {e}", exc_info=True)
        await callback.message.edit_text("❌ Произошла ошибка", parse_mode="HTML")


# ============================================
# ВОЗВРАТ В ГЛАВНОЕ МЕНЮ
# ============================================
# УДАЛЕН: Дублирующий обработчик sniper_menu (строка 94 уже обрабатывает)
# Причина: cmd_sniper_menu(callback.message) использует message.answer() вместо edit_text(),
# что приводит к зависанию при нажатии кнопки "Главное меню" из разделов
