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
from pathlib import Path

# Добавляем путь для импорта Tender Sniper
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tender_sniper.database import get_sniper_db, get_plan_limits
from tender_sniper.config import is_tender_sniper_enabled
from tender_sniper.all_tenders_report import generate_all_tenders_html

logger = logging.getLogger(__name__)
router = Router()


class SniperStates(StatesGroup):
    """Состояния для FSM управления фильтрами."""
    waiting_for_filter_name = State()
    waiting_for_keywords = State()
    waiting_for_exclude_keywords = State()
    waiting_for_price_range = State()
    waiting_for_regions = State()
    waiting_for_law_type = State()
    waiting_for_purchase_stage = State()
    waiting_for_purchase_method = State()
    waiting_for_tender_type = State()
    waiting_for_okpd2 = State()
    waiting_for_min_deadline = State()
    waiting_for_customer_keywords = State()


# ============================================
# ГЛАВНОЕ МЕНЮ TENDER SNIPER
# ============================================

@router.message(Command("sniper"))
@router.message(F.text == "🎯 Tender Sniper")
async def cmd_sniper_menu(message: Message):
    """Главное меню Tender Sniper."""

    # Проверяем, включен ли Tender Sniper
    if not is_tender_sniper_enabled():
        await message.answer(
            "⚠️ <b>Tender Sniper временно недоступен</b>\n\n"
            "Функция находится в стадии внедрения. "
            "Используйте обычный поиск через /start",
            parse_mode="HTML"
        )
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Новый поиск", callback_data="sniper_new_search")],
        [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
        [InlineKeyboardButton(text="📊 Все мои тендеры", callback_data="sniper_all_tenders")],
        [InlineKeyboardButton(text="📈 Статистика", callback_data="sniper_stats")],
        [InlineKeyboardButton(text="💎 Тарифы", callback_data="sniper_plans")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="sniper_help")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await message.answer(
        "🎯 <b>Tender Sniper - Умный поиск тендеров</b>\n\n"
        "<b>Новый workflow:</b>\n"
        "1️⃣ Создаете фильтр с критериями\n"
        "2️⃣ AI расширяет ваш запрос\n"
        "3️⃣ Получаете HTML отчет с тендерами\n"
        "4️⃣ Включаете автомониторинг (опционально)\n\n"
        "<b>Возможности:</b>\n"
        "• 🤖 AI расширение критериев поиска\n"
        "• 📊 Мгновенный поиск до 25 тендеров\n"
        "• 📄 Красивые HTML отчеты\n"
        "• 🔔 Автоматические уведомления\n\n"
        "Начните с создания фильтра!",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "sniper_menu")
async def show_sniper_menu(callback: CallbackQuery):
    """Callback для возврата в главное меню Sniper."""
    await callback.answer()

    # Проверяем статус автомониторинга
    db = await get_sniper_db()
    is_monitoring_enabled = await db.get_monitoring_status(callback.from_user.id)

    # Кнопка паузы/возобновления
    if is_monitoring_enabled:
        monitoring_button = InlineKeyboardButton(text="⏸️ Пауза автомониторинга", callback_data="sniper_pause_monitoring")
        monitoring_status = "🟢 <b>Автомониторинг активен</b>"
    else:
        monitoring_button = InlineKeyboardButton(text="▶️ Возобновить автомониторинг", callback_data="sniper_resume_monitoring")
        monitoring_status = "🔴 <b>Автомониторинг на паузе</b>"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Новый поиск", callback_data="sniper_new_search")],
        [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
        [InlineKeyboardButton(text="📊 Все мои тендеры", callback_data="sniper_all_tenders")],
        [monitoring_button],
        [InlineKeyboardButton(text="📈 Статистика", callback_data="sniper_stats")],
        [InlineKeyboardButton(text="💎 Тарифы", callback_data="sniper_plans")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="sniper_help")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await callback.message.edit_text(
        f"🎯 <b>Tender Sniper - Умный поиск тендеров</b>\n\n"
        f"{monitoring_status}\n\n"
        f"<b>Два режима работы:</b>\n\n"
        f"🔍 <b>Новый поиск</b> (мгновенный)\n"
        f"→ Разовый поиск по критериям\n"
        f"→ Получаете HTML отчет сразу\n"
        f"→ Нет автоматических уведомлений\n\n"
        f"📋 <b>Мои фильтры</b> (автомониторинг)\n"
        f"→ Создаете постоянные фильтры\n"
        f"→ Бот автоматически ищет новые тендеры\n"
        f"→ Получаете уведомления 24/7\n\n"
        f"<b>Возможности:</b>\n"
        f"• 🤖 AI расширение критериев\n"
        f"• 📄 Красивые HTML отчеты\n"
        f"• 🔔 Умные уведомления\n\n"
        f"<i>Выберите режим работы ниже</i>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


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
                subscription_tier='free'
            )
            user = await db.get_user_by_telegram_id(callback.from_user.id)

        # Получаем статистику
        stats = await db.get_user_stats(user['id'])

        # Получаем лимиты тарифа (хардкод, пока не мигрирован на PostgreSQL)
        max_filters = 5 if user['subscription_tier'] == 'free' else 15

        # Определяем emoji для тарифа
        tier_emoji = {
            'free': '🆓',
            'basic': '⭐',
            'premium': '💎'
        }.get(user['subscription_tier'], '🆓')

        tier_name = {
            'free': 'Бесплатный',
            'basic': 'Базовый',
            'premium': 'Премиум'
        }.get(user['subscription_tier'], 'Бесплатный')

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

        "🆓 <b>Бесплатный</b>\n"
        "• 5 фильтров мониторинга\n"
        "• 10 уведомлений в день\n"
        "• Базовый поиск\n"
        "• История поисков\n\n"

        "⭐ <b>Базовый - 15,000 ₽/мес</b>\n"
        "• 15 фильтров мониторинга\n"
        "• 50 уведомлений в день\n"
        "• AI-анализ тендеров (ограниченный)\n"
        "• Email поддержка\n"
        "• Приоритет в обработке\n"
        "• Экспорт в Excel\n\n"

        "💎 <b>Премиум - 50,000 ₽/мес</b>\n"
        "• Неограниченные фильтры\n"
        "• Неограниченные уведомления\n"
        "• Полный AI-анализ\n"
        "• API доступ\n"
        "• 24/7 приоритетная поддержка\n"
        "• Персональный менеджер\n"
        "• Расширенная аналитика\n"
        "• Интеграция с CRM\n\n"

        "<i>Оплата: YooKassa, CloudPayments</i>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оформить Базовый", callback_data="sniper_buy_basic")],
        [InlineKeyboardButton(text="💎 Оформить Премиум", callback_data="sniper_buy_premium")],
        [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await callback.message.edit_text(
        plans_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("sniper_buy_"))
async def process_subscription_purchase(callback: CallbackQuery):
    """Обработка покупки подписки."""
    await callback.answer("⚠️ Оплата в разработке")

    await callback.message.answer(
        "💳 <b>Оплата подписки</b>\n\n"
        "Интеграция с платежными системами находится в разработке.\n\n"
        "Для оформления подписки напишите администратору:\n"
        "📧 admin@tenderbot.ru\n\n"
        "Мы свяжемся с вами в течение 24 часов.",
        parse_mode="HTML"
    )


# ============================================
# МОИ ФИЛЬТРЫ
# ============================================

@router.callback_query(F.data == "sniper_my_filters")
async def show_my_filters(callback: CallbackQuery):
    """Показать список фильтров пользователя."""
    await callback.answer()

    try:
        db = await get_sniper_db()

        # Получаем или создаем пользователя
        user = await db.get_user_by_telegram_id(callback.from_user.id)
        if not user:
            await db.create_or_update_user(
                telegram_id=callback.from_user.id,
                username=callback.from_user.username,
                first_name=callback.from_user.first_name,
                subscription_tier='free'
            )
            user = await db.get_user_by_telegram_id(callback.from_user.id)

        # Получаем фильтры
        filters = await db.get_active_filters(user['id'])

        if not filters:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать первый фильтр", callback_data="sniper_create_filter")],
                [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])

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
            InlineKeyboardButton(text="➕ Добавить фильтр", callback_data="sniper_create_filter")
        ])
        keyboard_buttons.append([
            InlineKeyboardButton(text="🗑️ Удалить все фильтры", callback_data="confirm_delete_all_filters")
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
        await callback.message.answer(
            f"❌ Ошибка при получении фильтров: {str(e)}"
        )


# ============================================
# СОЗДАНИЕ ФИЛЬТРА
# ============================================

@router.callback_query(F.data == "sniper_create_filter")
async def start_create_filter(callback: CallbackQuery, state: FSMContext):
    """Начало создания нового фильтра."""
    await callback.answer()

    # Проверяем лимиты
    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await db.create_or_update_user(
                telegram_id=callback.from_user.id,
                username=callback.from_user.username,
                first_name=callback.from_user.first_name,
                subscription_tier='free'
            )
            user = await db.get_user_by_telegram_id(callback.from_user.id)

        # Получаем текущие фильтры и лимиты (хардкод, пока не мигрирован на PostgreSQL)
        filters = await db.get_active_filters(user['id'])
        max_filters = 5 if user['subscription_tier'] == 'free' else 15

        if len(filters) >= max_filters:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬆️ Улучшить тариф", callback_data="sniper_plans")],
                [InlineKeyboardButton(text="« Назад", callback_data="sniper_my_filters")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])

            await callback.message.edit_text(
                f"⚠️ <b>Достигнут лимит фильтров</b>\n\n"
                f"Ваш тариф: {user['subscription_tier']}\n"
                f"Максимум фильтров: {max_filters}\n\n"
                f"Для создания дополнительных фильтров улучшите тариф.",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            return

    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {str(e)}")
        return

    # Переходим в состояние ввода названия
    await state.set_state(SniperStates.waiting_for_filter_name)

    await callback.message.edit_text(
        "➕ <b>Создание нового фильтра</b>\n\n"
        "Шаг 1 из 4: Название фильтра\n\n"
        "Введите название (например: \"IT оборудование\" или \"Медицинские товары\"):",
        parse_mode="HTML"
    )


@router.message(SniperStates.waiting_for_filter_name)
async def process_filter_name(message: Message, state: FSMContext):
    """Обработка названия фильтра."""
    filter_name = message.text.strip()

    if len(filter_name) < 3:
        await message.answer("⚠️ Название должно содержать минимум 3 символа")
        return

    # Сохраняем название
    await state.update_data(filter_name=filter_name)
    await state.set_state(SniperStates.waiting_for_keywords)

    await message.answer(
        f"✅ Название: <b>{filter_name}</b>\n\n"
        f"Шаг 2 из 4: Ключевые слова\n\n"
        f"Введите ключевые слова через запятую:\n\n"
        f"Пример: компьютеры, ноутбуки, серверы",
        parse_mode="HTML"
    )


@router.message(SniperStates.waiting_for_keywords)
async def process_keywords(message: Message, state: FSMContext):
    """Обработка ключевых слов."""
    keywords_text = message.text.strip()
    keywords = [k.strip() for k in keywords_text.split(',') if k.strip()]

    if len(keywords) < 1:
        await message.answer("⚠️ Укажите хотя бы одно ключевое слово")
        return

    # Сохраняем ключевые слова
    await state.update_data(keywords=keywords)
    await state.set_state(SniperStates.waiting_for_price_range)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="sniper_skip_price")]
    ])

    await message.answer(
        f"✅ Ключевые слова: {', '.join(keywords)}\n\n"
        f"Шаг 3 из 4: Ценовой диапазон\n\n"
        f"Введите диапазон цен в формате:\n"
        f"<code>мин макс</code>\n\n"
        f"Пример: <code>100000 5000000</code>\n\n"
        f"Или нажмите \"Пропустить\" для любой цены",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "sniper_skip_price", SniperStates.waiting_for_price_range)
