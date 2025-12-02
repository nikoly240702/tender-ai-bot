"""
Sniper Search - новый workflow для создания фильтров с мгновенным поиском.

Процесс:
1. Пользователь создает фильтр
2. AI расширяет критерии
3. Выполняется мгновенный поиск (до 25 тендеров)
4. Пользователь получает HTML отчет
5. Опционально включает автоматический мониторинг
"""

import asyncio
import json
from pathlib import Path
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
import logging

from tender_sniper.database import get_sniper_db, get_plan_limits
from tender_sniper.query_expander import QueryExpander
from tender_sniper.instant_search import InstantSearch

logger = logging.getLogger(__name__)

router = Router()


# ============================================
# FSM States для нового процесса
# ============================================

class FilterSearchStates(StatesGroup):
    """Состояния для создания фильтра с поиском."""
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
    waiting_for_tender_count = State()
    confirm_auto_monitoring = State()


# ============================================
# НОВЫЙ WORKFLOW: СОЗДАНИЕ ФИЛЬТРА + ПОИСК
# ============================================

@router.callback_query(F.data == "sniper_create_filter")
async def start_create_filter_only(callback: CallbackQuery, state: FSMContext):
    """Создание фильтра БЕЗ мгновенного поиска (сразу активен)."""
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

        # Проверяем квоту на фильтры
        filters = await db.get_active_filters(user['id'])
        plan_limits = await get_plan_limits(db.db_path, user['subscription_tier'])
        max_filters = plan_limits.get('max_filters', 5)

        if len(filters) >= max_filters:
            await callback.message.edit_text(
                f"⚠️ <b>Достигнут лимит фильтров</b>\n\n"
                f"Ваш тариф <b>{user['subscription_tier'].title()}</b> позволяет создать максимум {max_filters} фильтров.\n"
                f"У вас уже создано: {len(filters)}\n\n"
                f"Удалите старые фильтры или обновите подписку.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")]
                ])
            )
            return

        # Сохраняем что это создание БЕЗ instant search
        await state.update_data(with_instant_search=False)

        # Запускаем процесс создания фильтра
        await state.set_state(FilterSearchStates.waiting_for_filter_name)

        await callback.message.edit_text(
            "➕ <b>Создание фильтра для автомониторинга</b>\n\n"
            "<b>Шаг 1/13:</b> Название фильтра\n\n"
            "Придумайте короткое название для вашего фильтра.\n"
            "Например: <i>IT оборудование</i>, <i>Медицинские товары</i>\n\n"
            "💡 Это название поможет вам управлять фильтрами в будущем.\n\n"
            "🔔 Фильтр будет сразу активен для мониторинга.",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Error starting filter creation: {e}", exc_info=True)
        await callback.message.answer("❌ Произошла ошибка. Попробуйте позже.")


@router.callback_query(F.data == "sniper_new_search")
async def start_new_filter_search(callback: CallbackQuery, state: FSMContext):
    """Начало нового workflow: создание фильтра + мгновенный поиск."""
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

        # Проверяем квоту на фильтры
        filters = await db.get_active_filters(user['id'])
        plan_limits = await get_plan_limits(db.db_path, user['subscription_tier'])
        max_filters = plan_limits.get('max_filters', 5)

        if len(filters) >= max_filters:
            await callback.message.edit_text(
                f"⚠️ <b>Достигнут лимит фильтров</b>\n\n"
                f"Ваш тариф <b>{user['subscription_tier'].title()}</b> позволяет создать максимум {max_filters} фильтров.\n"
                f"У вас уже создано: {len(filters)}\n\n"
                f"Удалите старые фильтры или обновите подписку.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")]
                ])
            )
            return

        # Сохраняем что это поиск с instant search
        await state.update_data(with_instant_search=True)

        # Запускаем процесс создания фильтра
        await state.set_state(FilterSearchStates.waiting_for_filter_name)

        await callback.message.edit_text(
            "🎯 <b>Создание фильтра с мгновенным поиском</b>\n\n"
            "<b>Шаг 1/13:</b> Название фильтра\n\n"
            "Придумайте короткое название для вашего фильтра.\n"
            "Например: <i>IT оборудование</i>, <i>Медицинские товары</i>\n\n"
            "💡 Это название поможет вам управлять фильтрами в будущем.",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Error starting filter search: {e}", exc_info=True)
        await callback.message.answer("❌ Произошла ошибка. Попробуйте позже.")


@router.message(FilterSearchStates.waiting_for_filter_name)
async def process_filter_name_new(message: Message, state: FSMContext):
    """Обработка названия фильтра."""
    filter_name = message.text.strip()

    if not filter_name or len(filter_name) > 100:
        await message.answer(
            "⚠️ Название должно быть от 1 до 100 символов. Попробуйте еще раз:"
        )
        return

    await state.update_data(filter_name=filter_name)
    await state.set_state(FilterSearchStates.waiting_for_keywords)

    await message.answer(
        f"✅ Название: <b>{filter_name}</b>\n\n"
        f"<b>Шаг 2/13:</b> Ключевые слова\n\n"
        f"Введите ключевые слова через запятую.\n"
        f"Например: <i>компьютеры, ноутбуки, серверы</i>\n\n"
        f"🤖 <b>AI автоматически расширит ваш запрос</b>\n"
        f"Система добавит синонимы и связанные термины для более точного поиска.",
        parse_mode="HTML"
    )


@router.message(FilterSearchStates.waiting_for_keywords)
async def process_keywords_new(message: Message, state: FSMContext):
    """Обработка ключевых слов."""
    keywords_input = message.text.strip()

    if not keywords_input:
        await message.answer("⚠️ Введите хотя бы одно ключевое слово:")
        return

    # Парсим ключевые слова
    keywords = [kw.strip() for kw in keywords_input.split(',') if kw.strip()]

    if len(keywords) > 20:
        await message.answer("⚠️ Максимум 20 ключевых слов. Попробуйте еще раз:")
        return

    await state.update_data(keywords=keywords)
    await state.set_state(FilterSearchStates.waiting_for_exclude_keywords)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ Пропустить", callback_data="skip_exclude_keywords")]
    ])

    await message.answer(
        f"✅ Ключевые слова: <b>{', '.join(keywords)}</b>\n\n"
        f"<b>Шаг 3/13:</b> Исключающие слова\n\n"
        f"Введите слова, которые НЕ должны быть в тендере:\n"
        f"Например: <i>ремонт, б/у, аренда, лизинг</i>\n\n"
        f"Или нажмите «Пропустить»",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "skip_exclude_keywords")
