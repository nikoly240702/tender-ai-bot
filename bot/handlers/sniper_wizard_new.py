"""
Simplified Wizard - Упрощённый wizard создания фильтров (3-5 шагов вместо 14).

Процесс:
1. Выбор отрасли (industry templates с готовыми настройками)
2. Ввод ключевых слов (с suggestions от отрасли)
3. Опциональные уточнения (бюджет, регионы, исключения)
4. Создание фильтра + мгновенный поиск

Feature flag: simplified_wizard (config/features.yaml)
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

from tender_sniper.database import get_sniper_db
from tender_sniper.config import is_new_feature_enabled
from tender_sniper.query_expander import QueryExpander
from tender_sniper.instant_search import InstantSearch
from tender_sniper.regions import (
    get_all_federal_districts,
    get_regions_by_district,
    parse_regions_input,
    format_regions_list
)

logger = logging.getLogger(__name__)

router = Router()


# ============================================
# INDUSTRY TEMPLATES
# ============================================

INDUSTRY_TEMPLATES = {
    'IT': {
        'icon': '💻',
        'name': 'IT и Телеком',
        'suggestions': [
            'Серверы и СХД',
            'Лицензии Microsoft',
            'Компьютеры и ноутбуки',
            'Сетевое оборудование',
            'ПО и подписки'
        ],
        'default_excluded_words': ['медицин', 'строител', 'транспорт', 'продукты питания'],
        'default_price_min': 100000,
        'default_price_max': 10000000,
        'default_keywords': ['компьютер', 'сервер', 'ноутбук', 'ПО', 'лицензия'],
    },
    'construction': {
        'icon': '🏗',
        'name': 'Строительство',
        'suggestions': [
            'СМР (строительно-монтажные работы)',
            'Стройматериалы',
            'Проектирование',
            'Ремонт зданий',
            'Благоустройство территории'
        ],
        'default_excluded_words': ['компьютер', 'ПО', 'лицензи', 'медицин'],
        'default_price_min': 500000,
        'default_price_max': 50000000,
        'default_keywords': ['строительство', 'ремонт', 'СМР', 'материалы'],
    },
    'medicine': {
        'icon': '⚕️',
        'name': 'Медицина',
        'suggestions': [
            'Медицинские изделия',
            'Лекарственные препараты',
            'Медицинское оборудование',
            'Расходные материалы',
            'Медицинские услуги'
        ],
        'default_excluded_words': ['строител', 'компьютер', 'транспорт'],
        'default_price_min': 50000,
        'default_price_max': 5000000,
        'default_keywords': ['медицинские', 'лекарства', 'оборудование'],
    },
    'industry': {
        'icon': '🏭',
        'name': 'Промышленность',
        'suggestions': [
            'Промышленное оборудование',
            'Запчасти и комплектующие',
            'Инструменты',
            'Техническое обслуживание',
            'Промышленная автоматика'
        ],
        'default_excluded_words': ['медицин', 'ПО', 'лицензи'],
        'default_price_min': 200000,
        'default_price_max': 20000000,
        'default_keywords': ['оборудование', 'запчасти', 'обслуживание'],
    },
    'transport': {
        'icon': '🚗',
        'name': 'Транспорт',
        'suggestions': [
            'Автомобили',
            'Спецтехника',
            'Запчасти',
            'ГСМ (горюче-смазочные материалы)',
            'Транспортные услуги'
        ],
        'default_excluded_words': ['компьютер', 'медицин', 'строител'],
        'default_price_min': 500000,
        'default_price_max': 30000000,
        'default_keywords': ['автомобиль', 'техника', 'ГСМ', 'транспорт'],
    },
    'services': {
        'icon': '📝',
        'name': 'Услуги',
        'suggestions': [
            'Консультационные услуги',
            'Обучение и тренинги',
            'Охрана и безопасность',
            'Клининг',
            'IT-аутсорсинг'
        ],
        'default_excluded_words': [],
        'default_price_min': 50000,
        'default_price_max': 5000000,
        'default_keywords': ['услуги', 'обслуживание', 'сервис'],
    },
    'other': {
        'icon': '📦',
        'name': 'Прочее',
        'suggestions': [
            'Канцелярские товары',
            'Мебель',
            'Хозтовары',
            'Продукты питания',
            'Текстиль'
        ],
        'default_excluded_words': [],
        'default_price_min': 50000,
        'default_price_max': 3000000,
        'default_keywords': ['поставка', 'товары'],
    },
}


# ============================================
# FSM States для упрощённого wizard
# ============================================

class SimplifiedWizardStates(StatesGroup):
    """Состояния для упрощённого wizard (3-5 шагов)."""
    select_industry = State()       # Шаг 1: Выбор отрасли
    enter_keywords = State()        # Шаг 2: Ключевые слова
    refine_filter = State()         # Шаг 3: Уточнение (опционально)
    enter_price_min = State()       # Шаг 3a: Минимальный бюджет
    enter_price_max = State()       # Шаг 3b: Максимальный бюджет
    select_region = State()         # Шаг 3c: Регион
    enter_excluded = State()        # Шаг 3d: Исключить слова
    confirm_create = State()        # Шаг 4: Подтверждение


# ============================================
# HELPER FUNCTIONS
# ============================================

def format_price(price: Optional[float]) -> str:
    """Форматирование цены в читаемый вид."""
    if price is None:
        return "не указано"
    if price >= 1_000_000:
        return f"{price / 1_000_000:.1f} млн ₽"
    elif price >= 1_000:
        return f"{price / 1_000:.0f} тыс ₽"
    else:
        return f"{price:.0f} ₽"


def get_industry_keyboard(selected: Optional[str] = None) -> InlineKeyboardMarkup:
    """Создать клавиатуру выбора отрасли."""
    keyboard = []
    row = []

    for industry_code, industry in INDUSTRY_TEMPLATES.items():
        emoji = "✅ " if industry_code == selected else ""
        text = f"{emoji}{industry['icon']} {industry['name']}"
        row.append(InlineKeyboardButton(
            text=text,
            callback_data=f"sw_industry:{industry_code}"
        ))

        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton(text="➡️ Пропустить - свой фильтр", callback_data="sw_industry:custom")
    ])
    keyboard.append([
        InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_suggestions_keyboard(industry_code: str) -> InlineKeyboardMarkup:
    """Создать клавиатуру с suggestions для отрасли."""
    industry = INDUSTRY_TEMPLATES.get(industry_code, {})
    suggestions = industry.get('suggestions', [])

    keyboard = []
    for suggestion in suggestions:
        keyboard.append([
            InlineKeyboardButton(
                text=f"📌 {suggestion}",
                callback_data=f"sw_suggest:{suggestion[:50]}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(text="✍️ Ввести свои слова", callback_data="sw_custom_keywords")
    ])
    keyboard.append([
        InlineKeyboardButton(text="« Назад", callback_data="sw_back_to_industry")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_refinement_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для опциональных уточнений."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Уточнить бюджет", callback_data="sw_refine:budget")],
        [InlineKeyboardButton(text="📍 Выбрать регионы", callback_data="sw_refine:region")],
        [InlineKeyboardButton(text="🚫 Исключить слова", callback_data="sw_refine:exclude")],
        [InlineKeyboardButton(
            text="🚀 Создать фильтр (стандартные настройки)",
            callback_data="sw_create_filter"
        )],
        [InlineKeyboardButton(text="« Назад", callback_data="sw_back_to_keywords")],
    ])


def get_region_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора региона."""
    federal_districts = get_all_federal_districts()

    keyboard = []
    for fd_code, fd_name in federal_districts.items():
        keyboard.append([
            InlineKeyboardButton(
                text=f"🗺 {fd_name}",
                callback_data=f"sw_fd:{fd_code}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(text="🌍 Вся Россия", callback_data="sw_region:all")
    ])
    keyboard.append([
        InlineKeyboardButton(text="« Назад", callback_data="sw_back_to_refine")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ============================================
# WIZARD HANDLERS
# ============================================

@router.callback_query(F.data == "sniper_new_search")
async def start_simplified_wizard(callback: CallbackQuery, state: FSMContext):
    """
    Начало упрощённого wizard.
    Вызывается из главного меню Sniper.
    """
    await callback.answer()

    # Проверяем feature flag
    if not is_new_feature_enabled('simplified_wizard'):
        # Fallback на старый wizard
        from bot.handlers.sniper_search import start_search_with_ai
        await start_search_with_ai(callback, state)
        return

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
        filters = await db.get_user_filters(user['id'], active_only=True)
        max_filters = 5 if user['subscription_tier'] == 'free' else 15

        if len(filters) >= max_filters:
            await callback.message.edit_text(
                f"⚠️ <b>Достигнут лимит фильтров</b>\n\n"
                f"Ваш тариф <b>{user['subscription_tier'].title()}</b> позволяет создать максимум {max_filters} фильтров.\n"
                f"У вас уже создано: {len(filters)}\n\n"
                f"Удалите старые фильтры или обновите подписку.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_filters")],
                    [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")]
                ])
            )
            return

        # Очищаем state
        await state.clear()
        await state.set_state(SimplifiedWizardStates.select_industry)

        await callback.message.edit_text(
            "🎯 <b>Быстрое создание фильтра</b>\n\n"
            "<b>Шаг 1/3:</b> Выберите вашу отрасль\n\n"
            "Это поможет подобрать оптимальные настройки поиска.\n"
            "Или нажмите «Пропустить» для ручной настройки.",
            parse_mode="HTML",
            reply_markup=get_industry_keyboard()
        )

    except Exception as e:
        logger.error(f"Error starting simplified wizard: {e}", exc_info=True)
        await callback.message.answer("❌ Произошла ошибка. Попробуйте позже.")


