"""
Extended Wizard - Расширенный wizard создания фильтров (8 шагов).

Процесс:
1. Тип закупки (товары/услуги/работы/любые)
2. Ключевые слова
3. Бюджет (опционально)
4. Регион (опционально)
5. Закон 44-ФЗ/223-ФЗ (опционально)
6. Исключения (опционально)
7. Настройки поиска (количество тендеров + автомониторинг)
8. Подтверждение и создание фильтра

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
# ЛИМИТЫ ПОИСКА
# ============================================

SEARCH_LIMITS = [
    {'value': 10, 'label': '10 тендеров', 'icon': '🔟'},
    {'value': 25, 'label': '25 тендеров', 'icon': '📊'},
    {'value': 50, 'label': '50 тендеров', 'icon': '📈'},
    {'value': 100, 'label': '100 тендеров', 'icon': '💯'},
]


# ============================================
# ШАБЛОНЫ ОТРАСЛЕЙ (для архивного поиска)
# ============================================

INDUSTRY_TEMPLATES = {
    'it': {
        'icon': '💻',
        'name': 'IT и оборудование',
        'default_keywords': ['компьютер', 'сервер', 'программное обеспечение'],
        'suggestions': [
            'Компьютеры, ноутбуки',
            'Серверы, СХД',
            'Программное обеспечение',
            'Сетевое оборудование',
        ]
    },
    'construction': {
        'icon': '🏗',
        'name': 'Строительство',
        'default_keywords': ['строительство', 'ремонт', 'СМР'],
        'suggestions': [
            'Строительные работы',
            'Капитальный ремонт',
            'Стройматериалы',
            'Проектирование',
        ]
    },
    'medicine': {
        'icon': '🏥',
        'name': 'Медицина',
        'default_keywords': ['медицинское оборудование', 'лекарства'],
        'suggestions': [
            'Медицинское оборудование',
            'Лекарственные препараты',
            'Расходные материалы',
        ]
    },
    'transport': {
        'icon': '🚗',
        'name': 'Транспорт',
        'default_keywords': ['автомобиль', 'транспорт', 'спецтехника'],
        'suggestions': [
            'Автомобили',
            'Спецтехника',
            'ГСМ, топливо',
            'Запчасти',
        ]
    },
    'services': {
        'icon': '🔧',
        'name': 'Услуги',
        'default_keywords': ['услуги', 'обслуживание'],
        'suggestions': [
            'Охранные услуги',
            'Клининг',
            'Питание',
            'Техническое обслуживание',
        ]
    },
}


# ============================================
# FSM States для расширенного wizard
# ============================================

class ExtendedWizardStates(StatesGroup):
    """Состояния для расширенного wizard (8 шагов)."""
    select_tender_type = State()    # Шаг 1: Тип закупки
    enter_keywords = State()        # Шаг 2: Ключевые слова
    enter_budget_min = State()      # Шаг 3a: Бюджет - минимум
    enter_budget_max = State()      # Шаг 3b: Бюджет - максимум
    confirm_budget = State()        # Шаг 3c: Подтверждение бюджета
    select_region = State()         # Шаг 4: Регион (опционально)
    select_law = State()            # Шаг 5: Закон (опционально)
    enter_excluded = State()        # Шаг 6: Исключения (опционально)
    select_search_limit = State()   # Шаг 7a: Количество тендеров
    select_automonitor = State()    # Шаг 7b: Автомониторинг
    confirm_create = State()        # Шаг 8: Подтверждение


# Алиас для обратной совместимости
SimplifiedWizardStates = ExtendedWizardStates


# ============================================
# HELPER FUNCTIONS
# ============================================

def format_price(price: Optional[float]) -> str:
    """Форматирование цены в читаемый вид."""
    if price is None:
        return "без ограничений"
    if price >= 1_000_000_000:
        # Миллиарды
        value = price / 1_000_000_000
        if value == int(value):
            return f"{int(value)} млрд ₽"
        return f"{value:.1f} млрд ₽"
    elif price >= 1_000_000:
        # Миллионы
        value = price / 1_000_000
        if value == int(value):
            return f"{int(value)} млн ₽"
        return f"{value:.1f} млн ₽"
    elif price >= 1_000:
        return f"{price / 1_000:.0f} тыс ₽"
    else:
        return f"{price:.0f} ₽"


async def save_draft(telegram_id: int, data: dict, current_step: str):
    """Сохраняет черновик фильтра в БД."""
    try:
        db = await get_sniper_db()
        await db.save_filter_draft(telegram_id, data, current_step)
        logger.debug(f"Draft saved for user {telegram_id}, step: {current_step}")
    except Exception as e:
        logger.error(f"Error saving draft: {e}")


async def delete_draft(telegram_id: int):
    """Удаляет черновик фильтра из БД."""
    try:
        db = await get_sniper_db()
        await db.delete_filter_draft(telegram_id)
        logger.debug(f"Draft deleted for user {telegram_id}")
    except Exception as e:
        logger.error(f"Error deleting draft: {e}")


async def get_draft(telegram_id: int) -> dict | None:
    """Получает черновик фильтра из БД."""
    try:
        db = await get_sniper_db()
        return await db.get_filter_draft(telegram_id)
    except Exception as e:
        logger.error(f"Error getting draft: {e}")
        return None


def get_step_name(step: str) -> str:
    """Возвращает читаемое название шага."""
    step_names = {
        'select_tender_type': 'Тип закупки',
        'enter_keywords': 'Ключевые слова',
        'enter_budget_min': 'Бюджет (мин)',
        'enter_budget_max': 'Бюджет (макс)',
        'confirm_budget': 'Подтверждение бюджета',
        'select_region': 'Регион',
        'select_law': 'Закон',
        'enter_excluded': 'Исключения',
        'select_search_limit': 'Количество тендеров',
        'select_automonitor': 'Автомониторинг',
        'confirm_create': 'Подтверждение',
    }
    return step_names.get(step, step)


def get_current_settings_text(data: dict) -> str:
    """Форматирует текущие настройки фильтра."""
    tender_type = data.get('tender_type_name', 'Любые')
    keywords = data.get('keywords', [])
    price_min = data.get('price_min')
    price_max = data.get('price_max')
    regions = data.get('regions', [])
    law_type = data.get('law_type_name', 'Любой')
    exclude_keywords = data.get('exclude_keywords', [])
    search_limit = data.get('search_limit', 25)
    automonitor = data.get('automonitor', True)

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

    # Форматируем автомониторинг
    automonitor_text = "включен 🔔" if automonitor else "выключен 🔕"

    return (
        f"<b>Текущие настройки:</b>\n"
        f"📦 Тип: <b>{tender_type}</b>\n"
        f"🔑 Слова: <b>{', '.join(keywords[:5]) if keywords else 'не указаны'}</b>\n"
        f"💰 Бюджет: <b>{budget_text}</b>\n"
        f"📍 Регион: <b>{region_text}</b>\n"
        f"📜 Закон: <b>{law_type}</b>\n"
        f"🚫 Исключения: <b>{exclude_text}</b>\n"
        f"🔍 Поиск: <b>{search_limit} тендеров</b>\n"
        f"📡 Автомониторинг: <b>{automonitor_text}</b>"
    )


def get_tender_type_keyboard(selected: list = None) -> InlineKeyboardMarkup:
    """Клавиатура выбора типа закупки с множественным выбором."""
    if selected is None:
        selected = []

    keyboard = []
    row = []

    # Типы без "any" - его обрабатываем отдельно
    selectable_types = {k: v for k, v in TENDER_TYPES.items() if k != 'any'}

    for type_code, type_info in selectable_types.items():
        is_selected = type_code in selected
        check = "✅ " if is_selected else ""
        text = f"{check}{type_info['icon']} {type_info['name']}"
        row.append(InlineKeyboardButton(
            text=text,
            callback_data=f"ew_type_toggle:{type_code}"
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    # Кнопка "Любые" - сбрасывает выбор
    keyboard.append([
        InlineKeyboardButton(
            text="📋 Любые (сбросить выбор)",
            callback_data="ew_type_toggle:any"
        )
    ])

    # Кнопка продолжить (если что-то выбрано или идём с "любые")
    keyboard.append([
        InlineKeyboardButton(
            text="➡️ Продолжить",
            callback_data="ew_type_continue"
        )
    ])

    keyboard.append([
        InlineKeyboardButton(text="« Отмена", callback_data="sniper_menu")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_region_keyboard(selected_districts: list = None) -> InlineKeyboardMarkup:
    """Клавиатура для выбора региона с множественным выбором."""
    if selected_districts is None:
        selected_districts = []

    federal_districts = get_all_federal_districts()

    keyboard = []
    row = []

    # federal_districts - это список словарей: [{"name": "Центральный", "code": "ЦФО", "regions_count": 18}, ...]
    for fd in federal_districts:
        fd_name = fd['name']
        is_selected = fd_name in selected_districts
        check = "✅ " if is_selected else ""
        text = f"{check}🗺 {fd_name}"

        row.append(InlineKeyboardButton(
            text=text,
            callback_data=f"ew_fd_toggle:{fd_name}"
        ))

        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    # Кнопка "Вся Россия" - сбрасывает выбор
    keyboard.append([
        InlineKeyboardButton(text="🌍 Вся Россия (сбросить)", callback_data="ew_region_toggle:all")
    ])

    # Кнопка продолжить
    selected_count = len(selected_districts)
    continue_text = f"➡️ Продолжить ({selected_count} выбрано)" if selected_count else "➡️ Продолжить (вся Россия)"
    keyboard.append([
        InlineKeyboardButton(text=continue_text, callback_data="ew_region_continue")
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


def get_search_limit_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора количества тендеров для поиска."""
    keyboard = []
    row = []

    for limit_info in SEARCH_LIMITS:
        text = f"{limit_info['icon']} {limit_info['label']}"
        row.append(InlineKeyboardButton(
            text=text,
            callback_data=f"ew_limit:{limit_info['value']}"
        ))
        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton(text="« Назад", callback_data="ew_back:exclude")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_automonitor_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора автомониторинга."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Да, отслеживать новые тендеры", callback_data="ew_monitor:yes")],
        [InlineKeyboardButton(text="🔕 Нет, только разовый поиск", callback_data="ew_monitor:no")],
        [InlineKeyboardButton(text="« Назад", callback_data="ew_back:limit")],
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
        [
            InlineKeyboardButton(text="🔍 Поиск", callback_data="ew_edit:limit"),
            InlineKeyboardButton(text="📡 Мониторинг", callback_data="ew_edit:monitor"),
        ],
        [InlineKeyboardButton(text="🚀 Создать фильтр", callback_data="ew_confirm:create")],
        [InlineKeyboardButton(text="« Отмена", callback_data="sniper_menu")],
    ])


