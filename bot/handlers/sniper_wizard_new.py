"""
Extended Wizard - Расширенный wizard создания фильтров (5-7 шагов).

Процесс:
1. Тип закупки (товары/услуги/работы/любые)
2. Ключевые слова
3. Бюджет (опционально)
4. Регион (опционально)
5. Закон 44-ФЗ/223-ФЗ (опционально)
6. Исключения (опционально)
7. Создание фильтра + мгновенный поиск

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
# ТИПЫ ЗАКУПОК
# ============================================

TENDER_TYPES = {
    'goods': {'icon': '📦', 'name': 'Товары', 'value': 'товары'},
    'services': {'icon': '🔧', 'name': 'Услуги', 'value': 'услуги'},
    'works': {'icon': '🏗', 'name': 'Работы', 'value': 'работы'},
    'any': {'icon': '📋', 'name': 'Любые', 'value': None},
}

# ============================================
# ЗАКОНЫ
# ============================================

LAW_TYPES = {
    '44fz': {'icon': '📜', 'name': '44-ФЗ (госзакупки)', 'value': '44'},
    '223fz': {'icon': '📜', 'name': '223-ФЗ (корпоративные)', 'value': '223'},
    'any': {'icon': '📋', 'name': 'Любой закон', 'value': None},
}

# ============================================
# БЫСТРЫЕ ВАРИАНТЫ БЮДЖЕТА
# ============================================

BUDGET_PRESETS = [
    {'label': 'до 500 тыс', 'min': None, 'max': 500000},
    {'label': '500 тыс - 3 млн', 'min': 500000, 'max': 3000000},
    {'label': '3 - 10 млн', 'min': 3000000, 'max': 10000000},
    {'label': '10 - 50 млн', 'min': 10000000, 'max': 50000000},
    {'label': '50 - 100 млн', 'min': 50000000, 'max': 100000000},
    {'label': 'более 100 млн', 'min': 100000000, 'max': None},
]


# ============================================
# FSM States для расширенного wizard
# ============================================

class ExtendedWizardStates(StatesGroup):
    """Состояния для расширенного wizard (5-7 шагов)."""
    select_tender_type = State()    # Шаг 1: Тип закупки
    enter_keywords = State()        # Шаг 2: Ключевые слова
    select_budget = State()         # Шаг 3: Бюджет (опционально)
    enter_budget_min = State()      # Шаг 3a: Свой бюджет - мин
    enter_budget_max = State()      # Шаг 3b: Свой бюджет - макс
    select_region = State()         # Шаг 4: Регион (опционально)
    select_law = State()            # Шаг 5: Закон (опционально)
    enter_excluded = State()        # Шаг 6: Исключения (опционально)
    confirm_create = State()        # Шаг 7: Подтверждение


# Алиас для обратной совместимости
SimplifiedWizardStates = ExtendedWizardStates


# ============================================
# HELPER FUNCTIONS
# ============================================

def format_price(price: Optional[float]) -> str:
    """Форматирование цены в читаемый вид."""
    if price is None:
        return "без ограничений"
    if price >= 1_000_000:
        return f"{price / 1_000_000:.1f} млн ₽"
    elif price >= 1_000:
        return f"{price / 1_000:.0f} тыс ₽"
    else:
        return f"{price:.0f} ₽"


def get_current_settings_text(data: dict) -> str:
    """Форматирует текущие настройки фильтра."""
    tender_type = data.get('tender_type_name', 'Любые')
    keywords = data.get('keywords', [])
    price_min = data.get('price_min')
    price_max = data.get('price_max')
    regions = data.get('regions', [])
    law_type = data.get('law_type_name', 'Любой')
    exclude_keywords = data.get('exclude_keywords', [])

    # Форматируем бюджет
    if price_min and price_max:
        budget_text = f"{format_price(price_min)} - {format_price(price_max)}"
    elif price_max:
        budget_text = f"до {format_price(price_max)}"
    elif price_min:
        budget_text = f"от {format_price(price_min)}"
    else:
        budget_text = "без ограничений"

    # Форматируем регионы
    if regions:
        region_text = f"{len(regions)} регион(ов)"
    else:
        region_text = "Вся Россия"

    # Форматируем исключения
    if exclude_keywords:
        exclude_text = ", ".join(exclude_keywords[:3])
        if len(exclude_keywords) > 3:
            exclude_text += f" +{len(exclude_keywords) - 3}"
    else:
        exclude_text = "нет"

    return (
        f"<b>Текущие настройки:</b>\n"
        f"📦 Тип: <b>{tender_type}</b>\n"
        f"🔑 Слова: <b>{', '.join(keywords[:5]) if keywords else 'не указаны'}</b>\n"
        f"💰 Бюджет: <b>{budget_text}</b>\n"
        f"📍 Регион: <b>{region_text}</b>\n"
        f"📜 Закон: <b>{law_type}</b>\n"
        f"🚫 Исключения: <b>{exclude_text}</b>"
    )


def get_tender_type_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора типа закупки."""
    keyboard = []
    row = []

    for type_code, type_info in TENDER_TYPES.items():
        text = f"{type_info['icon']} {type_info['name']}"
        row.append(InlineKeyboardButton(
            text=text,
            callback_data=f"ew_type:{type_code}"
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton(text="« Отмена", callback_data="sniper_menu")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_budget_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора бюджета."""
    keyboard = []

    # Быстрые варианты по 2 в ряд
    row = []
    for i, preset in enumerate(BUDGET_PRESETS):
        row.append(InlineKeyboardButton(
            text=f"💰 {preset['label']}",
            callback_data=f"ew_budget:{i}"
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton(text="✍️ Указать свой диапазон", callback_data="ew_budget:custom")
    ])
    keyboard.append([
        InlineKeyboardButton(text="⏭ Без ограничений", callback_data="ew_budget:skip")
    ])
    keyboard.append([
        InlineKeyboardButton(text="« Назад", callback_data="ew_back:keywords")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_region_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора региона."""
    federal_districts = get_all_federal_districts()

    keyboard = []
    for fd_code, fd_name in federal_districts.items():
        keyboard.append([
            InlineKeyboardButton(
                text=f"🗺 {fd_name}",
                callback_data=f"ew_fd:{fd_code}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(text="🌍 Вся Россия", callback_data="ew_region:all")
    ])
    keyboard.append([
        InlineKeyboardButton(text="« Назад", callback_data="ew_back:budget")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_law_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора закона."""
    keyboard = []

    for law_code, law_info in LAW_TYPES.items():
        text = f"{law_info['icon']} {law_info['name']}"
        keyboard.append([
            InlineKeyboardButton(text=text, callback_data=f"ew_law:{law_code}")
        ])

    keyboard.append([
        InlineKeyboardButton(text="« Назад", callback_data="ew_back:region")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_exclusions_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для шага исключений."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить (без исключений)", callback_data="ew_exclude:skip")],
        [InlineKeyboardButton(text="« Назад", callback_data="ew_back:law")],
    ])


def get_confirm_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения создания."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Создать фильтр и искать", callback_data="ew_confirm:create")],
        [InlineKeyboardButton(text="✏️ Изменить настройки", callback_data="ew_confirm:edit")],
        [InlineKeyboardButton(text="« Отмена", callback_data="sniper_menu")],
    ])


def get_edit_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура редактирования параметров."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📦 Тип", callback_data="ew_edit:type"),
            InlineKeyboardButton(text="🔑 Слова", callback_data="ew_edit:keywords"),
        ],
        [
            InlineKeyboardButton(text="💰 Бюджет", callback_data="ew_edit:budget"),
            InlineKeyboardButton(text="📍 Регион", callback_data="ew_edit:region"),
        ],
        [
            InlineKeyboardButton(text="📜 Закон", callback_data="ew_edit:law"),
            InlineKeyboardButton(text="🚫 Исключения", callback_data="ew_edit:exclude"),
        ],
        [InlineKeyboardButton(text="🚀 Создать фильтр", callback_data="ew_confirm:create")],
        [InlineKeyboardButton(text="« Отмена", callback_data="sniper_menu")],
    ])


# ============================================
# EXTENDED WIZARD HANDLERS
# ============================================

@router.callback_query(F.data == "sniper_new_search")
async def start_extended_wizard(callback: CallbackQuery, state: FSMContext):
    """
    Начало расширенного wizard (5-7 шагов).
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

        # Очищаем state и инициализируем defaults
        await state.clear()
        await state.update_data(
            tender_type=None,
            tender_type_name='Любые',
            keywords=[],
            price_min=None,
            price_max=None,
            regions=[],
            law_type=None,
            law_type_name='Любой',
            exclude_keywords=[]
        )
        await state.set_state(ExtendedWizardStates.select_tender_type)

        await callback.message.edit_text(
            "🎯 <b>Создание фильтра</b>\n\n"
            "<b>Шаг 1/6:</b> Что ищем?\n\n"
            "Выберите тип закупки:",
            parse_mode="HTML",
            reply_markup=get_tender_type_keyboard()
        )

    except Exception as e:
        logger.error(f"Error starting extended wizard: {e}", exc_info=True)
        await callback.message.answer("❌ Произошла ошибка. Попробуйте позже.")


# ============================================
# ШАГ 1: ТИП ЗАКУПКИ
# ============================================

@router.callback_query(F.data.startswith("ew_type:"))
async def handle_tender_type_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора типа закупки."""
    await callback.answer()

    type_code = callback.data.split(":")[1]
    type_info = TENDER_TYPES.get(type_code, TENDER_TYPES['any'])

    # Сохраняем выбор
    tender_types_list = [type_info['value']] if type_info['value'] else []
    await state.update_data(
        tender_type=tender_types_list,
        tender_type_name=type_info['name']
    )

    # Переходим к шагу 2: ключевые слова
    await state.set_state(ExtendedWizardStates.enter_keywords)

    await callback.message.edit_text(
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ Тип: <b>{type_info['icon']} {type_info['name']}</b>\n\n"
        f"<b>Шаг 2/6:</b> Введите ключевые слова\n\n"
        f"Укажите через запятую, что вы ищете.\n"
        f"Например: <i>Lenovo, ноутбуки, ThinkPad</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="ew_back:type")]
        ])
    )


# ============================================
# ШАГ 2: КЛЮЧЕВЫЕ СЛОВА
# ============================================

@router.message(ExtendedWizardStates.enter_keywords)
async def handle_keywords_input(message: Message, state: FSMContext):
    """Обработка ввода ключевых слов."""
    text = message.text.strip()

    if len(text) < 2:
        await message.answer(
            "⚠️ Введите хотя бы одно ключевое слово.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="ew_back:type")]
            ])
        )
        return

    # Парсим keywords
    keywords = [kw.strip() for kw in text.split(",") if kw.strip()]

    if not keywords:
        await message.answer(
            "⚠️ Не удалось распознать ключевые слова. Попробуйте ещё раз.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="ew_back:type")]
            ])
        )
        return

    # Генерируем название фильтра
    filter_name = ", ".join(keywords[:3])
    if len(keywords) > 3:
        filter_name += f" +{len(keywords) - 3}"

    await state.update_data(keywords=keywords, filter_name=filter_name)

    # Переходим к шагу 3: бюджет
    await state.set_state(ExtendedWizardStates.select_budget)

    data = await state.get_data()

    await message.answer(
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
        f"✅ Слова: <b>{', '.join(keywords[:5])}</b>\n\n"
        f"<b>Шаг 3/6:</b> Укажите бюджет\n\n"
        f"Выберите диапазон или укажите свой:",
        parse_mode="HTML",
        reply_markup=get_budget_keyboard()
    )


# ============================================
# ШАГ 3: БЮДЖЕТ
# ============================================

@router.callback_query(F.data.startswith("ew_budget:"))
async def handle_budget_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора бюджета."""
    await callback.answer()

    choice = callback.data.split(":")[1]

    if choice == "skip":
        # Без ограничений
        await state.update_data(price_min=None, price_max=None)
        await go_to_region_step(callback.message, state)

    elif choice == "custom":
        # Свой диапазон - запрашиваем минимум
        await state.set_state(ExtendedWizardStates.enter_budget_min)
        await callback.message.edit_text(
            "💰 <b>Укажите бюджет</b>\n\n"
            "Введите <b>минимальную</b> сумму контракта (в рублях).\n\n"
            "Примеры:\n"
            "• 100000 (100 тыс)\n"
            "• 1000000 (1 млн)\n"
            "• 0 (без минимума)",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏭ Пропустить минимум", callback_data="ew_budget_min:skip")],
                [InlineKeyboardButton(text="« Назад", callback_data="ew_back:keywords")]
            ])
        )

    else:
        # Выбран пресет
        try:
            preset_idx = int(choice)
            preset = BUDGET_PRESETS[preset_idx]
            await state.update_data(price_min=preset['min'], price_max=preset['max'])
            await go_to_region_step(callback.message, state)
        except (ValueError, IndexError):
            await callback.answer("Ошибка выбора бюджета")


@router.message(ExtendedWizardStates.enter_budget_min)
async def handle_budget_min_input(message: Message, state: FSMContext):
    """Обработка ввода минимального бюджета."""
    text = message.text.strip().replace(" ", "").replace(",", "")

    try:
        price_min = int(text)
        if price_min < 0:
            raise ValueError("Negative")
        if price_min == 0:
            price_min = None
    except ValueError:
        await message.answer(
            "⚠️ Введите число. Например: 100000",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏭ Пропустить", callback_data="ew_budget_min:skip")],
                [InlineKeyboardButton(text="« Назад", callback_data="ew_back:keywords")]
            ])
        )
        return

    await state.update_data(price_min=price_min)
    await state.set_state(ExtendedWizardStates.enter_budget_max)

    await message.answer(
        f"💰 <b>Укажите бюджет</b>\n\n"
        f"✅ Минимум: <b>{format_price(price_min)}</b>\n\n"
        f"Теперь введите <b>максимальную</b> сумму.\n"
        f"Или нажмите «Пропустить» (без ограничения сверху).",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Без максимума", callback_data="ew_budget_max:skip")],
            [InlineKeyboardButton(text="« Назад", callback_data="ew_back:budget")]
        ])
    )