async def skip_exclude_keywords(callback: CallbackQuery, state: FSMContext):
    """Пропуск исключающих слов."""
    await callback.answer()
    await state.update_data(exclude_keywords=[])
    await ask_for_price_range(callback.message, state)


@router.message(FilterSearchStates.waiting_for_exclude_keywords)
async def process_exclude_keywords(message: Message, state: FSMContext):
    """Обработка исключающих слов."""
    exclude_input = message.text.strip()

    if exclude_input:
        exclude_keywords = [kw.strip() for kw in exclude_input.split(',') if kw.strip()]
    else:
        exclude_keywords = []

    await state.update_data(exclude_keywords=exclude_keywords)
    await ask_for_price_range(message, state)


async def ask_for_price_range(message: Message, state: FSMContext):
    """Запрос ценового диапазона."""
    await state.set_state(FilterSearchStates.waiting_for_price_range)

    data = await state.get_data()
    exclude_text = f"❌ Исключаем: {', '.join(data.get('exclude_keywords', []))}\n\n" if data.get('exclude_keywords') else ""

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ Любая цена", callback_data="skip_price_range")]
    ])

    await message.answer(
        f"{exclude_text}"
        f"<b>Шаг 4/13:</b> Ценовой диапазон\n\n"
        f"Введите диапазон цен в формате: <code>мин макс</code>\n"
        f"Например: <code>100000 5000000</code> (от 100 тыс до 5 млн)\n\n"
        f"Или нажмите «Любая цена»",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "skip_price_range")