def get_industry_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора отрасли (для архивного поиска)."""
    keyboard = []

    for code, industry in INDUSTRY_TEMPLATES.items():
        keyboard.append([
            InlineKeyboardButton(
                text=f"{industry['icon']} {industry['name']}",
                callback_data=f"sw_industry:{code}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(text="🔍 Произвольный поиск", callback_data="sw_industry:custom")
    ])
    keyboard.append([
        InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_suggestions_keyboard(industry_code: str) -> InlineKeyboardMarkup:
    """Клавиатура с предложениями ключевых слов для отрасли."""
    industry = INDUSTRY_TEMPLATES.get(industry_code, {})
    suggestions = industry.get('suggestions', [])

    keyboard = []
    for suggestion in suggestions:
        keyboard.append([
            InlineKeyboardButton(
                text=f"🔎 {suggestion}",
                callback_data=f"sw_suggest:{suggestion}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(text="✍️ Свои ключевые слова", callback_data="sw_custom_keywords")
    ])
    keyboard.append([
        InlineKeyboardButton(text="« Назад", callback_data="arch_back_to_period")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


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
                    [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
                    [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")]
                ])
            )
            return

        # 🆕 Проверяем наличие черновика
        draft = await get_draft(callback.from_user.id)
        if draft and draft.get('draft_data'):
            draft_data = draft['draft_data']
            step_name = get_step_name(draft.get('current_step', ''))
            keywords_preview = ', '.join(draft_data.get('keywords', [])[:3]) or 'не указаны'

            await callback.message.edit_text(
                "📝 <b>Найден незавершенный фильтр</b>\n\n"
                f"Последний шаг: <b>{step_name}</b>\n"
                f"Ключевые слова: <b>{keywords_preview}</b>\n\n"
                "Хотите продолжить или начать заново?",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="▶️ Продолжить", callback_data="ew_draft:continue")],
                    [InlineKeyboardButton(text="🔄 Начать заново", callback_data="ew_draft:new")],
                    [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")]
                ])
            )
            return

        # Начинаем с чистого листа
        await start_fresh_wizard(callback, state)

    except Exception as e:
        logger.error(f"Error starting extended wizard: {e}", exc_info=True)
        await callback.message.answer("❌ Произошла ошибка. Попробуйте позже.")


async def start_fresh_wizard(callback: CallbackQuery, state: FSMContext):
    """Начинает wizard с чистого листа."""
    # Очищаем state и инициализируем defaults
    await state.clear()
    await state.update_data(
        tender_type=None,
        tender_type_name='Любые',
        selected_types=[],  # 🆕 Для множественного выбора типов
        selected_districts=[],  # 🆕 Для множественного выбора округов
        keywords=[],
        price_min=None,
        price_max=None,
        regions=[],
        law_type=None,
        law_type_name='Любой',
        exclude_keywords=[],
        search_limit=25,
        automonitor=True
    )
    await state.set_state(ExtendedWizardStates.select_tender_type)

    await callback.message.edit_text(
        "🎯 <b>Создание фильтра</b>\n\n"
        "<b>Шаг 1/8:</b> Что ищем?\n\n"
        "Выберите один или несколько типов закупки:",
        parse_mode="HTML",
        reply_markup=get_tender_type_keyboard([])
    )


@router.callback_query(F.data == "ew_draft:continue")
async def continue_from_draft(callback: CallbackQuery, state: FSMContext):
    """Продолжает создание фильтра из черновика."""
    await callback.answer("Восстанавливаю прогресс...")

    try:
        draft = await get_draft(callback.from_user.id)
        if not draft or not draft.get('draft_data'):
            await start_fresh_wizard(callback, state)
            return

        draft_data = draft['draft_data']
        current_step = draft.get('current_step', 'select_tender_type')

        # Восстанавливаем state
        await state.clear()
        await state.update_data(**draft_data)

        # Определяем следующий шаг на основе сохраненного
        step_state_map = {
            'select_tender_type': ExtendedWizardStates.select_tender_type,
            'enter_keywords': ExtendedWizardStates.enter_keywords,
            'enter_budget_min': ExtendedWizardStates.enter_budget_min,
            'enter_budget_max': ExtendedWizardStates.enter_budget_max,
            'confirm_budget': ExtendedWizardStates.confirm_budget,
            'select_region': ExtendedWizardStates.select_region,
            'select_law': ExtendedWizardStates.select_law,
            'enter_excluded': ExtendedWizardStates.enter_excluded,
            'select_search_limit': ExtendedWizardStates.select_search_limit,
            'select_automonitor': ExtendedWizardStates.select_automonitor,
            'confirm_create': ExtendedWizardStates.confirm_create,
        }

        target_state = step_state_map.get(current_step, ExtendedWizardStates.select_tender_type)
        await state.set_state(target_state)

        # Показываем соответствующий шаг
        await show_step_for_state(callback, state, current_step, draft_data)

    except Exception as e:
        logger.error(f"Error continuing from draft: {e}", exc_info=True)
        await callback.message.answer("❌ Не удалось восстановить прогресс. Начинаем заново.")
        await start_fresh_wizard(callback, state)


@router.callback_query(F.data == "ew_draft:new")
async def start_new_discard_draft(callback: CallbackQuery, state: FSMContext):
    """Начинает новый фильтр, удаляя черновик."""
    await callback.answer("Начинаю заново...")
    await delete_draft(callback.from_user.id)
    await start_fresh_wizard(callback, state)


async def show_step_for_state(callback: CallbackQuery, state: FSMContext, step: str, data: dict):
    """Показывает UI для указанного шага."""
    settings_text = get_current_settings_text(data)

    if step == 'select_tender_type':
        selected_types = data.get('selected_types', [])
        await callback.message.edit_text(
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"<b>Шаг 1/8:</b> Что ищем?\n\n"
            f"Выберите один или несколько типов закупки:",
            parse_mode="HTML",
            reply_markup=get_tender_type_keyboard(selected_types)
        )
    elif step == 'enter_keywords':
        await callback.message.edit_text(
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"{settings_text}\n\n"
            f"<b>Шаг 2/8:</b> Введите ключевые слова\n\n"
            f"Укажите через запятую, что вы ищете.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="ew_back:type")]
            ])
        )
    elif step in ('enter_budget_min', 'enter_budget_max', 'confirm_budget'):
        await callback.message.edit_text(
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"{settings_text}\n\n"
            f"<b>Шаг 3/8:</b> Укажите бюджет\n\n"
            f"Введите <b>минимальную</b> сумму контракта (в рублях).\n\n"
            f"Примеры:\n"
            f"• 100000 (100 тыс)\n"
            f"• 1000000 (1 млн)\n"
            f"• 0 (без минимума)\n\n"
            f"Или нажмите «Пропустить» для любого бюджета.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏭ Пропустить (любой бюджет)", callback_data="ew_budget:skip_all")],
                [InlineKeyboardButton(text="« Назад", callback_data="ew_back:keywords")]
            ])
        )
    elif step == 'select_region':
        selected_districts = data.get('selected_districts', [])
        await callback.message.edit_text(
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"{settings_text}\n\n"
            f"<b>Шаг 4/8:</b> Выберите один или несколько регионов:",
            parse_mode="HTML",
            reply_markup=get_region_keyboard(selected_districts)
        )
    elif step == 'select_law':
        await callback.message.edit_text(
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"{settings_text}\n\n"
            f"<b>Шаг 5/8:</b> Выберите закон",
            parse_mode="HTML",
            reply_markup=get_law_keyboard()
        )
    elif step == 'enter_excluded':
        await callback.message.edit_text(
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"{settings_text}\n\n"
            f"<b>Шаг 6/8:</b> Исключения\n\n"
            f"Какие слова исключить из поиска? (или пропустите)",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏭ Пропустить", callback_data="ew_exclude:skip")],
                [InlineKeyboardButton(text="« Назад", callback_data="ew_back:law")]
            ])
        )
    elif step == 'select_search_limit':
        await callback.message.edit_text(
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"{settings_text}\n\n"
            f"<b>Шаг 7/8:</b> Сколько тендеров искать?",
            parse_mode="HTML",
            reply_markup=get_search_limit_keyboard()
        )
    elif step == 'select_automonitor':
        await callback.message.edit_text(
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"{settings_text}\n\n"
            f"<b>Шаг 7/8:</b> Включить автомониторинг?\n\n"
            f"Бот будет автоматически искать новые тендеры по этому фильтру.",
            parse_mode="HTML",
            reply_markup=get_automonitor_keyboard()
        )
    elif step == 'confirm_create':
        await callback.message.edit_text(
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"{settings_text}\n\n"
            f"<b>Шаг 8/8:</b> Всё верно?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Создать и искать", callback_data="ew_confirm:create")],
                [InlineKeyboardButton(text="« Назад", callback_data="ew_back:automonitor")]
            ])
        )


# ============================================
# ШАГ 1: ТИП ЗАКУПКИ
# ============================================

@router.callback_query(F.data.startswith("ew_type:"))
async def handle_tender_type_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора типа закупки (legacy single-select)."""
    await callback.answer()

    type_code = callback.data.split(":")[1]
    type_info = TENDER_TYPES.get(type_code, TENDER_TYPES['any'])

    # Сохраняем выбор
    tender_types_list = [type_info['value']] if type_info['value'] else []
    await state.update_data(
        tender_type=tender_types_list,
        tender_type_name=type_info['name'],
        selected_types=[type_code] if type_code != 'any' else []
    )

    # 🆕 Автосохранение черновика
    data = await state.get_data()
    await save_draft(callback.from_user.id, data, 'enter_keywords')

    # Переходим к шагу 2: ключевые слова
    await state.set_state(ExtendedWizardStates.enter_keywords)

    await callback.message.edit_text(
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ Тип: <b>{type_info['icon']} {type_info['name']}</b>\n\n"
        f"<b>Шаг 2/8:</b> Введите ключевые слова\n\n"
        f"Укажите через запятую, что вы ищете.\n"
        f"Например: <i>Lenovo, ноутбуки, ThinkPad</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="ew_back:type")]
        ])
    )