@router.callback_query(F.data == "ew_budget_min:skip")
async def skip_budget_min(callback: CallbackQuery, state: FSMContext):
    """Пропуск минимального бюджета."""
    await callback.answer()
    await state.update_data(price_min=None)
    await state.set_state(ExtendedWizardStates.enter_budget_max)

    await callback.message.edit_text(
        "💰 <b>Укажите бюджет</b>\n\n"
        "Введите <b>максимальную</b> сумму контракта.\n"
        "Или нажмите «Пропустить» (без ограничений).",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Без ограничений", callback_data="ew_budget_max:skip")],
            [InlineKeyboardButton(text="« Назад", callback_data="ew_back:budget")]
        ])
    )


@router.message(ExtendedWizardStates.enter_budget_max)
async def handle_budget_max_input(message: Message, state: FSMContext):
    """Обработка ввода максимального бюджета."""
    text = message.text.strip().replace(" ", "").replace(",", "")

    try:
        price_max = int(text)
        if price_max < 0:
            raise ValueError("Negative")
        if price_max == 0:
            price_max = None
    except ValueError:
        await message.answer(
            "⚠️ Введите число. Например: 10000000",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏭ Пропустить", callback_data="ew_budget_max:skip")],
                [InlineKeyboardButton(text="« Назад", callback_data="ew_back:budget")]
            ])
        )
        return

    await state.update_data(price_max=price_max)
    await go_to_region_step(message, state)