async def skip_price_range(callback: CallbackQuery, state: FSMContext):
    """Пропуск ценового диапазона."""
    await callback.answer()
    await state.update_data(price_min=None, price_max=None)
    await ask_for_regions(callback.message, state)


@router.message(FilterSearchStates.waiting_for_price_range)
async def process_price_range_new(message: Message, state: FSMContext):
    """Обработка ценового диапазона."""
    price_input = message.text.strip()

    price_min = None
    price_max = None

    if price_input != "0":
        parts = price_input.split()
        if len(parts) == 2:
            try:
                price_min = int(parts[0])
                price_max = int(parts[1])

                if price_min < 0 or price_max < 0 or price_min > price_max:
                    await message.answer("⚠️ Некорректный диапазон. Попробуйте еще раз:")
                    return
            except ValueError:
                await message.answer("⚠️ Введите числа в формате: <code>мин макс</code>", parse_mode="HTML")
                return
        else:
            await message.answer("⚠️ Введите два числа через пробел или нажмите «Любая цена»", parse_mode="HTML")
            return

    await state.update_data(price_min=price_min, price_max=price_max)
    await ask_for_regions(message, state)


async def ask_for_regions(message: Message, state: FSMContext):
    """Запрос региона."""
    await state.set_state(FilterSearchStates.waiting_for_regions)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏙️ Москва", callback_data="region_Москва")],
        [InlineKeyboardButton(text="🏛️ Санкт-Петербург", callback_data="region_Санкт-Петербург")],
        [InlineKeyboardButton(text="🏘️ Московская область", callback_data="region_Московская область")],
        [InlineKeyboardButton(text="🌴 Краснодарский край", callback_data="region_Краснодарский край")],
        [InlineKeyboardButton(text="🌍 Все регионы", callback_data="region_all")],
        [InlineKeyboardButton(text="✍️ Ввести вручную", callback_data="region_custom")]
    ])

    await message.answer(
        f"<b>Шаг 5/13:</b> Регион заказчика\n\n"
        f"Выберите регион или введите название вручную:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("region_"))
async def process_region_callback(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора региона."""
    await callback.answer()

    region_value = callback.data.replace("region_", "")

    if region_value == "all":
        await state.update_data(regions=[])
        await ask_for_law_type(callback.message, state)
    elif region_value == "custom":
        await callback.message.answer(
            "Введите название региона:\n"
            "Например: <i>Новосибирская область</i>",
            parse_mode="HTML"
        )
    else:
        await state.update_data(regions=[region_value])
        await ask_for_law_type(callback.message, state)


@router.message(FilterSearchStates.waiting_for_regions)
async def process_region_text(message: Message, state: FSMContext):
    """Обработка текстового ввода региона."""
    region = message.text.strip()
    if region:
        await state.update_data(regions=[region])
    else:
        await state.update_data(regions=[])
    await ask_for_law_type(message, state)


async def ask_for_law_type(message: Message, state: FSMContext):
    """Запрос типа закона."""
    await state.set_state(FilterSearchStates.waiting_for_law_type)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 44-ФЗ (госзакупки)", callback_data="law_44")],
        [InlineKeyboardButton(text="📋 223-ФЗ (корпоративные)", callback_data="law_223")],
        [InlineKeyboardButton(text="📚 Оба закона", callback_data="law_all")]
    ])

    await message.answer(
        f"<b>Шаг 6/13:</b> Тип закона\n\n"
        f"<b>44-ФЗ</b> — государственные закупки (бюджетные организации)\n"
        f"<b>223-ФЗ</b> — закупки госкомпаний (Газпром, РЖД и др.)\n\n"
        f"Выберите:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("law_"))
async def process_law_type(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора типа закона."""
    await callback.answer()

    law_value = callback.data.replace("law_", "")
    law_type = None
    if law_value == "44":
        law_type = "44-ФЗ"
    elif law_value == "223":
        law_type = "223-ФЗ"
    # "all" оставляем None

    await state.update_data(law_type=law_type)
    await ask_for_purchase_stage(callback.message, state)


async def ask_for_purchase_stage(message: Message, state: FSMContext):
    """Запрос этапа закупки."""
    await state.set_state(FilterSearchStates.waiting_for_purchase_stage)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Только подача заявок (актуальные)", callback_data="stage_submission")],
        [InlineKeyboardButton(text="📊 Все этапы", callback_data="stage_all")]
    ])

    await message.answer(
        f"<b>Шаг 7/13:</b> Этап закупки\n\n"
        f"<b>Подача заявок</b> — можно подать заявку прямо сейчас\n"
        f"<b>Все этапы</b> — включая завершённые и на рассмотрении\n\n"
        f"💡 Рекомендуем «Только подача заявок»",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("stage_"))