# ============================================
# ШАГ 1: МНОЖЕСТВЕННЫЙ ВЫБОР ТИПОВ ЗАКУПКИ
# ============================================

@router.callback_query(F.data.startswith("ew_type_toggle:"))
async def toggle_tender_type(callback: CallbackQuery, state: FSMContext):
    """Переключение типа закупки (множественный выбор)."""
    type_code = callback.data.split(":")[1]
    data = await state.get_data()
    selected = data.get('selected_types', [])

    if type_code == 'any':
        # "Любые" сбрасывает весь выбор
        selected = []
        await callback.answer("Выбор сброшен")
    else:
        # Toggle выбранного типа
        if type_code in selected:
            selected.remove(type_code)
            await callback.answer(f"{TENDER_TYPES[type_code]['name']} убран")
        else:
            selected.append(type_code)
            await callback.answer(f"{TENDER_TYPES[type_code]['name']} добавлен")

    await state.update_data(selected_types=selected)

    # Обновляем клавиатуру
    await callback.message.edit_text(
        "🎯 <b>Создание фильтра</b>\n\n"
        "<b>Шаг 1/8:</b> Что ищем?\n\n"
        "Выберите один или несколько типов закупки:",
        parse_mode="HTML",
        reply_markup=get_tender_type_keyboard(selected)
    )