@router.callback_query(F.data == "ew_budget_max:skip")
async def skip_budget_max(callback: CallbackQuery, state: FSMContext):
    """Пропуск максимального бюджета."""
    await callback.answer()
    await state.update_data(price_max=None)
    await go_to_region_step(callback.message, state)


async def go_to_region_step(message, state: FSMContext):
    """Переход к шагу выбора региона."""
    await state.set_state(ExtendedWizardStates.select_region)
    data = await state.get_data()

    # Форматируем бюджет для отображения
    price_min = data.get('price_min')
    price_max = data.get('price_max')
    if price_min and price_max:
        budget_text = f"{format_price(price_min)} - {format_price(price_max)}"
    elif price_max:
        budget_text = f"до {format_price(price_max)}"
    elif price_min:
        budget_text = f"от {format_price(price_min)}"
    else:
        budget_text = "без ограничений"

    text = (
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
        f"✅ Слова: <b>{', '.join(data.get('keywords', [])[:3])}</b>\n"
        f"✅ Бюджет: <b>{budget_text}</b>\n\n"
        f"<b>Шаг 4/6:</b> Выберите регион"
    )

    if hasattr(message, 'edit_text'):
        await message.edit_text(text, parse_mode="HTML", reply_markup=get_region_keyboard())
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=get_region_keyboard())