@router.callback_query(F.data.startswith("sw_industry:"))
async def handle_industry_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора отрасли."""
    await callback.answer()

    industry_code = callback.data.split(":")[1]

    if industry_code == "custom":
        # Пользователь хочет свой фильтр без шаблона
        await state.update_data(
            industry=None,
            with_template=False
        )
        await state.set_state(SimplifiedWizardStates.enter_keywords)

        await callback.message.edit_text(
            "🎯 <b>Создание фильтра</b>\n\n"
            "<b>Шаг 2/3:</b> Введите ключевые слова\n\n"
            "Укажите через запятую, что вы ищете.\n"
            "Например: <i>компьютеры, серверы, Dell</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="sw_back_to_industry")]
            ])
        )
        return

    # Выбрана отрасль
    industry = INDUSTRY_TEMPLATES.get(industry_code, {})

    await state.update_data(
        industry=industry_code,
        with_template=True,
        default_excluded_words=industry.get('default_excluded_words', []),
        default_price_min=industry.get('default_price_min'),
        default_price_max=industry.get('default_price_max'),
    )
    await state.set_state(SimplifiedWizardStates.enter_keywords)

    suggestions_text = "\n".join([f"• {s}" for s in industry.get('suggestions', [])])

    await callback.message.edit_text(
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"<b>Отрасль:</b> {industry['icon']} {industry['name']}\n\n"
        f"<b>Шаг 2/3:</b> Что вы ищете?\n\n"
        f"Популярные запросы:\n{suggestions_text}\n\n"
        f"Выберите готовый вариант или введите свои слова:",
        parse_mode="HTML",
        reply_markup=get_suggestions_keyboard(industry_code)
    )


@router.callback_query(F.data == "sw_back_to_industry")
async def back_to_industry(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору отрасли."""
    await callback.answer()
    await state.set_state(SimplifiedWizardStates.select_industry)

    await callback.message.edit_text(
        "🎯 <b>Быстрое создание фильтра</b>\n\n"
        "<b>Шаг 1/3:</b> Выберите вашу отрасль\n\n"
        "Это поможет подобрать оптимальные настройки поиска.",
        parse_mode="HTML",
        reply_markup=get_industry_keyboard()
    )