@router.callback_query(F.data == "ew_type_continue")
async def continue_after_type_selection(callback: CallbackQuery, state: FSMContext):
    """Продолжить после выбора типов закупки."""
    await callback.answer()

    data = await state.get_data()
    selected = data.get('selected_types', [])

    # Формируем список значений для поиска
    if selected:
        tender_types_list = [TENDER_TYPES[code]['value'] for code in selected if TENDER_TYPES[code]['value']]
        type_names = [TENDER_TYPES[code]['name'] for code in selected]
        type_name_str = ', '.join(type_names)
    else:
        tender_types_list = []
        type_name_str = 'Любые'

    await state.update_data(
        tender_type=tender_types_list,
        tender_type_name=type_name_str
    )

    # 🆕 Автосохранение черновика
    data = await state.get_data()
    await save_draft(callback.from_user.id, data, 'enter_keywords')

    # Переходим к шагу 2: ключевые слова
    await state.set_state(ExtendedWizardStates.enter_keywords)

    await callback.message.edit_text(
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ Тип: <b>{type_name_str}</b>\n\n"
        f"<b>Шаг 2/8:</b> Введите ключевые слова\n\n"
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

    # 🆕 Автосохранение черновика
    data = await state.get_data()
    await save_draft(message.from_user.id, data, 'enter_budget_min')

    # Переходим к шагу 3: бюджет - сразу запрашиваем минимум
    await state.set_state(ExtendedWizardStates.enter_budget_min)

    await message.answer(
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
        f"✅ Слова: <b>{', '.join(keywords[:5])}</b>\n\n"
        f"<b>Шаг 3/8:</b> Укажите бюджет\n\n"
        f"Введите <b>минимальную</b> сумму контракта (в рублях).\n\n"
        f"Примеры:\n"
        f"• 100000 (100 тыс)\n"
        f"• 1000000 (1 млн)\n"
        f"• 0 (без минимума)\n\n"
        f"Или нажмите «Пропустить» для любого бюджета.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить (любой бюджет)", callback_data="ew_budget:skip_all")],
            [InlineKeyboardButton(text="« Назад", callback_data="ew_back:keywords")]
        ])
    )