# ============================================
# ШАГ 4: РЕГИОН
# ============================================

@router.callback_query(F.data == "ew_region:all")
async def select_all_russia(callback: CallbackQuery, state: FSMContext):
    """Выбор всей России."""
    await callback.answer()
    await state.update_data(regions=[], region_name="Вся Россия")
    await go_to_law_step(callback.message, state)


@router.callback_query(F.data.startswith("ew_fd:"))
async def handle_federal_district(callback: CallbackQuery, state: FSMContext):
    """Выбор федерального округа."""
    await callback.answer()

    fd_code = callback.data.split(":")[1]
    regions = get_regions_by_district(fd_code)
    federal_districts = get_all_federal_districts()
    fd_name = federal_districts.get(fd_code, fd_code)

    await state.update_data(regions=regions, region_name=fd_name)
    await go_to_law_step(callback.message, state)


async def go_to_law_step(message, state: FSMContext):
    """Переход к шагу выбора закона."""
    await state.set_state(ExtendedWizardStates.select_law)
    data = await state.get_data()

    text = (
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
        f"✅ Слова: <b>{', '.join(data.get('keywords', [])[:3])}</b>\n"
        f"✅ Регион: <b>{data.get('region_name', 'Вся Россия')}</b>\n\n"
        f"<b>Шаг 5/6:</b> Выберите закон"
    )

    await message.edit_text(text, parse_mode="HTML", reply_markup=get_law_keyboard())