async def skip_price_range(callback: CallbackQuery, state: FSMContext):
    """Пропуск ценового диапазона."""
    await callback.answer()

    await state.update_data(price_min=None, price_max=None)
    await finalize_filter_creation(callback.message, state)


@router.message(SniperStates.waiting_for_price_range)
async def process_price_range(message: Message, state: FSMContext):
    """Обработка ценового диапазона."""
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

        await state.update_data(price_min=price_min, price_max=price_max)
        await finalize_filter_creation(message, state)

    except ValueError:
        await message.answer(
            "⚠️ Введите корректные числа.\n"
            "Пример: <code>100000 5000000</code>",
            parse_mode="HTML"
        )


async def finalize_filter_creation(message: Message, state: FSMContext):
    """Завершение создания фильтра."""
    data = await state.get_data()

    try:
        db = await get_sniper_db()
        telegram_id = message.from_user.id if hasattr(message, 'from_user') else message.chat.id

        # Получаем или создаем пользователя
        user = await db.get_user_by_telegram_id(telegram_id)
        if not user:
            # Создаем пользователя с бесплатным тарифом
            await db.create_or_update_user(
                telegram_id=telegram_id,
                username=message.from_user.username if hasattr(message, 'from_user') else None,
                subscription_tier='free'
            )
            user = await db.get_user_by_telegram_id(telegram_id)

        # Создаем фильтр
        filter_id = await db.create_filter(
            user_id=user['id'],
            name=data['filter_name'],
            keywords=data['keywords'],
            price_min=data.get('price_min'),
            price_max=data.get('price_max'),
            regions=None,  # TODO: добавить выбор регионов
            tender_types=['товары']  # По умолчанию товары
        )

        await state.clear()

        price_text = ""
        if data.get('price_min') and data.get('price_max'):
            price_text = f"\n💰 Цена: {data['price_min']:,} - {data['price_max']:,} ₽"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
            [InlineKeyboardButton(text="🎯 Меню Sniper", callback_data="sniper_menu")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        await message.answer(
            f"✅ <b>Фильтр создан успешно!</b>\n\n"
            f"📝 Название: {data['filter_name']}\n"
            f"🔑 Ключевые слова: {', '.join(data['keywords'])}"
            f"{price_text}\n\n"
            f"🔔 Вы будете получать уведомления о новых подходящих тендерах автоматически!",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        await message.answer(f"❌ Ошибка при создании фильтра: {str(e)}")
        await state.clear()


# ============================================
# ПОМОЩЬ
# ============================================

@router.callback_query(F.data == "sniper_help")
async def show_sniper_help(callback: CallbackQuery):
    """Показать справку по Tender Sniper."""
    await callback.answer()

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
        "• Premium: Unlimited\n\n"

        "<b>Советы по созданию фильтров</b>\n"
        "• Используйте конкретные ключевые слова\n"
        "• Указывайте ценовой диапазон для точности\n"
        "• Создавайте отдельные фильтры для разных категорий\n"
        "• Проверяйте статистику для оптимизации"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Инструкция для новичков", callback_data="start_onboarding")],
        [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await callback.message.edit_text(
        help_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# ============================================
# ПРОСМОТР И РЕДАКТИРОВАНИЕ ФИЛЬТРА
# ============================================

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

        # Кнопки управления фильтром
        keyboard_buttons = [
            [InlineKeyboardButton(
                text="✏️ Редактировать цену",
                callback_data=f"edit_filter_price_{filter_id}"
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
            [InlineKeyboardButton(text="📋 Просмотреть фильтр", callback_data=f"sniper_filter_{filter_id}")],
            [InlineKeyboardButton(text="« К списку фильтров", callback_data="sniper_my_filters")]
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


@router.callback_query(F.data.startswith("toggle_filter_"))
async def toggle_filter_status(callback: CallbackQuery):
    """Переключить статус фильтра (активен/приостановлен)."""
    await callback.answer()

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
        await callback.answer(f"Фильтр {status_text}", show_alert=True)

        # Обновляем отображение фильтра
        await show_filter_details(callback)

    except Exception as e:
        await callback.message.answer(f"❌ Ошибка: {str(e)}")


@router.callback_query(F.data.startswith("delete_filter_"))
async def delete_filter(callback: CallbackQuery):
    """Удалить фильтр."""
    await callback.answer()

    try:
        filter_id = int(callback.data.replace("delete_filter_", ""))

        db = await get_sniper_db()
        filter_data = await db.get_filter_by_id(filter_id)

        if not filter_data:
            await callback.message.answer("❌ Фильтр не найден")
            return

        # Удаляем фильтр
        await db.delete_filter(filter_id)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        await callback.message.edit_text(
            f"✅ <b>Фильтр удален</b>\n\n"
            f"Фильтр «{filter_data['name']}» успешно удален.",
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
            f"⚠️ <b>Подтверждение удаления</b>\n\n"
            f"Вы уверены, что хотите удалить все {filters_count} фильтр(ов)?\n\n"
            f"<i>Это действие необратимо!</i>",
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
            [InlineKeyboardButton(text="➕ Создать новый фильтр", callback_data="sniper_create_filter")],
            [InlineKeyboardButton(text="🎯 Меню Sniper", callback_data="sniper_menu")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        await callback.message.edit_text(
            f"✅ <b>Все фильтры удалены</b>\n\n"
            f"Удалено фильтров: {deleted_count}\n\n"
            f"Вы можете создать новые фильтры для мониторинга тендеров.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при удалении фильтров: {str(e)}")


# ============================================
# ОБРАТНАЯ СВЯЗЬ ПО ТЕНДЕРАМ
# ============================================

@router.callback_query(F.data.startswith("interested_"))
async def mark_tender_interesting(callback: CallbackQuery):
    """Пользователь отметил тендер как интересный."""
    await callback.answer("👍 Отмечено как интересное")

    try:
        # Извлекаем номер тендера из callback_data
        tender_number = callback.data.replace("interested_", "")

        # Здесь можно сохранить в БД для аналитики/ML
        logger.info(f"Пользователь {callback.from_user.id} отметил тендер {tender_number} как интересный")

        # Обновляем сообщение
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Отмечено как интересное", callback_data="noop")]
            ])
        )

    except Exception as e:
        logger.error(f"Ошибка при отметке тендера: {e}", exc_info=True)


@router.callback_query(F.data.startswith("skip_"))
async def mark_tender_skipped(callback: CallbackQuery):
    """Пользователь пропустил тендер."""
    await callback.answer("👎 Пропущено")

    try:
        # Извлекаем номер тендера из callback_data
        tender_number = callback.data.replace("skip_", "")

        # Здесь можно сохранить в БД для аналитики/ML
        logger.info(f"Пользователь {callback.from_user.id} пропустил тендер {tender_number}")

        # Обновляем сообщение
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Пропущено", callback_data="noop")]
            ])
        )

    except Exception as e:
        logger.error(f"Ошибка при пропуске тендера: {e}", exc_info=True)


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    """Пустой callback для неактивных кнопок."""
    await callback.answer()


# ============================================
# ВОЗВРАТ В ГЛАВНОЕ МЕНЮ
# ============================================
# УДАЛЕН: Дублирующий обработчик sniper_menu (строка 94 уже обрабатывает)
# Причина: cmd_sniper_menu(callback.message) использует message.answer() вместо edit_text(),
# что приводит к зависанию при нажатии кнопки "Главное меню" из разделов