@router.callback_query(F.data.startswith("sw_suggest:"))
async def handle_suggestion_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора готового suggestion."""
    await callback.answer()

    suggestion = callback.data.split(":", 1)[1]

    # Парсим suggestion как keywords
    keywords = [kw.strip() for kw in suggestion.replace("(", ",").replace(")", "").split(",") if kw.strip()]

    await state.update_data(keywords=keywords, filter_name=suggestion[:100])
    await state.set_state(SimplifiedWizardStates.refine_filter)

    await callback.message.edit_text(
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ <b>Ключевые слова:</b> {suggestion}\n\n"
        f"<b>Шаг 3/3:</b> Хотите уточнить фильтр?\n\n"
        f"<i>Текущие настройки:</i>\n"
        f"💰 Бюджет: <b>без ограничений</b>\n"
        f"🌍 Регион: <b>Вся Россия</b>\n"
        f"🚫 Исключения: <b>не заданы</b>\n\n"
        f"Можете уточнить или сразу создать фильтр.",
        parse_mode="HTML",
        reply_markup=get_refinement_keyboard()
    )


@router.callback_query(F.data == "sw_custom_keywords")
async def prompt_custom_keywords(callback: CallbackQuery, state: FSMContext):
    """Запрос ввода своих ключевых слов."""
    await callback.answer()
    await state.set_state(SimplifiedWizardStates.enter_keywords)

    await callback.message.edit_text(
        "🎯 <b>Создание фильтра</b>\n\n"
        "<b>Шаг 2/3:</b> Введите ключевые слова\n\n"
        "Укажите через запятую, что вы ищете.\n"
        "Например: <i>компьютеры, серверы, Dell</i>\n\n"
        "💡 Можно указать бренды, модели, или общие категории.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="sw_back_to_industry")]
        ])
    )


@router.message(SimplifiedWizardStates.enter_keywords)
async def handle_keywords_input(message: Message, state: FSMContext):
    """Обработка ввода ключевых слов."""
    text = message.text.strip()

    if len(text) < 2:
        await message.answer(
            "⚠️ Введите хотя бы одно ключевое слово.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="sw_back_to_industry")]
            ])
        )
        return

    # Парсим keywords
    keywords = [kw.strip() for kw in text.split(",") if kw.strip()]

    if not keywords:
        await message.answer(
            "⚠️ Не удалось распознать ключевые слова. Попробуйте ещё раз.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="sw_back_to_industry")]
            ])
        )
        return

    # Генерируем название фильтра
    filter_name = ", ".join(keywords[:3])
    if len(keywords) > 3:
        filter_name += f" +{len(keywords) - 3}"

    await state.update_data(keywords=keywords, filter_name=filter_name)
    await state.set_state(SimplifiedWizardStates.refine_filter)

    await message.answer(
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ <b>Ключевые слова:</b> {', '.join(keywords)}\n\n"
        f"<b>Шаг 3/3:</b> Хотите уточнить фильтр?\n\n"
        f"<i>Текущие настройки:</i>\n"
        f"💰 Бюджет: <b>без ограничений</b>\n"
        f"🌍 Регион: <b>Вся Россия</b>\n"
        f"🚫 Исключения: <b>не заданы</b>\n\n"
        f"Можете уточнить или сразу создать фильтр.",
        parse_mode="HTML",
        reply_markup=get_refinement_keyboard()
    )


@router.callback_query(F.data == "sw_back_to_keywords")
async def back_to_keywords(callback: CallbackQuery, state: FSMContext):
    """Возврат к вводу ключевых слов."""
    await callback.answer()

    data = await state.get_data()
    industry_code = data.get('industry')

    if industry_code:
        await state.set_state(SimplifiedWizardStates.enter_keywords)
        await callback.message.edit_text(
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"<b>Шаг 2/3:</b> Что вы ищете?\n\n"
            f"Выберите готовый вариант или введите свои слова:",
            parse_mode="HTML",
            reply_markup=get_suggestions_keyboard(industry_code)
        )
    else:
        await state.set_state(SimplifiedWizardStates.enter_keywords)
        await callback.message.edit_text(
            "🎯 <b>Создание фильтра</b>\n\n"
            "<b>Шаг 2/3:</b> Введите ключевые слова\n\n"
            "Укажите через запятую, что вы ищете.\n"
            "Например: <i>компьютеры, серверы, Dell</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="sw_back_to_industry")]
            ])
        )


# ============================================
# REFINEMENT HANDLERS
# ============================================

@router.callback_query(F.data == "sw_refine:budget")
async def refine_budget(callback: CallbackQuery, state: FSMContext):
    """Уточнение бюджета - минимальная сумма."""
    await callback.answer()
    await state.set_state(SimplifiedWizardStates.enter_price_min)

    await callback.message.edit_text(
        "💰 <b>Уточнение бюджета</b>\n\n"
        "Введите <b>минимальную</b> сумму контракта (в рублях).\n\n"
        "Примеры:\n"
        "• 100000 (100 тыс)\n"
        "• 1000000 (1 млн)\n"
        "• 0 (без ограничения)\n\n"
        "Или нажмите «Пропустить».",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="sw_skip_price_min")],
            [InlineKeyboardButton(text="« Назад", callback_data="sw_back_to_refine")]
        ])
    )


@router.message(SimplifiedWizardStates.enter_price_min)
async def handle_price_min_input(message: Message, state: FSMContext):
    """Обработка ввода минимальной цены."""
    text = message.text.strip().replace(" ", "").replace(",", "")

    try:
        price_min = int(text)
        if price_min < 0:
            raise ValueError("Negative price")
    except ValueError:
        await message.answer(
            "⚠️ Введите число. Например: 100000",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏭ Пропустить", callback_data="sw_skip_price_min")],
                [InlineKeyboardButton(text="« Назад", callback_data="sw_back_to_refine")]
            ])
        )
        return

    await state.update_data(price_min=price_min)
    await state.set_state(SimplifiedWizardStates.enter_price_max)

    await message.answer(
        f"✅ Минимум: <b>{format_price(price_min)}</b>\n\n"
        f"Теперь введите <b>максимальную</b> сумму контракта.\n\n"
        f"Или нажмите «Пропустить» (без ограничения).",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="sw_skip_price_max")],
            [InlineKeyboardButton(text="« Назад", callback_data="sw_refine:budget")]
        ])
    )


@router.callback_query(F.data == "sw_skip_price_min")
async def skip_price_min(callback: CallbackQuery, state: FSMContext):
    """Пропуск минимальной цены."""
    await callback.answer()
    await state.set_state(SimplifiedWizardStates.enter_price_max)

    await callback.message.edit_text(
        "💰 <b>Уточнение бюджета</b>\n\n"
        "Введите <b>максимальную</b> сумму контракта.\n\n"
        "Или нажмите «Пропустить» (без ограничения).",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="sw_skip_price_max")],
            [InlineKeyboardButton(text="« Назад", callback_data="sw_back_to_refine")]
        ])
    )


@router.message(SimplifiedWizardStates.enter_price_max)
async def handle_price_max_input(message: Message, state: FSMContext):
    """Обработка ввода максимальной цены."""
    text = message.text.strip().replace(" ", "").replace(",", "")

    try:
        price_max = int(text)
        if price_max < 0:
            raise ValueError("Negative price")
    except ValueError:
        await message.answer(
            "⚠️ Введите число. Например: 10000000",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏭ Пропустить", callback_data="sw_skip_price_max")],
                [InlineKeyboardButton(text="« Назад", callback_data="sw_refine:budget")]
            ])
        )
        return

    await state.update_data(price_max=price_max)
    await state.set_state(SimplifiedWizardStates.refine_filter)

    data = await state.get_data()
    price_min = data.get('price_min', 0)

    await message.answer(
        f"✅ Бюджет: <b>{format_price(price_min)} - {format_price(price_max)}</b>\n\n"
        f"Хотите ещё что-то уточнить?",
        parse_mode="HTML",
        reply_markup=get_refinement_keyboard()
    )


@router.callback_query(F.data == "sw_skip_price_max")
async def skip_price_max(callback: CallbackQuery, state: FSMContext):
    """Пропуск максимальной цены."""
    await callback.answer()
    await state.set_state(SimplifiedWizardStates.refine_filter)

    await callback.message.edit_text(
        "✅ Бюджет: без ограничений\n\n"
        "Хотите ещё что-то уточнить?",
        parse_mode="HTML",
        reply_markup=get_refinement_keyboard()
    )


@router.callback_query(F.data == "sw_back_to_refine")
async def back_to_refine(callback: CallbackQuery, state: FSMContext):
    """Возврат к меню уточнений."""
    await callback.answer()
    await state.set_state(SimplifiedWizardStates.refine_filter)

    data = await state.get_data()
    keywords = data.get('keywords', [])

    await callback.message.edit_text(
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ <b>Ключевые слова:</b> {', '.join(keywords)}\n\n"
        f"<b>Шаг 3/3:</b> Хотите уточнить фильтр?",
        parse_mode="HTML",
        reply_markup=get_refinement_keyboard()
    )


@router.callback_query(F.data == "sw_refine:region")
async def refine_region(callback: CallbackQuery, state: FSMContext):
    """Уточнение региона."""
    await callback.answer()
    await state.set_state(SimplifiedWizardStates.select_region)

    await callback.message.edit_text(
        "📍 <b>Выбор региона</b>\n\n"
        "Выберите федеральный округ или всю Россию:",
        parse_mode="HTML",
        reply_markup=get_region_keyboard()
    )


@router.callback_query(F.data.startswith("sw_fd:"))
async def handle_federal_district_selection(callback: CallbackQuery, state: FSMContext):
    """Выбор федерального округа."""
    await callback.answer()

    fd_code = callback.data.split(":")[1]
    regions = get_regions_by_district(fd_code)

    await state.update_data(regions=regions, federal_district=fd_code)
    await state.set_state(SimplifiedWizardStates.refine_filter)

    federal_districts = get_all_federal_districts()
    fd_name = federal_districts.get(fd_code, fd_code)

    await callback.message.edit_text(
        f"✅ Регион: <b>{fd_name}</b>\n"
        f"({len(regions)} субъектов)\n\n"
        f"Хотите ещё что-то уточнить?",
        parse_mode="HTML",
        reply_markup=get_refinement_keyboard()
    )


@router.callback_query(F.data == "sw_region:all")
async def select_all_russia(callback: CallbackQuery, state: FSMContext):
    """Выбор всей России."""
    await callback.answer()

    await state.update_data(regions=[], federal_district=None)
    await state.set_state(SimplifiedWizardStates.refine_filter)

    await callback.message.edit_text(
        "✅ Регион: <b>Вся Россия</b>\n\n"
        "Хотите ещё что-то уточнить?",
        parse_mode="HTML",
        reply_markup=get_refinement_keyboard()
    )


@router.callback_query(F.data == "sw_refine:exclude")
async def refine_excluded(callback: CallbackQuery, state: FSMContext):
    """Уточнение исключённых слов."""
    await callback.answer()
    await state.set_state(SimplifiedWizardStates.enter_excluded)

    data = await state.get_data()
    default_excluded = data.get('default_excluded_words', [])

    if default_excluded:
        default_text = f"\n\n<i>По умолчанию исключаются: {', '.join(default_excluded)}</i>"
    else:
        default_text = ""

    await callback.message.edit_text(
        f"🚫 <b>Исключить слова</b>\n\n"
        f"Введите слова, которые НЕ должны встречаться в тендерах.\n"
        f"Через запятую.\n\n"
        f"Примеры: <i>медицин, военн, оборонн</i>"
        f"{default_text}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="sw_skip_excluded")],
            [InlineKeyboardButton(text="« Назад", callback_data="sw_back_to_refine")]
        ])
    )


@router.message(SimplifiedWizardStates.enter_excluded)
async def handle_excluded_input(message: Message, state: FSMContext):
    """Обработка ввода исключённых слов."""
    text = message.text.strip()

    excluded = [kw.strip() for kw in text.split(",") if kw.strip()]

    await state.update_data(exclude_keywords=excluded)
    await state.set_state(SimplifiedWizardStates.refine_filter)

    await message.answer(
        f"✅ Исключены: <b>{', '.join(excluded)}</b>\n\n"
        f"Хотите ещё что-то уточнить?",
        parse_mode="HTML",
        reply_markup=get_refinement_keyboard()
    )


@router.callback_query(F.data == "sw_skip_excluded")
async def skip_excluded(callback: CallbackQuery, state: FSMContext):
    """Пропуск исключённых слов - НЕ применяем defaults автоматически."""
    await callback.answer()
    await state.set_state(SimplifiedWizardStates.refine_filter)

    # НЕ применяем default_excluded_words - оставляем пустой список
    await state.update_data(exclude_keywords=[])

    await callback.message.edit_text(
        "✅ Исключённые слова: <b>не заданы</b>\n\n"
        "Хотите ещё что-то уточнить?",
        parse_mode="HTML",
        reply_markup=get_refinement_keyboard()
    )


# ============================================
# CREATE FILTER + INSTANT SEARCH
# ============================================

# ============================================
# ARCHIVE SEARCH (Simplified Flow)
# ============================================

class ArchiveSimplifiedStates(StatesGroup):
    """Состояния для упрощённого архивного поиска."""
    select_industry = State()       # Шаг 1: Выбор отрасли
    select_period = State()         # Шаг 2: Выбор периода
    enter_keywords = State()        # Шаг 3: Ключевые слова
    searching = State()             # Выполнение поиска


def get_period_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора периода для архивного поиска."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 7 дней", callback_data="arch_period:7"),
            InlineKeyboardButton(text="📅 30 дней", callback_data="arch_period:30")
        ],
        [
            InlineKeyboardButton(text="📅 90 дней", callback_data="arch_period:90"),
            InlineKeyboardButton(text="📅 180 дней", callback_data="arch_period:180")
        ],
        [InlineKeyboardButton(text="📅 Всё время", callback_data="arch_period:0")],
        [InlineKeyboardButton(text="« Назад", callback_data="arch_back_to_industry")]
    ])


@router.callback_query(F.data == "sniper_archive_search")
async def start_archive_simplified(callback: CallbackQuery, state: FSMContext):
    """Начало упрощённого архивного поиска."""
    await callback.answer()

    # Проверяем feature flag
    if not is_new_feature_enabled('simplified_wizard'):
        # Fallback на старый archive search
        from bot.handlers.sniper_search import start_archive_search
        await start_archive_search(callback, state)
        return

    await state.clear()
    await state.update_data(is_archive=True)
    await state.set_state(ArchiveSimplifiedStates.select_industry)

    await callback.message.edit_text(
        "📦 <b>Поиск в архиве</b>\n\n"
        "<b>Шаг 1/3:</b> Выберите отрасль\n\n"
        "Или нажмите «Пропустить» для произвольного поиска.",
        parse_mode="HTML",
        reply_markup=get_industry_keyboard()
    )


@router.callback_query(ArchiveSimplifiedStates.select_industry, F.data.startswith("sw_industry:"))
async def archive_handle_industry(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора отрасли для архивного поиска."""
    await callback.answer()

    industry_code = callback.data.split(":")[1]

    if industry_code == "custom":
        await state.update_data(industry=None, with_template=False)
    else:
        industry = INDUSTRY_TEMPLATES.get(industry_code, {})
        await state.update_data(
            industry=industry_code,
            with_template=True,
            default_keywords=industry.get('default_keywords', []),
        )

    await state.set_state(ArchiveSimplifiedStates.select_period)

    await callback.message.edit_text(
        "📦 <b>Поиск в архиве</b>\n\n"
        "<b>Шаг 2/3:</b> Выберите период поиска\n\n"
        "За какой период искать завершённые закупки?",
        parse_mode="HTML",
        reply_markup=get_period_keyboard()
    )