# ============================================
# ШАГ 5: ЗАКОН
# ============================================

@router.callback_query(F.data.startswith("ew_law:"))
async def handle_law_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора закона."""
    await callback.answer()

    law_code = callback.data.split(":")[1]
    law_info = LAW_TYPES.get(law_code, LAW_TYPES['any'])

    await state.update_data(
        law_type=law_info['value'],
        law_type_name=law_info['name']
    )

    # Переходим к шагу 6: исключения
    await go_to_exclusions_step(callback.message, state)


async def go_to_exclusions_step(message, state: FSMContext):
    """Переход к шагу исключений."""
    await state.set_state(ExtendedWizardStates.enter_excluded)
    data = await state.get_data()

    text = (
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
        f"✅ Слова: <b>{', '.join(data.get('keywords', [])[:3])}</b>\n"
        f"✅ Закон: <b>{data.get('law_type_name', 'Любой')}</b>\n\n"
        f"<b>Шаг 6/6:</b> Исключить слова\n\n"
        f"Введите слова, которые НЕ должны встречаться в тендерах.\n"
        f"Через запятую. Например: <i>медицин, ремонт, демонтаж</i>\n\n"
        f"Или пропустите этот шаг."
    )

    await message.edit_text(text, parse_mode="HTML", reply_markup=get_exclusions_keyboard())


# ============================================
# ШАГ 6: ИСКЛЮЧЕНИЯ
# ============================================

@router.message(ExtendedWizardStates.enter_excluded)
async def handle_exclusions_input(message: Message, state: FSMContext):
    """Обработка ввода исключений."""
    text = message.text.strip()
    excluded = [kw.strip() for kw in text.split(",") if kw.strip()]

    await state.update_data(exclude_keywords=excluded)
    await go_to_confirm_step(message, state)


@router.callback_query(F.data == "ew_exclude:skip")
async def skip_exclusions(callback: CallbackQuery, state: FSMContext):
    """Пропуск исключений."""
    await callback.answer()
    await state.update_data(exclude_keywords=[])
    await go_to_confirm_step(callback.message, state)


async def go_to_confirm_step(message, state: FSMContext):
    """Переход к шагу подтверждения."""
    await state.set_state(ExtendedWizardStates.confirm_create)
    data = await state.get_data()

    settings_text = get_current_settings_text(data)

    text = (
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"{settings_text}\n\n"
        f"Всё верно? Нажмите «Создать» или измените настройки."
    )

    if hasattr(message, 'edit_text'):
        await message.edit_text(text, parse_mode="HTML", reply_markup=get_confirm_keyboard())
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=get_confirm_keyboard())


# ============================================
# ПОДТВЕРЖДЕНИЕ И РЕДАКТИРОВАНИЕ
# ============================================

@router.callback_query(F.data == "ew_confirm:edit")
async def show_edit_menu(callback: CallbackQuery, state: FSMContext):
    """Показать меню редактирования."""
    await callback.answer()
    data = await state.get_data()

    settings_text = get_current_settings_text(data)

    await callback.message.edit_text(
        f"✏️ <b>Редактирование фильтра</b>\n\n"
        f"{settings_text}\n\n"
        f"Выберите параметр для изменения:",
        parse_mode="HTML",
        reply_markup=get_edit_keyboard()
    )


@router.callback_query(F.data.startswith("ew_edit:"))
async def handle_edit_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора редактирования."""
    await callback.answer()
    param = callback.data.split(":")[1]

    if param == "type":
        await state.set_state(ExtendedWizardStates.select_tender_type)
        await callback.message.edit_text(
            "📦 <b>Изменить тип закупки</b>\n\n"
            "Выберите тип:",
            parse_mode="HTML",
            reply_markup=get_tender_type_keyboard()
        )
    elif param == "keywords":
        await state.set_state(ExtendedWizardStates.enter_keywords)
        await callback.message.edit_text(
            "🔑 <b>Изменить ключевые слова</b>\n\n"
            "Введите новые ключевые слова через запятую:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Отмена", callback_data="ew_back:confirm")]
            ])
        )
    elif param == "budget":
        await state.set_state(ExtendedWizardStates.select_budget)
        await callback.message.edit_text(
            "💰 <b>Изменить бюджет</b>\n\n"
            "Выберите диапазон:",
            parse_mode="HTML",
            reply_markup=get_budget_keyboard()
        )
    elif param == "region":
        await state.set_state(ExtendedWizardStates.select_region)
        await callback.message.edit_text(
            "📍 <b>Изменить регион</b>\n\n"
            "Выберите регион:",
            parse_mode="HTML",
            reply_markup=get_region_keyboard()
        )
    elif param == "law":
        await state.set_state(ExtendedWizardStates.select_law)
        await callback.message.edit_text(
            "📜 <b>Изменить закон</b>\n\n"
            "Выберите закон:",
            parse_mode="HTML",
            reply_markup=get_law_keyboard()
        )
    elif param == "exclude":
        await state.set_state(ExtendedWizardStates.enter_excluded)
        await callback.message.edit_text(
            "🚫 <b>Изменить исключения</b>\n\n"
            "Введите слова для исключения через запятую:",
            parse_mode="HTML",
            reply_markup=get_exclusions_keyboard()
        )