async def process_purchase_stage(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора этапа закупки."""
    await callback.answer()

    stage_value = callback.data.replace("stage_", "")
    purchase_stage = "submission" if stage_value == "submission" else None

    await state.update_data(purchase_stage=purchase_stage)
    await ask_for_purchase_method(callback.message, state)


async def ask_for_purchase_method(message: Message, state: FSMContext):
    """Запрос способа закупки."""
    await state.set_state(FilterSearchStates.waiting_for_purchase_method)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔨 Электронный аукцион", callback_data="method_auction")],
        [InlineKeyboardButton(text="📋 Открытый конкурс", callback_data="method_tender")],
        [InlineKeyboardButton(text="💬 Запрос котировок", callback_data="method_quotation")],
        [InlineKeyboardButton(text="📝 Запрос предложений", callback_data="method_request")],
        [InlineKeyboardButton(text="🔍 Все способы", callback_data="method_all")]
    ])

    await message.answer(
        f"<b>Шаг 8/13:</b> Способ закупки\n\n"
        f"<b>Электронный аукцион</b> — побеждает минимальная цена\n"
        f"<b>Открытый конкурс</b> — оценка по критериям\n"
        f"<b>Запрос котировок</b> — до 3 млн руб\n"
        f"<b>Запрос предложений</b> — сложные закупки\n\n"
        f"Выберите:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("method_"))
async def process_purchase_method(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора способа закупки."""
    await callback.answer()

    method_value = callback.data.replace("method_", "")
    purchase_method = None if method_value == "all" else method_value

    await state.update_data(purchase_method=purchase_method)
    await ask_for_tender_type(callback.message, state)


async def ask_for_tender_type(message: Message, state: FSMContext):
    """Запрос типа закупки."""
    await state.set_state(FilterSearchStates.waiting_for_tender_type)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Товары (поставка)", callback_data="ttype_goods")],
        [InlineKeyboardButton(text="🔧 Услуги", callback_data="ttype_services")],
        [InlineKeyboardButton(text="🏗️ Работы", callback_data="ttype_works")],
        [InlineKeyboardButton(text="🔍 Все типы", callback_data="ttype_all")]
    ])

    await message.answer(
        f"<b>Шаг 9/13:</b> Тип закупки\n\n"
        f"<b>Товары</b> — поставка продукции\n"
        f"<b>Услуги</b> — обслуживание, консалтинг\n"
        f"<b>Работы</b> — строительство, ремонт\n\n"
        f"Выберите:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("ttype_"))
async def process_tender_type(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора типа закупки."""
    await callback.answer()

    ttype_value = callback.data.replace("ttype_", "")
    tender_types_map = {
        "goods": ["товары"],
        "services": ["услуги"],
        "works": ["работы"],
        "all": []
    }
    tender_types = tender_types_map.get(ttype_value, [])

    await state.update_data(tender_types=tender_types)
    await ask_for_min_deadline(callback.message, state)


async def ask_for_min_deadline(message: Message, state: FSMContext):
    """Запрос минимального количества дней до дедлайна."""
    await state.set_state(FilterSearchStates.waiting_for_min_deadline)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="3 дня", callback_data="deadline_3")],
        [InlineKeyboardButton(text="5 дней", callback_data="deadline_5")],
        [InlineKeyboardButton(text="7 дней", callback_data="deadline_7")],
        [InlineKeyboardButton(text="14 дней", callback_data="deadline_14")],
        [InlineKeyboardButton(text="⏭️ Без ограничений", callback_data="deadline_skip")]
    ])

    await message.answer(
        f"<b>Шаг 10/13:</b> Минимум дней до дедлайна\n\n"
        f"Сколько дней минимум должно оставаться до окончания подачи заявок?\n\n"
        f"💡 Это поможет отфильтровать тендеры, на которые не успеете подать заявку",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("deadline_"))
async def process_min_deadline(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора минимального дедлайна."""
    await callback.answer()

    deadline_value = callback.data.replace("deadline_", "")
    min_deadline_days = None if deadline_value == "skip" else int(deadline_value)

    await state.update_data(min_deadline_days=min_deadline_days)
    await ask_for_customer_keywords(callback.message, state)


async def ask_for_customer_keywords(message: Message, state: FSMContext):
    """Запрос ключевых слов в названии заказчика."""
    await state.set_state(FilterSearchStates.waiting_for_customer_keywords)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ Пропустить", callback_data="customer_skip")]
    ])

    await message.answer(
        f"<b>Шаг 11/13:</b> Фильтр по заказчику\n\n"
        f"Введите ключевые слова для фильтрации по названию заказчика:\n"
        f"Например: <i>больница, школа, университет</i>\n\n"
        f"Или нажмите «Пропустить» для поиска среди всех заказчиков",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "customer_skip")