@router.callback_query(F.data == "arch_back_to_industry")
async def archive_back_to_industry(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору отрасли в архивном поиске."""
    await callback.answer()
    await state.set_state(ArchiveSimplifiedStates.select_industry)

    await callback.message.edit_text(
        "📦 <b>Поиск в архиве</b>\n\n"
        "<b>Шаг 1/3:</b> Выберите отрасль\n\n"
        "Или нажмите «Пропустить» для произвольного поиска.",
        parse_mode="HTML",
        reply_markup=get_industry_keyboard()
    )


@router.callback_query(F.data.startswith("arch_period:"))
async def archive_handle_period(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора периода."""
    await callback.answer()

    period_days = int(callback.data.split(":")[1])
    await state.update_data(archive_period_days=period_days)
    await state.set_state(ArchiveSimplifiedStates.enter_keywords)

    period_text = f"{period_days} дней" if period_days > 0 else "всё время"

    data = await state.get_data()
    industry_code = data.get('industry')

    if industry_code:
        industry = INDUSTRY_TEMPLATES.get(industry_code, {})
        await callback.message.edit_text(
            f"📦 <b>Поиск в архиве</b>\n\n"
            f"📅 Период: <b>{period_text}</b>\n\n"
            f"<b>Шаг 3/3:</b> Что ищем?\n\n"
            f"Выберите из популярных запросов или введите свои слова:",
            parse_mode="HTML",
            reply_markup=get_suggestions_keyboard(industry_code)
        )
    else:
        await callback.message.edit_text(
            f"📦 <b>Поиск в архиве</b>\n\n"
            f"📅 Период: <b>{period_text}</b>\n\n"
            f"<b>Шаг 3/3:</b> Введите ключевые слова\n\n"
            f"Укажите через запятую, что вы ищете.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="arch_back_to_period")]
            ])
        )