# ============================================
# НАВИГАЦИЯ НАЗАД
# ============================================

@router.callback_query(F.data.startswith("ew_back:"))
async def handle_back_navigation(callback: CallbackQuery, state: FSMContext):
    """Обработка кнопок «Назад»."""
    await callback.answer()
    target = callback.data.split(":")[1]

    if target == "type":
        await state.set_state(ExtendedWizardStates.select_tender_type)
        await callback.message.edit_text(
            "🎯 <b>Создание фильтра</b>\n\n"
            "<b>Шаг 1/6:</b> Что ищем?\n\n"
            "Выберите тип закупки:",
            parse_mode="HTML",
            reply_markup=get_tender_type_keyboard()
        )

    elif target == "keywords":
        data = await state.get_data()
        await state.set_state(ExtendedWizardStates.enter_keywords)
        await callback.message.edit_text(
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n\n"
            f"<b>Шаг 2/6:</b> Введите ключевые слова\n\n"
            f"Укажите через запятую, что вы ищете:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="ew_back:type")]
            ])
        )

    elif target == "budget":
        data = await state.get_data()
        await state.set_state(ExtendedWizardStates.select_budget)
        await callback.message.edit_text(
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
            f"✅ Слова: <b>{', '.join(data.get('keywords', [])[:3])}</b>\n\n"
            f"<b>Шаг 3/6:</b> Укажите бюджет",
            parse_mode="HTML",
            reply_markup=get_budget_keyboard()
        )

    elif target == "region":
        await go_to_region_step(callback.message, state)

    elif target == "law":
        await go_to_law_step(callback.message, state)

    elif target == "confirm":
        await go_to_confirm_step(callback.message, state)


# ============================================
# СОЗДАНИЕ ФИЛЬТРА
# ============================================

@router.callback_query(F.data == "ew_confirm:create")
async def create_filter_and_search(callback: CallbackQuery, state: FSMContext):
    """Создание фильтра и запуск мгновенного поиска."""
    await callback.answer("🔄 Создаю фильтр...")

    data = await state.get_data()

    # Получаем настройки - используем только то, что пользователь явно указал
    keywords = data.get('keywords', [])
    filter_name = data.get('filter_name', 'Мой фильтр')
    tender_types = data.get('tender_type', [])
    price_min = data.get('price_min')
    price_max = data.get('price_max')
    regions = data.get('regions', [])
    law_type = data.get('law_type')
    exclude_keywords = data.get('exclude_keywords', [])

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

        # Создаём фильтр в БД
        filter_id = await db.create_filter(
            user_id=user['id'],
            name=filter_name[:255],
            keywords=keywords,
            exclude_keywords=exclude_keywords,
            price_min=price_min,
            price_max=price_max,
            regions=regions if regions else None,
            tender_types=tender_types if tender_types else None,
            law_type=law_type,
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
            'tender_types': json_lib.dumps(tender_types or [], ensure_ascii=False),
            'law_type': law_type,
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

        # Сохраняем историю поиска
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


# ============================================
# LEGACY HANDLERS (для обратной совместимости с архивным поиском)
# ============================================

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