async def skip_customer_keywords(callback: CallbackQuery, state: FSMContext):
    """Пропуск фильтра по заказчику."""
    await callback.answer()
    await state.update_data(customer_keywords=[])
    await ask_for_okpd2(callback.message, state)


@router.message(FilterSearchStates.waiting_for_customer_keywords)
async def process_customer_keywords(message: Message, state: FSMContext):
    """Обработка ключевых слов заказчика."""
    customer_input = message.text.strip()

    if customer_input:
        customer_keywords = [kw.strip() for kw in customer_input.split(',') if kw.strip()]
    else:
        customer_keywords = []

    await state.update_data(customer_keywords=customer_keywords)
    await ask_for_okpd2(message, state)


async def ask_for_okpd2(message: Message, state: FSMContext):
    """Запрос кода ОКПД2."""
    await state.set_state(FilterSearchStates.waiting_for_okpd2)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💻 26 - Компьютеры и электроника", callback_data="okpd_26")],
        [InlineKeyboardButton(text="🏗️ 41-43 - Строительство", callback_data="okpd_41")],
        [InlineKeyboardButton(text="🚗 29 - Автотранспорт", callback_data="okpd_29")],
        [InlineKeyboardButton(text="💊 21 - Лекарства", callback_data="okpd_21")],
        [InlineKeyboardButton(text="🍞 10 - Продукты питания", callback_data="okpd_10")],
        [InlineKeyboardButton(text="✍️ Ввести код вручную", callback_data="okpd_custom")],
        [InlineKeyboardButton(text="⏭️ Пропустить", callback_data="okpd_skip")]
    ])

    await message.answer(
        f"<b>Шаг 12/13:</b> Код ОКПД2\n\n"
        f"ОКПД2 — классификатор продукции для точного поиска.\n\n"
        f"Выберите категорию или введите код вручную:\n"
        f"Например: <code>26.20</code> (компьютеры)\n\n"
        f"💡 Можете пропустить для поиска по всем категориям",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("okpd_"))