# ============================================
# ШАГ 3: БЮДЖЕТ (мин → макс → подтверждение)
# ============================================

@router.callback_query(F.data == "ew_budget:skip_all")
async def skip_budget_entirely(callback: CallbackQuery, state: FSMContext):
    """Пропуск бюджета полностью - переход к региону."""
    logger.info(f"[BUDGET] skip_all clicked by user {callback.from_user.id}")
    await callback.answer("Пропускаю бюджет...")
    await state.update_data(price_min=None, price_max=None)
    await go_to_region_step(callback.message, state)


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
                [InlineKeyboardButton(text="⏭ Пропустить (любой бюджет)", callback_data="ew_budget:skip_all")],
                [InlineKeyboardButton(text="« Назад", callback_data="ew_back:keywords")]
            ])
        )
        return

    await state.update_data(price_min=price_min)
    await state.set_state(ExtendedWizardStates.enter_budget_max)

    await message.answer(
        f"💰 <b>Укажите бюджет</b>\n\n"
        f"✅ Минимум: <b>{format_price(price_min)}</b>\n\n"
        f"Теперь введите <b>максимальную</b> сумму контракта (в рублях).\n\n"
        f"Примеры:\n"
        f"• 1000000 (1 млн)\n"
        f"• 10000000 (10 млн)\n"
        f"• 0 (без максимума)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад (изменить минимум)", callback_data="ew_back:budget_min")]
        ])
    )


@router.message(ExtendedWizardStates.enter_budget_max)
async def handle_budget_max_input(message: Message, state: FSMContext):
    """Обработка ввода максимального бюджета → подтверждение."""
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
                [InlineKeyboardButton(text="« Назад (изменить минимум)", callback_data="ew_back:budget_min")]
            ])
        )
        return

    await state.update_data(price_max=price_max)

    # Проверка что max >= min
    data = await state.get_data()
    price_min = data.get('price_min')

    if price_min and price_max and price_max < price_min:
        await message.answer(
            f"⚠️ Максимум ({format_price(price_max)}) меньше минимума ({format_price(price_min)}).\n\n"
            f"Введите корректную максимальную сумму.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад (изменить минимум)", callback_data="ew_back:budget_min")]
            ])
        )
        return

    # Показываем подтверждение
    await show_budget_confirmation(message, state)


async def show_budget_confirmation(message, state: FSMContext):
    """Показать подтверждение бюджета."""
    await state.set_state(ExtendedWizardStates.confirm_budget)
    data = await state.get_data()

    price_min = data.get('price_min')
    price_max = data.get('price_max')

    # Форматируем диапазон
    if price_min and price_max:
        budget_text = f"от {format_price(price_min)} до {format_price(price_max)}"
    elif price_max:
        budget_text = f"до {format_price(price_max)}"
    elif price_min:
        budget_text = f"от {format_price(price_min)}"
    else:
        budget_text = "без ограничений"

    text = (
        f"💰 <b>Подтверждение бюджета</b>\n\n"
        f"Вы указали диапазон:\n"
        f"<b>{budget_text}</b>\n\n"
        f"Всё верно?"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, продолжить", callback_data="ew_budget:confirm")],
        [InlineKeyboardButton(text="✏️ Изменить минимум", callback_data="ew_back:budget_min")],
        [InlineKeyboardButton(text="✏️ Изменить максимум", callback_data="ew_back:budget_max")],
    ])

    # Проверяем, можно ли редактировать сообщение (только для сообщений бота)
    can_edit = hasattr(message, 'from_user') and message.from_user and message.from_user.is_bot

    if can_edit:
        try:
            await message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(F.data == "ew_budget:confirm")
async def confirm_budget(callback: CallbackQuery, state: FSMContext):
    """Подтверждение бюджета → переход к региону."""
    await callback.answer()
    await go_to_region_step(callback.message, state)


@router.callback_query(F.data == "ew_back:budget_min")
async def back_to_budget_min(callback: CallbackQuery, state: FSMContext):
    """Возврат к вводу минимума."""
    await callback.answer()
    await state.set_state(ExtendedWizardStates.enter_budget_min)

    data = await state.get_data()

    await callback.message.edit_text(
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
        f"✅ Слова: <b>{', '.join(data.get('keywords', [])[:5])}</b>\n\n"
        f"<b>Шаг 3/8:</b> Укажите бюджет\n\n"
        f"Введите <b>минимальную</b> сумму контракта (в рублях).\n\n"
        f"Примеры:\n"
        f"• 100000 (100 тыс)\n"
        f"• 1000000 (1 млн)\n"
        f"• 0 (без минимума)\n\n"
        f"Или нажмите «Пропустить» для любого бюджета.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить (любой бюджет)", callback_data="ew_budget:skip_all")],
            [InlineKeyboardButton(text="« Назад", callback_data="ew_back:keywords")]
        ])
    )


@router.callback_query(F.data == "ew_back:budget_max")
async def back_to_budget_max(callback: CallbackQuery, state: FSMContext):
    """Возврат к вводу максимума."""
    await callback.answer()
    await state.set_state(ExtendedWizardStates.enter_budget_max)

    data = await state.get_data()
    price_min = data.get('price_min')

    await callback.message.edit_text(
        f"💰 <b>Укажите бюджет</b>\n\n"
        f"✅ Минимум: <b>{format_price(price_min)}</b>\n\n"
        f"Введите <b>максимальную</b> сумму контракта (в рублях).\n\n"
        f"Примеры:\n"
        f"• 1000000 (1 млн)\n"
        f"• 10000000 (10 млн)\n"
        f"• 0 (без максимума)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад (изменить минимум)", callback_data="ew_back:budget_min")]
        ])
    )