@router.callback_query(F.data == "arch_back_to_period")
async def archive_back_to_period(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору периода."""
    await callback.answer()
    await state.set_state(ArchiveSimplifiedStates.select_period)

    await callback.message.edit_text(
        "📦 <b>Поиск в архиве</b>\n\n"
        "<b>Шаг 2/3:</b> Выберите период поиска",
        parse_mode="HTML",
        reply_markup=get_period_keyboard()
    )


@router.callback_query(ArchiveSimplifiedStates.enter_keywords, F.data.startswith("sw_suggest:"))
async def archive_handle_suggestion(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора suggestion для архивного поиска."""
    await callback.answer("🔍 Начинаю поиск...")

    suggestion = callback.data.split(":", 1)[1]
    keywords = [kw.strip() for kw in suggestion.replace("(", ",").replace(")", "").split(",") if kw.strip()]

    await state.update_data(archive_keywords=keywords)

    # Запускаем поиск
    await run_archive_simplified_search(callback, state)


@router.callback_query(ArchiveSimplifiedStates.enter_keywords, F.data == "sw_custom_keywords")
async def archive_prompt_keywords(callback: CallbackQuery, state: FSMContext):
    """Запрос ввода своих ключевых слов для архива."""
    await callback.answer()

    await callback.message.edit_text(
        "📦 <b>Поиск в архиве</b>\n\n"
        "<b>Шаг 3/3:</b> Введите ключевые слова\n\n"
        "Укажите через запятую, что вы ищете.\n"
        "Например: <i>компьютеры, серверы, Dell</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="arch_back_to_period")]
        ])
    )