async def process_okpd2_callback(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора ОКПД2."""
    await callback.answer()

    okpd_value = callback.data.replace("okpd_", "")

    if okpd_value == "skip":
        await state.update_data(okpd2_codes=[])
        await ask_for_tender_count(callback.message, state)
    elif okpd_value == "custom":
        await callback.message.answer(
            "Введите код ОКПД2:\n"
            "Например: <code>26.20</code> или <code>26.20.1</code>\n\n"
            "Можно ввести несколько кодов через запятую",
            parse_mode="HTML"
        )
    else:
        # Популярные категории
        okpd_map = {
            "26": ["26"],  # Компьютеры и электроника
            "41": ["41", "42", "43"],  # Строительство
            "29": ["29"],  # Автотранспорт
            "21": ["21"],  # Лекарства
            "10": ["10"],  # Продукты питания
        }
        okpd2_codes = okpd_map.get(okpd_value, [okpd_value])
        await state.update_data(okpd2_codes=okpd2_codes)
        await ask_for_tender_count(callback.message, state)


@router.message(FilterSearchStates.waiting_for_okpd2)
async def process_okpd2_text(message: Message, state: FSMContext):
    """Обработка текстового ввода ОКПД2."""
    okpd_input = message.text.strip()

    if okpd_input:
        okpd2_codes = [code.strip() for code in okpd_input.split(',') if code.strip()]
    else:
        okpd2_codes = []

    await state.update_data(okpd2_codes=okpd2_codes)
    await ask_for_tender_count(message, state)


async def ask_for_tender_count(message: Message, state: FSMContext):
    """Запрос количества тендеров."""
    await state.set_state(FilterSearchStates.waiting_for_tender_count)

    await message.answer(
        f"<b>Шаг 13/13:</b> Количество тендеров\n\n"
        f"Сколько тендеров найти?\n"
        f"Введите число от <code>1</code> до <code>25</code>\n\n"
        f"💡 Рекомендуем 10-15 для быстрого результата",
        parse_mode="HTML"
    )


@router.message(FilterSearchStates.waiting_for_tender_count)
async def process_tender_count(message: Message, state: FSMContext):
    """Обработка количества тендеров."""
    try:
        count = int(message.text.strip())
        if not (1 <= count <= 25):
            await message.answer("⚠️ Введите число от 1 до 25:")
            return
    except ValueError:
        await message.answer("⚠️ Введите число:")
        return

    await state.update_data(tender_count=count)

    # Получаем все данные
    data = await state.get_data()
    with_instant_search = data.get('with_instant_search', True)

    # Показываем прогресс
    if with_instant_search:
        progress_msg = await message.answer(
            "🔄 <b>Обработка вашего запроса...</b>\n\n"
            "⏳ Шаг 1/4: Сохранение фильтра...",
            parse_mode="HTML"
        )
    else:
        progress_msg = await message.answer(
            "🔄 <b>Создание фильтра...</b>\n\n"
            "⏳ Сохранение...",
            parse_mode="HTML"
        )

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(message.from_user.id)

        # 1. Сохраняем фильтр в БД с новыми критериями
        # active=0 для with_instant_search (требует подтверждения)
        # active=1 для прямого создания (сразу активен)
        filter_id = await db.create_filter(
            user_id=user['id'],
            name=data['filter_name'],
            keywords=data['keywords'],
            exclude_keywords=data.get('exclude_keywords', []),
            price_min=data.get('price_min'),
            price_max=data.get('price_max'),
            regions=data.get('regions', []),
            tender_types=data.get('tender_types', []),
            law_type=data.get('law_type'),
            purchase_stage=data.get('purchase_stage'),
            purchase_method=data.get('purchase_method'),
            okpd2_codes=data.get('okpd2_codes', []),
            min_deadline_days=data.get('min_deadline_days'),
            customer_keywords=data.get('customer_keywords', []),
            active=0 if with_instant_search else 1  # Активен только если без поиска
        )

        # РЕЖИМ 1: С мгновенным поиском
        if with_instant_search:
            # 2. AI расширение критериев
            await progress_msg.edit_text(
                "🔄 <b>Обработка вашего запроса...</b>\n\n"
                "✅ Шаг 1/4: Фильтр сохранен\n"
                "⏳ Шаг 2/4: AI расширяет критерии поиска...",
                parse_mode="HTML"
            )

            expander = QueryExpander()
            expansion = await expander.expand_keywords(data['keywords'])
            expanded_keywords = expansion.get('expanded_keywords', [])

            # 3. Мгновенный поиск
            await progress_msg.edit_text(
                "🔄 <b>Обработка вашего запроса...</b>\n\n"
                "✅ Шаг 1/4: Фильтр сохранен\n"
                "✅ Шаг 2/4: AI расширил запрос (+{} терминов)\n"
                "⏳ Шаг 3/4: Поиск тендеров на zakupki.gov.ru...".format(len(expanded_keywords)),
                parse_mode="HTML"
            )

            searcher = InstantSearch()
            filter_data = {
                'id': filter_id,
                'name': data['filter_name'],
                'keywords': json.dumps(data['keywords'], ensure_ascii=False),
                'exclude_keywords': json.dumps(data.get('exclude_keywords', []), ensure_ascii=False),
                'price_min': data.get('price_min'),
                'price_max': data.get('price_max'),
                'regions': json.dumps(data.get('regions', []), ensure_ascii=False),
                'tender_types': json.dumps(data.get('tender_types', []), ensure_ascii=False),
                'law_type': data.get('law_type'),
                'purchase_stage': data.get('purchase_stage'),
                'purchase_method': data.get('purchase_method'),
                'okpd2_codes': json.dumps(data.get('okpd2_codes', []), ensure_ascii=False),
                'min_deadline_days': data.get('min_deadline_days'),
                'customer_keywords': json.dumps(data.get('customer_keywords', []), ensure_ascii=False),
            }

            search_results = await searcher.search_by_filter(
                filter_data=filter_data,
                max_tenders=count,
                expanded_keywords=expanded_keywords
            )

            # 4. Генерация HTML отчета
            await progress_msg.edit_text(
                "🔄 <b>Обработка вашего запроса...</b>\n\n"
                "✅ Шаг 1/4: Фильтр сохранен\n"
                "✅ Шаг 2/4: AI расширил запрос (+{} терминов)\n"
                "✅ Шаг 3/4: Найдено {} тендеров\n"
                "⏳ Шаг 4/4: Генерация HTML отчета...".format(
                    len(expanded_keywords),
                    search_results['total_found']
                ),
                parse_mode="HTML"
            )

            report_path = await searcher.generate_html_report(
                search_results=search_results,
                filter_data=filter_data
            )

            # Получаем лимиты тарифа для отображения
            plan_limits = await get_plan_limits(db.db_path, user['subscription_tier'])

            # Отправляем результаты
            await progress_msg.edit_text(
                "✅ <b>Готово!</b>\n\n"
                f"📊 Найдено тендеров: {search_results['total_found']}\n"
                f"🎯 Релевантных: {len(search_results['matches'])}\n"
                f"🔥 Отличных (≥70): {search_results['stats'].get('high_score_count', 0)}\n\n"
                f"📄 Отправляю HTML отчет...",
                parse_mode="HTML"
            )

            # Отправляем HTML файл
            await message.answer_document(
                document=FSInputFile(report_path),
                caption=(
                    f"📊 <b>Результаты поиска</b>\n\n"
                    f"Фильтр: <b>{data['filter_name']}</b>\n"
                    f"Найдено: {search_results['total_found']} тендеров\n\n"
                    f"🤖 AI расширил ваш запрос с {len(data['keywords'])} до {len(data['keywords']) + len(expanded_keywords)} терминов"
                ),
                parse_mode="HTML"
            )

            # Предлагаем включить автоматический мониторинг
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="🔔 Включить автомониторинг",
                    callback_data=f"enable_monitoring_{filter_id}"
                )],
                [InlineKeyboardButton(
                    text="📋 Мои фильтры",
                    callback_data="sniper_my_filters"
                )],
                [InlineKeyboardButton(
                    text="🎯 Новый поиск",
                    callback_data="sniper_new_search"
                )]
            ])

            await message.answer(
                "💡 <b>Хотите получать автоматические уведомления?</b>\n\n"
                "Включите автоматический мониторинг, и бот будет присылать вам\n"
                "уведомления о новых тендерах по этим критериям каждые 5 минут.\n\n"
                f"🆓 Ваш лимит: {plan_limits.get('max_notifications_daily', 10)} уведомлений в день",
                reply_markup=keyboard,
                parse_mode="HTML"
            )

            await state.clear()

        # РЕЖИМ 2: Без мгновенного поиска (просто сохраняем фильтр)
        else:
            await progress_msg.edit_text(
                "✅ <b>Фильтр создан!</b>",
                parse_mode="HTML"
            )

            # Получаем лимиты
            plan_limits = await get_plan_limits(db.db_path, user['subscription_tier'])

            # Формируем описание фильтра
            filter_summary = f"📝 <b>{data['filter_name']}</b>\n\n"
            filter_summary += f"🔑 Ключевые слова: {', '.join(data['keywords'])}\n"

            if data.get('price_min') or data.get('price_max'):
                price_min = f"{data.get('price_min'):,}" if data.get('price_min') else "0"
                price_max = f"{data.get('price_max'):,}" if data.get('price_max') else "∞"
                filter_summary += f"💰 Цена: {price_min} - {price_max} ₽\n"

            if data.get('regions'):
                filter_summary += f"📍 Регионы: {', '.join(data['regions'])}\n"

            if data.get('min_deadline_days'):
                filter_summary += f"⏰ Минимум дней до дедлайна: {data['min_deadline_days']}\n"

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
                [InlineKeyboardButton(text="🎯 Главное меню", callback_data="sniper_menu")]
            ])

            await message.answer(
                f"✅ <b>Фильтр успешно создан и активирован!</b>\n\n"
                f"{filter_summary}\n"
                f"🔔 <b>Автоматический мониторинг включен</b>\n\n"
                f"Вы будете получать уведомления о новых подходящих тендерах каждые 5 минут.\n\n"
                f"🆓 Ваш лимит: {plan_limits.get('max_notifications_daily', 10)} уведомлений в день",
                reply_markup=keyboard,
                parse_mode="HTML"
            )

            await state.clear()

    except Exception as e:
        logger.error(f"Error in filter search: {e}", exc_info=True)
        await progress_msg.edit_text(
            f"❌ <b>Ошибка при поиске</b>\n\n"
            f"Произошла ошибка: {str(e)}\n\n"
            f"Попробуйте позже или измените критерии.",
            parse_mode="HTML"
        )
        await state.clear()


@router.callback_query(F.data.startswith("enable_monitoring_"))
async def enable_auto_monitoring(callback: CallbackQuery):
    """Включение автоматического мониторинга для фильтра."""
    await callback.answer()

    filter_id = int(callback.data.split('_')[-1])

    try:
        db = await get_sniper_db()

        # Активируем фильтр (включаем мониторинг)
        await db.activate_filter(filter_id)

        logger.info(f"✅ Фильтр {filter_id} активирован пользователем {callback.from_user.id}")

        await callback.message.edit_text(
            "✅ <b>Автоматический мониторинг включен!</b>\n\n"
            "🔔 Теперь вы будете получать уведомления о новых тендерах,\n"
            "соответствующих вашим критериям.\n\n"
            "Проверка новых тендеров происходит каждые 5 минут.\n\n"
            "Управлять фильтрами можно в разделе \"Мои фильтры\".",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
                [InlineKeyboardButton(text="« В главное меню", callback_data="sniper_menu")]
            ])
        )

    except Exception as e:
        logger.error(f"Error enabling monitoring for filter {filter_id}: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка. Попробуйте позже.")