async def go_to_region_step(message, state: FSMContext):
    """Переход к шагу выбора региона."""
    data = await state.get_data()

    # 🆕 Автосохранение черновика
    user_id = message.from_user.id if hasattr(message, 'from_user') else message.chat.id
    await save_draft(user_id, data, 'select_region')

    await state.set_state(ExtendedWizardStates.select_region)

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

    # Получаем выбранные округа (если есть)
    selected_districts = data.get('selected_districts', [])

    text = (
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
        f"✅ Слова: <b>{', '.join(data.get('keywords', [])[:3])}</b>\n"
        f"✅ Бюджет: <b>{budget_text}</b>\n\n"
        f"<b>Шаг 4/8:</b> Выберите один или несколько регионов:"
    )

    # Проверяем, можно ли редактировать (только сообщения бота)
    can_edit = hasattr(message, 'from_user') and message.from_user and message.from_user.is_bot
    if can_edit:
        try:
            await message.edit_text(text, parse_mode="HTML", reply_markup=get_region_keyboard(selected_districts))
        except Exception:
            await message.answer(text, parse_mode="HTML", reply_markup=get_region_keyboard(selected_districts))
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=get_region_keyboard(selected_districts))


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
    """Выбор федерального округа (legacy single-select)."""
    await callback.answer()

    # fd_name теперь передается напрямую (например, "Центральный")
    fd_name = callback.data.split(":")[1]
    regions = get_regions_by_district(fd_name)

    await state.update_data(regions=regions, region_name=fd_name, selected_districts=[fd_name])
    await go_to_law_step(callback.message, state)


# ============================================
# ШАГ 4: МНОЖЕСТВЕННЫЙ ВЫБОР РЕГИОНОВ
# ============================================

@router.callback_query(F.data.startswith("ew_fd_toggle:"))
async def toggle_federal_district(callback: CallbackQuery, state: FSMContext):
    """Переключение федерального округа (множественный выбор)."""
    fd_name = callback.data.split(":")[1]
    data = await state.get_data()
    selected = data.get('selected_districts', [])

    # Toggle выбранного округа
    if fd_name in selected:
        selected.remove(fd_name)
        await callback.answer(f"{fd_name} убран")
    else:
        selected.append(fd_name)
        await callback.answer(f"{fd_name} добавлен")

    await state.update_data(selected_districts=selected)

    # Обновляем клавиатуру
    data = await state.get_data()
    settings_text = (
        f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
        f"✅ Слова: <b>{', '.join(data.get('keywords', [])[:3])}</b>\n"
        f"✅ Бюджет: <b>{_format_budget_text(data)}</b>"
    )

    await callback.message.edit_text(
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"{settings_text}\n\n"
        f"<b>Шаг 4/8:</b> Выберите один или несколько регионов:",
        parse_mode="HTML",
        reply_markup=get_region_keyboard(selected)
    )


@router.callback_query(F.data == "ew_region_toggle:all")
async def reset_region_selection(callback: CallbackQuery, state: FSMContext):
    """Сброс выбора регионов."""
    await callback.answer("Выбор сброшен - вся Россия")
    await state.update_data(selected_districts=[])

    data = await state.get_data()
    settings_text = (
        f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
        f"✅ Слова: <b>{', '.join(data.get('keywords', [])[:3])}</b>\n"
        f"✅ Бюджет: <b>{_format_budget_text(data)}</b>"
    )

    await callback.message.edit_text(
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"{settings_text}\n\n"
        f"<b>Шаг 4/8:</b> Выберите один или несколько регионов:",
        parse_mode="HTML",
        reply_markup=get_region_keyboard([])
    )


@router.callback_query(F.data == "ew_region_continue")
async def continue_after_region_selection(callback: CallbackQuery, state: FSMContext):
    """Продолжить после выбора регионов."""
    await callback.answer()

    data = await state.get_data()
    selected = data.get('selected_districts', [])

    # Собираем все регионы из выбранных округов
    all_regions = []
    for fd_name in selected:
        all_regions.extend(get_regions_by_district(fd_name))

    # Формируем название для отображения
    if selected:
        if len(selected) == 1:
            region_name = selected[0]
        else:
            region_name = f"{len(selected)} округов"
    else:
        region_name = "Вся Россия"

    await state.update_data(regions=all_regions, region_name=region_name)
    await go_to_law_step(callback.message, state)


def _format_budget_text(data: dict) -> str:
    """Вспомогательная функция для форматирования бюджета."""
    price_min = data.get('price_min')
    price_max = data.get('price_max')
    if price_min and price_max:
        return f"{format_price(price_min)} - {format_price(price_max)}"
    elif price_max:
        return f"до {format_price(price_max)}"
    elif price_min:
        return f"от {format_price(price_min)}"
    else:
        return "без ограничений"


