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
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import sys
from pathlib import Path

# Добавляем путь для импорта Tender Sniper
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tender_sniper.database import get_sniper_db, get_plan_limits
from tender_sniper.config import is_tender_sniper_enabled

router = Router()


class SniperStates(StatesGroup):
    """Состояния для FSM управления фильтрами."""
    waiting_for_filter_name = State()
    waiting_for_keywords = State()
    waiting_for_price_range = State()
    waiting_for_regions = State()


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
        [InlineKeyboardButton(text="📊 Статистика", callback_data="sniper_stats")],
        [InlineKeyboardButton(text="💎 Тарифы", callback_data="sniper_plans")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="sniper_help")]
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

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Новый поиск", callback_data="sniper_new_search")],
        [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="sniper_stats")],
        [InlineKeyboardButton(text="💎 Тарифы", callback_data="sniper_plans")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="sniper_help")]
    ])

    await callback.message.edit_text(
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

        # Получаем лимиты тарифа
        plan_limits = await get_plan_limits(db.db_path, user['subscription_tier'])

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
            f"• Активных фильтров: {stats['active_filters']}/{plan_limits.get('max_filters', 5)}\n"
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
            [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")]
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
        [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")]
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
                [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")]
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
            import json
            keywords = json.loads(f.get('keywords', '[]'))
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
            InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")
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

        # Получаем текущие фильтры и лимиты
        filters = await db.get_active_filters(user['id'])
        plan_limits = await get_plan_limits(db.db_path, user['subscription_tier'])
        max_filters = plan_limits.get('max_filters', 5)

        if len(filters) >= max_filters:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬆️ Улучшить тариф", callback_data="sniper_plans")],
                [InlineKeyboardButton(text="« Назад", callback_data="sniper_my_filters")]
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
        user = await db.get_user_by_telegram_id(message.from_user.id if hasattr(message, 'from_user') else message.chat.id)

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
            [InlineKeyboardButton(text="🎯 Главное меню", callback_data="sniper_menu")]
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
        [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")]
    ])

    await callback.message.edit_text(
        help_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# ============================================
# ВОЗВРАТ В ГЛАВНОЕ МЕНЮ
# ============================================

@router.callback_query(F.data == "sniper_menu")
async def return_to_sniper_menu(callback: CallbackQuery):
    """Возврат в главное меню Tender Sniper."""
    # Вызываем главное меню
    await cmd_sniper_menu(callback.message)