@router.message(ArchiveSimplifiedStates.enter_keywords)
async def archive_handle_keywords(message: Message, state: FSMContext):
    """Обработка ввода ключевых слов для архива."""
    text = message.text.strip()

    if len(text) < 2:
        await message.answer("⚠️ Введите хотя бы одно ключевое слово.")
        return

    keywords = [kw.strip() for kw in text.split(",") if kw.strip()]

    if not keywords:
        await message.answer("⚠️ Не удалось распознать ключевые слова.")
        return

    await state.update_data(archive_keywords=keywords)

    # Запускаем поиск
    await run_archive_simplified_search(message, state)


async def run_archive_simplified_search(message_or_callback, state: FSMContext):
    """Выполнение упрощённого архивного поиска."""
    import json as json_lib

    data = await state.get_data()
    period_days = data.get('archive_period_days', 30)
    keywords = data.get('archive_keywords', [])

    period_text = f"{period_days} дней" if period_days > 0 else "всё время"

    # Показываем статус
    if hasattr(message_or_callback, 'message'):
        # CallbackQuery
        message = message_or_callback.message
        await message.edit_text(
            f"📦 <b>Поиск в архиве</b>\n\n"
            f"🔄 Ищу завершённые закупки...\n\n"
            f"📅 Период: <b>{period_text}</b>\n"
            f"🔑 Слова: <b>{', '.join(keywords[:5])}</b>",
            parse_mode="HTML"
        )
    else:
        # Message
        message = message_or_callback
        status_msg = await message.answer(
            f"📦 <b>Поиск в архиве</b>\n\n"
            f"🔄 Ищу завершённые закупки...\n\n"
            f"📅 Период: <b>{period_text}</b>\n"
            f"🔑 Слова: <b>{', '.join(keywords[:5])}</b>",
            parse_mode="HTML"
        )
        message = status_msg

    try:
        # Получаем пользователя для сохранения истории
        db = await get_sniper_db()
        user_telegram_id = message_or_callback.from_user.id if hasattr(message_or_callback, 'from_user') else None
        user = None
        if user_telegram_id:
            user = await db.get_user_by_telegram_id(user_telegram_id)

        # Формируем filter_data для поиска
        filter_name = f"Архив: {', '.join(keywords[:2])}"
        filter_data = {
            'id': 0,
            'name': filter_name,
            'keywords': json_lib.dumps(keywords, ensure_ascii=False),
            'exclude_keywords': json_lib.dumps([], ensure_ascii=False),
            'price_min': None,
            'price_max': None,
            'regions': json_lib.dumps([], ensure_ascii=False),
            'tender_types': json_lib.dumps([], ensure_ascii=False),
            'law_type': None,
            'purchase_stage': 'archive',
            'purchase_method': None,
            'okpd2_codes': json_lib.dumps([], ensure_ascii=False),
            'min_deadline_days': None,
            'customer_keywords': json_lib.dumps([], ensure_ascii=False),
            'publication_days': period_days if period_days > 0 else None,
        }

        # Выполняем поиск
        searcher = InstantSearch()
        search_results = await searcher.search_by_filter(
            filter_data=filter_data,
            max_tenders=50,
            expanded_keywords=[]
        )

        matches = search_results.get('matches', [])

        # 🧪 БЕТА: Сохраняем историю поиска
        if user:
            try:
                await db.save_search_history(
                    user_id=user['id'],
                    search_type='archive_search',
                    keywords=keywords,
                    results_count=len(matches),
                    filter_id=None,
                    duration_ms=search_results.get('duration_ms')
                )
            except Exception as e:
                logger.warning(f"Не удалось сохранить историю архивного поиска: {e}")

        if not matches:
            await message.edit_text(
                f"📦 <b>Поиск в архиве</b>\n\n"
                f"😔 По вашему запросу ничего не найдено.\n\n"
                f"📅 Период: <b>{period_text}</b>\n"
                f"🔑 Слова: <b>{', '.join(keywords)}</b>\n\n"
                f"Попробуйте изменить ключевые слова или период.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Новый поиск", callback_data="sniper_archive_search")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="sniper_menu")]
                ]),
                parse_mode="HTML"
            )
            await state.clear()
            return

        # Генерируем HTML отчёт
        await message.edit_text(
            f"📦 <b>Поиск в архиве</b>\n\n"
            f"✅ Найдено: {len(matches)} тендеров\n"
            f"📄 Генерирую отчёт...",
            parse_mode="HTML"
        )

        report_path = await searcher.generate_html_report(
            search_results=search_results,
            filter_data=filter_data
        )

        # Отправляем отчёт
        if hasattr(message_or_callback, 'message'):
            msg_obj = message_or_callback.message
        else:
            msg_obj = message

        await msg_obj.answer_document(
            document=FSInputFile(report_path),
            caption=(
                f"📦 <b>Результаты архивного поиска</b>\n\n"
                f"📅 Период: <b>{period_text}</b>\n"
                f"🔑 Слова: <b>{', '.join(keywords[:3])}</b>\n"
                f"📊 Найдено: <b>{len(matches)}</b> тендеров"
            ),
            parse_mode="HTML"
        )

        await message.edit_text(
            f"✅ <b>Поиск завершён!</b>\n\n"
            f"📊 Найдено: {len(matches)} завершённых закупок\n\n"
            f"HTML отчёт отправлен выше ⬆️",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Новый поиск в архиве", callback_data="sniper_archive_search")],
                [InlineKeyboardButton(text="🔍 Поиск актуальных", callback_data="sniper_new_search")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="sniper_menu")]
            ])
        )

        await state.clear()

    except Exception as e:
        logger.error(f"Archive search error: {e}", exc_info=True)
        await message.edit_text(
            f"❌ Произошла ошибка при поиске.\n\n"
            f"Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="sniper_archive_search")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="sniper_menu")]
            ])
        )
        await state.clear()