async def go_to_law_step(message, state: FSMContext):
    """Переход к шагу выбора закона."""
    data = await state.get_data()

    # 🆕 Автосохранение черновика
    user_id = message.from_user.id if hasattr(message, 'from_user') else message.chat.id
    await save_draft(user_id, data, 'select_law')

    await state.set_state(ExtendedWizardStates.select_law)

    text = (
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
        f"✅ Слова: <b>{', '.join(data.get('keywords', [])[:3])}</b>\n"
        f"✅ Регион: <b>{data.get('region_name', 'Вся Россия')}</b>\n\n"
        f"<b>Шаг 5/8:</b> Выберите закон"
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
    data = await state.get_data()

    # 🆕 Автосохранение черновика
    user_id = message.from_user.id if hasattr(message, 'from_user') else message.chat.id
    await save_draft(user_id, data, 'enter_excluded')

    await state.set_state(ExtendedWizardStates.enter_excluded)

    text = (
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
        f"✅ Слова: <b>{', '.join(data.get('keywords', [])[:3])}</b>\n"
        f"✅ Закон: <b>{data.get('law_type_name', 'Любой')}</b>\n\n"
        f"<b>Шаг 6/8:</b> Исключить слова\n\n"
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
    await go_to_search_settings_step(message, state)


@router.callback_query(F.data == "ew_exclude:skip")
async def skip_exclusions(callback: CallbackQuery, state: FSMContext):
    """Пропуск исключений."""
    await callback.answer()
    await state.update_data(exclude_keywords=[])
    await go_to_search_settings_step(callback.message, state)


# ============================================
# ШАГ 7: НАСТРОЙКИ ПОИСКА
# ============================================

async def go_to_search_settings_step(message, state: FSMContext):
    """Переход к шагу настроек поиска (количество тендеров)."""
    data = await state.get_data()

    # 🆕 Автосохранение черновика
    user_id = message.from_user.id if hasattr(message, 'from_user') else message.chat.id
    await save_draft(user_id, data, 'select_search_limit')

    await state.set_state(ExtendedWizardStates.select_search_limit)

    text = (
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n"
        f"✅ Слова: <b>{', '.join(data.get('keywords', [])[:3])}</b>\n\n"
        f"<b>Шаг 7/8:</b> Настройки поиска\n\n"
        f"Сколько тендеров найти при мгновенном поиске?"
    )

    can_edit = hasattr(message, 'from_user') and message.from_user and message.from_user.is_bot
    if can_edit:
        try:
            await message.edit_text(text, parse_mode="HTML", reply_markup=get_search_limit_keyboard())
        except Exception:
            await message.answer(text, parse_mode="HTML", reply_markup=get_search_limit_keyboard())
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=get_search_limit_keyboard())


@router.callback_query(F.data.startswith("ew_limit:"))
async def handle_search_limit_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора количества тендеров."""
    await callback.answer()

    limit_value = int(callback.data.split(":")[1])
    await state.update_data(search_limit=limit_value)

    # Переходим к выбору автомониторинга
    await go_to_automonitor_step(callback.message, state)


async def go_to_automonitor_step(message, state: FSMContext):
    """Переход к шагу выбора автомониторинга."""
    data = await state.get_data()

    # 🆕 Автосохранение черновика
    user_id = message.from_user.id if hasattr(message, 'from_user') else message.chat.id
    await save_draft(user_id, data, 'select_automonitor')

    await state.set_state(ExtendedWizardStates.select_automonitor)

    search_limit = data.get('search_limit', 25)

    text = (
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"✅ Поиск: <b>{search_limit} тендеров</b>\n\n"
        f"<b>Шаг 7/8:</b> Автомониторинг\n\n"
        f"Хотите получать уведомления о новых тендерах по этому фильтру?\n\n"
        f"🔔 <b>Да</b> — система будет автоматически искать новые тендеры и отправлять вам уведомления\n"
        f"🔕 <b>Нет</b> — только разовый поиск без отслеживания"
    )

    can_edit = hasattr(message, 'from_user') and message.from_user and message.from_user.is_bot
    if can_edit:
        try:
            await message.edit_text(text, parse_mode="HTML", reply_markup=get_automonitor_keyboard())
        except Exception:
            await message.answer(text, parse_mode="HTML", reply_markup=get_automonitor_keyboard())
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=get_automonitor_keyboard())


@router.callback_query(F.data.startswith("ew_monitor:"))
async def handle_automonitor_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора автомониторинга."""
    await callback.answer()

    choice = callback.data.split(":")[1]
    automonitor = (choice == "yes")
    await state.update_data(automonitor=automonitor)

    # Переходим к подтверждению
    await go_to_confirm_step(callback.message, state)


async def go_to_confirm_step(message, state: FSMContext):
    """Переход к шагу подтверждения."""
    data = await state.get_data()

    # 🆕 Автосохранение черновика
    user_id = message.from_user.id if hasattr(message, 'from_user') else message.chat.id
    await save_draft(user_id, data, 'confirm_create')

    await state.set_state(ExtendedWizardStates.confirm_create)

    settings_text = get_current_settings_text(data)

    text = (
        f"🎯 <b>Создание фильтра</b>\n\n"
        f"<b>Шаг 8/8:</b> Подтверждение\n\n"
        f"{settings_text}\n\n"
        f"Всё верно? Нажмите «Создать» или измените настройки."
    )

    can_edit = hasattr(message, 'from_user') and message.from_user and message.from_user.is_bot
    if can_edit:
        try:
            await message.edit_text(text, parse_mode="HTML", reply_markup=get_confirm_keyboard())
        except Exception:
            await message.answer(text, parse_mode="HTML", reply_markup=get_confirm_keyboard())
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
        data = await state.get_data()
        selected_types = data.get('selected_types', [])
        await callback.message.edit_text(
            "📦 <b>Изменить тип закупки</b>\n\n"
            "Выберите один или несколько типов:",
            parse_mode="HTML",
            reply_markup=get_tender_type_keyboard(selected_types)
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
        await state.set_state(ExtendedWizardStates.enter_budget_min)
        await callback.message.edit_text(
            "💰 <b>Изменить бюджет</b>\n\n"
            "Введите <b>минимальную</b> сумму контракта (в рублях).\n\n"
            "Примеры:\n"
            "• 100000 (100 тыс)\n"
            "• 1000000 (1 млн)\n"
            "• 0 (без минимума)",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏭ Пропустить (любой бюджет)", callback_data="ew_budget:skip_all")],
                [InlineKeyboardButton(text="« Отмена", callback_data="ew_back:confirm")]
            ])
        )
    elif param == "region":
        await state.set_state(ExtendedWizardStates.select_region)
        data = await state.get_data()
        selected_districts = data.get('selected_districts', [])
        await callback.message.edit_text(
            "📍 <b>Изменить регион</b>\n\n"
            "Выберите один или несколько регионов:",
            parse_mode="HTML",
            reply_markup=get_region_keyboard(selected_districts)
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
    elif param == "limit":
        await state.set_state(ExtendedWizardStates.select_search_limit)
        await callback.message.edit_text(
            "🔍 <b>Изменить количество тендеров</b>\n\n"
            "Сколько тендеров найти при мгновенном поиске?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔟 10", callback_data="ew_limit:10"),
                    InlineKeyboardButton(text="📊 25", callback_data="ew_limit:25"),
                ],
                [
                    InlineKeyboardButton(text="📈 50", callback_data="ew_limit:50"),
                    InlineKeyboardButton(text="💯 100", callback_data="ew_limit:100"),
                ],
                [InlineKeyboardButton(text="« Отмена", callback_data="ew_back:confirm")]
            ])
        )
    elif param == "monitor":
        await state.set_state(ExtendedWizardStates.select_automonitor)
        await callback.message.edit_text(
            "📡 <b>Изменить автомониторинг</b>\n\n"
            "Отслеживать новые тендеры по этому фильтру?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔔 Да, отслеживать", callback_data="ew_monitor:yes")],
                [InlineKeyboardButton(text="🔕 Нет, только поиск", callback_data="ew_monitor:no")],
                [InlineKeyboardButton(text="« Отмена", callback_data="ew_back:confirm")]
            ])
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
        data = await state.get_data()
        selected_types = data.get('selected_types', [])
        await callback.message.edit_text(
            "🎯 <b>Создание фильтра</b>\n\n"
            "<b>Шаг 1/8:</b> Что ищем?\n\n"
            "Выберите один или несколько типов закупки:",
            parse_mode="HTML",
            reply_markup=get_tender_type_keyboard(selected_types)
        )

    elif target == "keywords":
        data = await state.get_data()
        await state.set_state(ExtendedWizardStates.enter_keywords)
        await callback.message.edit_text(
            f"🎯 <b>Создание фильтра</b>\n\n"
            f"✅ Тип: <b>{data.get('tender_type_name', 'Любые')}</b>\n\n"
            f"<b>Шаг 2/8:</b> Введите ключевые слова\n\n"
            f"Укажите через запятую, что вы ищете:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Назад", callback_data="ew_back:type")]
            ])
        )

    elif target == "budget":
        # Возврат к подтверждению бюджета из региона
        await show_budget_confirmation(callback.message, state)

    elif target == "region":
        await go_to_region_step(callback.message, state)

    elif target == "law":
        await go_to_law_step(callback.message, state)

    elif target == "exclude":
        # Возврат к исключениям (из шага настроек поиска)
        await go_to_exclusions_step(callback.message, state)

    elif target == "limit":
        # Возврат к выбору количества тендеров (из шага автомониторинга)
        await go_to_search_settings_step(callback.message, state)

    elif target == "automonitor":
        # Возврат к выбору автомониторинга (из шага подтверждения)
        await go_to_automonitor_step(callback.message, state)

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
    search_limit = data.get('search_limit', 25)
    automonitor = data.get('automonitor', True)

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
        # is_active зависит от выбора автомониторинга
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
            is_active=automonitor  # False если пользователь выбрал "только поиск"
        )

        logger.info(f"Created filter {filter_id} for user {callback.from_user.id}, automonitor={automonitor}")

        # 🆕 Удаляем черновик после успешного создания фильтра
        await delete_draft(callback.from_user.id)

        # Запускаем мгновенный поиск
        await callback.message.edit_text(
            f"✅ <b>Фильтр создан!</b>\n\n"
            f"📝 Название: {filter_name}\n"
            f"🔑 Слова: {', '.join(keywords[:5])}\n\n"
            f"🔍 Запускаю поиск тендеров ({search_limit} шт.)...",
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

        # Выполняем поиск с указанным лимитом
        searcher = InstantSearch()
        search_results = await searcher.search_by_filter(
            filter_data=filter_data,
            max_tenders=search_limit,
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
            # Разное сообщение в зависимости от автомониторинга
            if automonitor:
                notification_text = "🔔 Вы получите уведомление, когда появятся новые тендеры."
            else:
                notification_text = "ℹ️ Автомониторинг отключен. Вы можете включить его в настройках фильтра."

            await callback.message.edit_text(
                f"✅ <b>Фильтр создан!</b>\n\n"
                f"📝 Название: {filter_name}\n\n"
                f"😔 Пока не найдено подходящих тендеров.\n\n"
                f"{notification_text}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
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
                f"{'🔔 Автомониторинг активирован!' if data.get('automonitor', True) else '🔕 Только разовый поиск'}"
            ),
            parse_mode="HTML"
        )

        await callback.message.answer(
            f"✅ <b>Готово!</b>\n\n"
            f"Фильтр <b>{filter_name}</b> создан" + (" и активирован.\n" + "Вы будете получать уведомления о новых тендерах." if data.get('automonitor', True) else ".\nАвтомониторинг отключен — только разовый поиск."),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
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

        # 🆕 Удаляем черновик после успешного создания фильтра
        await delete_draft(callback.from_user.id)

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
            # Разное сообщение в зависимости от автомониторинга
            if automonitor:
                notification_text = "🔔 Вы получите уведомление, когда появятся новые тендеры."
            else:
                notification_text = "ℹ️ Автомониторинг отключен. Вы можете включить его в настройках фильтра."

            await callback.message.edit_text(
                f"✅ <b>Фильтр создан!</b>\n\n"
                f"📝 Название: {filter_name}\n\n"
                f"😔 Пока не найдено подходящих тендеров.\n\n"
                f"{notification_text}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
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
                f"{'🔔 Автомониторинг активирован!' if data.get('automonitor', True) else '🔕 Только разовый поиск'}"
            ),
            parse_mode="HTML"
        )

        await callback.message.answer(
            f"✅ <b>Готово!</b>\n\n"
            f"Фильтр <b>{filter_name}</b> создан" + (" и активирован.\n" + "Вы будете получать уведомления о новых тендерах." if data.get('automonitor', True) else ".\nАвтомониторинг отключен — только разовый поиск."),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
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