@router.callback_query(F.data == "sw_create_filter")
async def create_filter_and_search(callback: CallbackQuery, state: FSMContext):
    """Создание фильтра и запуск мгновенного поиска."""
    await callback.answer("🔄 Создаю фильтр...")

    data = await state.get_data()

    # Получаем настройки
    keywords = data.get('keywords', [])
    filter_name = data.get('filter_name', 'Мой фильтр')

    # ВАЖНО: НЕ применяем defaults из шаблона автоматически!
    # Используем только то, что пользователь явно указал
    price_min = data.get('price_min')  # None если не указано
    price_max = data.get('price_max')  # None если не указано
    exclude_keywords = data.get('exclude_keywords', [])  # Пустой список если не указано
    regions = data.get('regions', [])

    if not keywords:
        await callback.message.edit_text(
            "❌ Не указаны ключевые слова. Начните сначала.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Начать заново", callback_data="sniper_new_search")],
                [InlineKeyboardButton(text="« Меню", callback_data="sniper_menu")]
            ])
        )
        await state.clear()
        return

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(callback.from_user.id)

        if not user:
            await callback.message.edit_text("❌ Пользователь не найден. Попробуйте /start")
            await state.clear()
            return

        # Показываем прогресс
        await callback.message.edit_text(
            f"🔄 <b>Создание фильтра...</b>\n\n"
            f"📝 Название: {filter_name}\n"
            f"🔑 Слова: {', '.join(keywords[:5])}\n\n"
            f"⏳ Пожалуйста, подождите...",
            parse_mode="HTML"
        )

        # Создаём фильтр
        filter_id = await db.create_filter(
            user_id=user['id'],
            name=filter_name[:255],
            keywords=keywords,
            exclude_keywords=exclude_keywords,
            price_min=price_min,
            price_max=price_max,
            regions=regions if regions else None,
            is_active=True
        )

        logger.info(f"Created filter {filter_id} for user {callback.from_user.id}")

        # Запускаем мгновенный поиск
        await callback.message.edit_text(
            f"✅ <b>Фильтр создан!</b>\n\n"
            f"📝 Название: {filter_name}\n"
            f"🔑 Слова: {', '.join(keywords[:5])}\n\n"
            f"🔍 Запускаю поиск тендеров...",
            parse_mode="HTML"
        )

        # Формируем filter_data для поиска
        import json as json_lib
        filter_data = {
            'id': filter_id,
            'name': filter_name,
            'keywords': json_lib.dumps(keywords, ensure_ascii=False),
            'exclude_keywords': json_lib.dumps(exclude_keywords or [], ensure_ascii=False),
            'price_min': price_min,
            'price_max': price_max,
            'regions': json_lib.dumps(regions or [], ensure_ascii=False),
            'tender_types': json_lib.dumps([], ensure_ascii=False),
            'law_type': None,
            'purchase_stage': None,
            'purchase_method': None,
            'okpd2_codes': json_lib.dumps([], ensure_ascii=False),
            'min_deadline_days': None,
            'customer_keywords': json_lib.dumps([], ensure_ascii=False),
        }

        # Выполняем поиск
        searcher = InstantSearch()
        search_results = await searcher.search_by_filter(
            filter_data=filter_data,
            max_tenders=25,
            expanded_keywords=[]
        )

        matches = search_results.get('matches', [])

        # 🧪 БЕТА: Сохраняем историю поиска
        try:
            await db.save_search_history(
                user_id=user['id'],
                search_type='instant_search',
                keywords=keywords,
                results_count=len(matches),
                filter_id=filter_id,
                duration_ms=search_results.get('duration_ms')
            )
        except Exception as e:
            logger.warning(f"Не удалось сохранить историю поиска: {e}")

        if not matches:
            await callback.message.edit_text(
                f"✅ <b>Фильтр создан!</b>\n\n"
                f"📝 Название: {filter_name}\n\n"
                f"😔 Пока не найдено подходящих тендеров.\n\n"
                f"🔔 Вы получите уведомление, когда появятся новые тендеры.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_filters")],
                    [InlineKeyboardButton(text="🔍 Новый поиск", callback_data="sniper_new_search")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="sniper_menu")]
                ])
            )
            await state.clear()
            return

        # Сохраняем найденные тендеры
        saved_count = 0
        for match in matches:
            try:
                tender_number = match.get('number', '')
                if not tender_number:
                    continue

                tender_data = {
                    'number': tender_number,
                    'name': match.get('name', ''),
                    'price': match.get('price'),
                    'region': match.get('customer_region') or match.get('region', ''),
                    'customer': match.get('customer') or match.get('customer_name', ''),
                    'published': match.get('published', ''),
                    'deadline': match.get('deadline') or match.get('end_date', ''),
                    'url': f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber={tender_number}"
                }

                await db.save_notification(
                    user_id=user['id'],
                    filter_id=filter_id,
                    filter_name=filter_name,
                    tender_data=tender_data,
                    score=match.get('match_score', 0),
                    matched_keywords=match.get('match_reasons', []),
                    source='instant_search'
                )
                saved_count += 1
            except Exception as e:
                logger.warning(f"Не удалось сохранить тендер: {e}")

        # Генерируем HTML отчёт
        await callback.message.edit_text(
            f"✅ <b>Фильтр создан!</b>\n\n"
            f"📊 Найдено: {len(matches)} тендеров\n"
            f"💾 Сохранено: {saved_count}\n\n"
            f"📄 Генерирую отчёт...",
            parse_mode="HTML"
        )

        report_path = await searcher.generate_html_report(
            search_results=search_results,
            filter_data=filter_data
        )

        # Отправляем отчёт
        await callback.message.answer_document(
            document=FSInputFile(report_path),
            caption=(
                f"📊 <b>Результаты поиска</b>\n\n"
                f"📝 Фильтр: {filter_name}\n"
                f"🔑 Слова: {', '.join(keywords[:3])}\n"
                f"📊 Найдено: {len(matches)} тендеров\n\n"
                f"🔔 Автомониторинг активирован!"
            ),
            parse_mode="HTML"
        )

        await callback.message.answer(
            f"✅ <b>Готово!</b>\n\n"
            f"Фильтр <b>{filter_name}</b> создан и активирован.\n"
            f"Вы получите уведомления о новых подходящих тендерах.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_filters")],
                [InlineKeyboardButton(text="🔍 Новый поиск", callback_data="sniper_new_search")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="sniper_menu")]
            ])
        )

        await state.clear()

    except Exception as e:
        logger.error(f"Error creating filter: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ Произошла ошибка при создании фильтра.\n\n"
            f"Попробуйте позже или обратитесь в поддержку.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="sniper_new_search")],
                [InlineKeyboardButton(text="« Меню", callback_data="sniper_menu")]
            ])
        )
        await state.clear()
