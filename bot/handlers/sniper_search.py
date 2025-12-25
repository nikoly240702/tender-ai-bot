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
from bot.utils.access_check import require_feature
from tender_sniper.instant_search import InstantSearch
from tender_sniper.regions import (
    get_all_federal_districts,
    get_regions_by_district,
    parse_regions_input,
    format_regions_list
)
from bot.schemas.filters import FilterCreate, sanitize_html
from pydantic import ValidationError

logger = logging.getLogger(__name__)

router = Router()


# ============================================
# 🧪 БЕТА: Сохранение черновиков фильтров
# ============================================

async def save_wizard_draft(telegram_id: int, state: FSMContext, current_step: str = None):
    """
    Сохранить текущее состояние wizard в БД.

    Args:
        telegram_id: Telegram ID пользователя
        state: FSMContext с данными
        current_step: Название текущего шага (для отображения)
    """
    try:
        data = await state.get_data()
        if not data:
            return

        db = await get_sniper_db()
        await db.save_filter_draft(
            telegram_id=telegram_id,
            draft_data=data,
            current_step=current_step
        )
    except Exception as e:
        logger.warning(f"Не удалось сохранить черновик: {e}")


async def check_and_offer_draft(
    callback: CallbackQuery,
    state: FSMContext,
    db,
    with_instant_search: bool
) -> bool:
    """
    Проверить наличие черновика и предложить продолжить.

    Returns:
        True если предложили продолжить, False если начинаем с нуля
    """
    try:
        draft = await db.get_filter_draft(callback.from_user.id)
        if draft and draft.get('draft_data'):
            # Есть черновик - предлагаем продолжить
            draft_data = draft['draft_data']
            filter_name = draft_data.get('filter_name', 'Без названия')
            current_step = draft.get('current_step', 'неизвестно')

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Продолжить", callback_data=f"draft_resume_{1 if with_instant_search else 0}")],
                [InlineKeyboardButton(text="🔄 Начать заново", callback_data=f"draft_discard_{1 if with_instant_search else 0}")],
                [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")]
            ])

            await callback.message.edit_text(
                f"📝 <b>Найден незавершённый фильтр</b> 🧪 БЕТА\n\n"
                f"Название: <b>{filter_name}</b>\n"
                f"Последний шаг: <i>{current_step}</i>\n\n"
                f"Хотите продолжить с места остановки?",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            return True
    except Exception as e:
        logger.warning(f"Ошибка проверки черновика: {e}")

    return False


# ============================================
# FSM States для нового процесса
# ============================================

class FilterSearchStates(StatesGroup):
    """Состояния для создания фильтра с поиском."""
    waiting_for_filter_name = State()
    waiting_for_keywords = State()
    waiting_for_exclude_keywords = State()
    waiting_for_price_range = State()
    confirm_price_range = State()
    waiting_for_regions = State()
    waiting_for_law_type = State()
    waiting_for_purchase_stage = State()
    waiting_for_purchase_method = State()
    waiting_for_tender_type = State()
    waiting_for_okpd2 = State()
    waiting_for_min_deadline = State()
    waiting_for_customer_keywords = State()
    waiting_for_search_mode = State()  # Выбор режима поиска (точный/расширенный)
    waiting_for_tender_count = State()
    confirm_auto_monitoring = State()


class ArchiveSearchStates(StatesGroup):
    """🧪 БЕТА: Упрощённые состояния для архивного поиска."""
    waiting_for_period = State()      # Шаг 1: Выбор периода
    waiting_for_keywords = State()    # Шаг 2: Ключевые слова
    waiting_for_region = State()      # Шаг 3: Регион (опционально)
    confirm_search = State()          # Шаг 4: Подтверждение


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
        filters = await db.get_user_filters(user['id'], active_only=True)
        # Временно используем жёстко заданные лимиты (TODO: мигрировать get_plan_limits на PostgreSQL)
        max_filters = 5 if user['subscription_tier'] == 'free' else 15

        if len(filters) >= max_filters:
            await callback.message.edit_text(
                f"⚠️ <b>Достигнут лимит фильтров</b>\n\n"
                f"Ваш тариф <b>{user['subscription_tier'].title()}</b> позволяет создать максимум {max_filters} фильтров.\n"
                f"У вас уже создано: {len(filters)}\n\n"
                f"Удалите старые фильтры или обновите подписку.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
                ])
            )
            return

        # Сохраняем что это создание БЕЗ instant search
        await state.update_data(with_instant_search=False)

        # Запускаем процесс создания фильтра
        await state.set_state(FilterSearchStates.waiting_for_filter_name)

        await callback.message.edit_text(
            "➕ <b>Создание фильтра для автомониторинга</b>\n\n"
            "<b>Шаг 1/14:</b> Название фильтра\n\n"
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
        filters = await db.get_user_filters(user['id'], active_only=True)
        # Временно используем жёстко заданные лимиты (TODO: мигрировать get_plan_limits на PostgreSQL)
        max_filters = 5 if user['subscription_tier'] == 'free' else 15

        if len(filters) >= max_filters:
            await callback.message.edit_text(
                f"⚠️ <b>Достигнут лимит фильтров</b>\n\n"
                f"Ваш тариф <b>{user['subscription_tier'].title()}</b> позволяет создать максимум {max_filters} фильтров.\n"
                f"У вас уже создано: {len(filters)}\n\n"
                f"Удалите старые фильтры или обновите подписку.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
                ])
            )
            return

        # Сохраняем что это поиск с instant search
        await state.update_data(with_instant_search=True)

        # Запускаем процесс создания фильтра
        await state.set_state(FilterSearchStates.waiting_for_filter_name)

        await callback.message.edit_text(
            "🎯 <b>Создание фильтра с мгновенным поиском</b>\n\n"
            "<b>Шаг 1/14:</b> Название фильтра\n\n"
            "Придумайте короткое название для вашего фильтра.\n"
            "Например: <i>IT оборудование</i>, <i>Медицинские товары</i>\n\n"
            "💡 Это название поможет вам управлять фильтрами в будущем.",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Error starting filter search: {e}", exc_info=True)
        await callback.message.answer("❌ Произошла ошибка. Попробуйте позже.")


# ============================================
# 🧪 БЕТА: УПРОЩЁННЫЙ АРХИВНЫЙ ПОИСК
# ============================================

@router.callback_query(F.data == "sniper_archive_search")
async def start_archive_search(callback: CallbackQuery, state: FSMContext):
    """
    🧪 БЕТА: Поиск в архиве - упрощённый поток.

    Шаг 1: Выбор периода
    Шаг 2: Ключевые слова
    Шаг 3: Регион (опционально)
    Шаг 4: Поиск
    """
    # Проверяем доступ к архивному поиску (только Premium)
    if not await require_feature(callback, 'archive_search'):
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

        # Инициализируем данные архивного поиска
        await state.clear()
        await state.update_data(archive_mode=True)

        # Шаг 1: Выбор периода
        await state.set_state(ArchiveSearchStates.waiting_for_period)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📅 За 7 дней", callback_data="arch_period_7")],
            [InlineKeyboardButton(text="📅 За 30 дней", callback_data="arch_period_30")],
            [InlineKeyboardButton(text="📅 За 90 дней", callback_data="arch_period_90")],
            [InlineKeyboardButton(text="📅 За 180 дней", callback_data="arch_period_180")],
            [InlineKeyboardButton(text="📅 За всё время", callback_data="arch_period_0")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="sniper_menu")]
        ])

        await callback.message.edit_text(
            "📦 <b>Поиск в архиве</b> 🧪 БЕТА\n\n"
            "<b>Шаг 1/4:</b> Выберите период поиска\n\n"
            "За какое время искать завершённые тендеры?\n\n"
            "💡 Чем больше период, тем дольше поиск.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Error starting archive search: {e}", exc_info=True)
        await callback.message.answer("❌ Произошла ошибка. Попробуйте позже.")


@router.callback_query(F.data.startswith("arch_period_"), ArchiveSearchStates.waiting_for_period)
async def archive_select_period(callback: CallbackQuery, state: FSMContext):
    """Шаг 1: Выбор периода для архивного поиска."""
    await callback.answer()

    period_days = int(callback.data.replace("arch_period_", ""))
    await state.update_data(archive_period_days=period_days)

    # Переходим к шагу 2: Ключевые слова
    await state.set_state(ArchiveSearchStates.waiting_for_keywords)

    period_text = f"за {period_days} дней" if period_days > 0 else "за всё время"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад", callback_data="sniper_archive_search")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="sniper_menu")]
    ])

    await callback.message.edit_text(
        f"📦 <b>Поиск в архиве</b> 🧪 БЕТА\n\n"
        f"<b>Шаг 2/4:</b> Введите ключевые слова\n\n"
        f"📅 Период: <b>{period_text}</b>\n\n"
        f"Введите слова для поиска через запятую:\n"
        f"<i>Например: компьютер, ноутбук, моноблок</i>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.message(ArchiveSearchStates.waiting_for_keywords)
async def archive_process_keywords(message: Message, state: FSMContext):
    """Шаг 2: Обработка ключевых слов."""
    # Проверяем системные кнопки
    if message.text in ["🏠 Главное меню", "🎯 Tender Sniper", "📊 Мои фильтры"]:
        await state.clear()
        return

    keywords_text = message.text.strip()
    if not keywords_text:
        await message.answer("⚠️ Введите хотя бы одно ключевое слово:")
        return

    keywords = [kw.strip() for kw in keywords_text.split(',') if kw.strip()]
    if not keywords:
        await message.answer("⚠️ Введите ключевые слова через запятую:")
        return

    await state.update_data(archive_keywords=keywords)

    # Переходим к шагу 3: Выбор региона
    await state.set_state(ArchiveSearchStates.waiting_for_region)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌍 Вся Россия", callback_data="arch_region_all")],
        [InlineKeyboardButton(text="🏛️ Москва", callback_data="arch_region_77")],
        [InlineKeyboardButton(text="🏛️ Санкт-Петербург", callback_data="arch_region_78")],
        [InlineKeyboardButton(text="🏛️ Московская область", callback_data="arch_region_50")],
        [InlineKeyboardButton(text="📝 Ввести код региона", callback_data="arch_region_custom")],
        [InlineKeyboardButton(text="« Назад", callback_data="arch_back_to_keywords")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="sniper_menu")]
    ])

    data = await state.get_data()
    period_days = data.get('archive_period_days', 30)
    period_text = f"за {period_days} дней" if period_days > 0 else "за всё время"

    await message.answer(
        f"📦 <b>Поиск в архиве</b> 🧪 БЕТА\n\n"
        f"<b>Шаг 3/4:</b> Выберите регион\n\n"
        f"📅 Период: <b>{period_text}</b>\n"
        f"🔑 Слова: <b>{', '.join(keywords[:3])}</b>"
        f"{' (+' + str(len(keywords)-3) + ')' if len(keywords) > 3 else ''}\n\n"
        f"Выберите регион или оставьте «Вся Россия»:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "arch_back_to_keywords", ArchiveSearchStates.waiting_for_region)
async def archive_back_to_keywords(callback: CallbackQuery, state: FSMContext):
    """Возврат к вводу ключевых слов."""
    await callback.answer()

    data = await state.get_data()
    period_days = data.get('archive_period_days', 30)
    period_text = f"за {period_days} дней" if period_days > 0 else "за всё время"

    await state.set_state(ArchiveSearchStates.waiting_for_keywords)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад", callback_data="sniper_archive_search")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="sniper_menu")]
    ])

    await callback.message.edit_text(
        f"📦 <b>Поиск в архиве</b> 🧪 БЕТА\n\n"
        f"<b>Шаг 2/4:</b> Введите ключевые слова\n\n"
        f"📅 Период: <b>{period_text}</b>\n\n"
        f"Введите слова для поиска через запятую:\n"
        f"<i>Например: компьютер, ноутбук, моноблок</i>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("arch_region_"), ArchiveSearchStates.waiting_for_region)
async def archive_select_region(callback: CallbackQuery, state: FSMContext):
    """Шаг 3: Выбор региона."""
    await callback.answer()

    region_code = callback.data.replace("arch_region_", "")

    if region_code == "custom":
        # Запрашиваем ввод кода региона
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="arch_back_to_region_select")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="sniper_menu")]
        ])

        await callback.message.edit_text(
            "📦 <b>Поиск в архиве</b> 🧪 БЕТА\n\n"
            "<b>Введите код региона</b>\n\n"
            "Например: <code>77</code> - Москва, <code>78</code> - СПб\n\n"
            "Можно ввести несколько через запятую:\n"
            "<code>77, 50, 78</code>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await state.set_state(ArchiveSearchStates.confirm_search)
        await state.update_data(waiting_for_region_input=True)
        return

    # Сохраняем регион
    regions = [] if region_code == "all" else [region_code]
    await state.update_data(archive_regions=regions)

    # Запускаем поиск
    await run_archive_search(callback.message, state, callback.from_user.id)


@router.callback_query(F.data == "arch_back_to_region_select")
async def archive_back_to_region_select(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору региона."""
    await callback.answer()
    await state.update_data(waiting_for_region_input=False)
    await state.set_state(ArchiveSearchStates.waiting_for_region)

    data = await state.get_data()
    period_days = data.get('archive_period_days', 30)
    keywords = data.get('archive_keywords', [])
    period_text = f"за {period_days} дней" if period_days > 0 else "за всё время"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌍 Вся Россия", callback_data="arch_region_all")],
        [InlineKeyboardButton(text="🏛️ Москва", callback_data="arch_region_77")],
        [InlineKeyboardButton(text="🏛️ Санкт-Петербург", callback_data="arch_region_78")],
        [InlineKeyboardButton(text="🏛️ Московская область", callback_data="arch_region_50")],
        [InlineKeyboardButton(text="📝 Ввести код региона", callback_data="arch_region_custom")],
        [InlineKeyboardButton(text="« Назад", callback_data="arch_back_to_keywords")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="sniper_menu")]
    ])

    await callback.message.edit_text(
        f"📦 <b>Поиск в архиве</b> 🧪 БЕТА\n\n"
        f"<b>Шаг 3/4:</b> Выберите регион\n\n"
        f"📅 Период: <b>{period_text}</b>\n"
        f"🔑 Слова: <b>{', '.join(keywords[:3])}</b>"
        f"{' (+' + str(len(keywords)-3) + ')' if len(keywords) > 3 else ''}\n\n"
        f"Выберите регион или оставьте «Вся Россия»:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.message(ArchiveSearchStates.confirm_search)
async def archive_process_custom_region(message: Message, state: FSMContext):
    """Обработка ввода кода региона."""
    data = await state.get_data()

    if not data.get('waiting_for_region_input'):
        return

    # Проверяем системные кнопки
    if message.text in ["🏠 Главное меню", "🎯 Tender Sniper", "📊 Мои фильтры"]:
        await state.clear()
        return

    # Парсим коды регионов
    region_codes = [r.strip() for r in message.text.split(',') if r.strip().isdigit()]
    if not region_codes:
        await message.answer("⚠️ Введите числовой код региона (например: 77):")
        return

    await state.update_data(archive_regions=region_codes, waiting_for_region_input=False)

    # Запускаем поиск
    await run_archive_search(message, state, message.from_user.id)


async def run_archive_search(message_or_callback, state: FSMContext, user_id: int):
    """Выполнение архивного поиска с генерацией HTML отчёта."""
    import json
    from aiogram.types import FSInputFile

    data = await state.get_data()
    period_days = data.get('archive_period_days', 30)
    keywords = data.get('archive_keywords', [])
    regions = data.get('archive_regions', [])

    period_text = f"за {period_days} дней" if period_days > 0 else "за всё время"
    region_text = ', '.join(regions) if regions else "Вся Россия"

    # Показываем статус поиска
    if hasattr(message_or_callback, 'edit_text'):
        status_msg = await message_or_callback.edit_text(
            f"📦 <b>Поиск в архиве</b> 🧪 БЕТА\n\n"
            f"🔄 <b>Шаг 1/3:</b> Поиск тендеров...\n\n"
            f"📅 Период: <b>{period_text}</b>\n"
            f"🔑 Слова: <b>{', '.join(keywords[:3])}</b>\n"
            f"🌍 Регион: <b>{region_text}</b>",
            parse_mode="HTML"
        )
    else:
        status_msg = await message_or_callback.answer(
            f"📦 <b>Поиск в архиве</b> 🧪 БЕТА\n\n"
            f"🔄 <b>Шаг 1/3:</b> Поиск тендеров...\n\n"
            f"📅 Период: <b>{period_text}</b>\n"
            f"🔑 Слова: <b>{', '.join(keywords[:3])}</b>\n"
            f"🌍 Регион: <b>{region_text}</b>",
            parse_mode="HTML"
        )

    try:
        db = await get_sniper_db()

        # Генерируем название
        filter_name = f"Архив: {' '.join(keywords[:2])}"

        user = await db.get_user_by_telegram_id(user_id)
        if not user:
            await status_msg.edit_text("❌ Пользователь не найден")
            await state.clear()
            return

        # Создаём временный фильтр
        filter_id = await db.create_filter(
            user_id=user['id'],
            name=filter_name,
            keywords=keywords,
            regions=regions if regions else None,
            is_active=False
        )

        # Формируем filter_data для поиска
        filter_data = {
            'id': filter_id,
            'name': filter_name,
            'keywords': json.dumps(keywords, ensure_ascii=False),
            'exclude_keywords': json.dumps([], ensure_ascii=False),
            'price_min': None,
            'price_max': None,
            'regions': json.dumps(regions, ensure_ascii=False) if regions else json.dumps([], ensure_ascii=False),
            'tender_types': json.dumps([], ensure_ascii=False),
            'law_type': None,
            'purchase_stage': 'archive',
            'purchase_method': None,
            'okpd2_codes': json.dumps([], ensure_ascii=False),
            'min_deadline_days': None,
            'customer_keywords': json.dumps([], ensure_ascii=False),
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

        if not matches:
            # Удаляем временный фильтр
            await db.delete_filter(filter_id)

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📦 Новый поиск в архиве", callback_data="sniper_archive_search")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])

            await status_msg.edit_text(
                f"📦 <b>Поиск в архиве</b> 🧪 БЕТА\n\n"
                f"😔 По вашему запросу ничего не найдено.\n\n"
                f"📅 Период: <b>{period_text}</b>\n"
                f"🔑 Слова: <b>{', '.join(keywords)}</b>\n\n"
                f"Попробуйте изменить ключевые слова или период поиска.",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await state.clear()
            return

        # Шаг 2: Сохраняем тендеры в БД
        await status_msg.edit_text(
            f"📦 <b>Поиск в архиве</b> 🧪 БЕТА\n\n"
            f"✅ <b>Шаг 1/3:</b> Найдено {len(matches)} тендеров\n"
            f"🔄 <b>Шаг 2/3:</b> Сохранение в базу...",
            parse_mode="HTML"
        )

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
                    source='archive_search'
                )
                saved_count += 1
            except Exception as e:
                logger.warning(f"Не удалось сохранить тендер: {e}")

        # Шаг 3: Генерируем HTML отчёт
        await status_msg.edit_text(
            f"📦 <b>Поиск в архиве</b> 🧪 БЕТА\n\n"
            f"✅ <b>Шаг 1/3:</b> Найдено {len(matches)} тендеров\n"
            f"✅ <b>Шаг 2/3:</b> Сохранено {saved_count} в базу\n"
            f"🔄 <b>Шаг 3/3:</b> Генерация HTML отчёта...",
            parse_mode="HTML"
        )

        report_path = await searcher.generate_html_report(
            search_results=search_results,
            filter_data=filter_data
        )

        # Удаляем временный фильтр
        await db.delete_filter(filter_id)
        logger.info(f"🗑️ Временный архивный фильтр {filter_id} удален")

        # Отправляем HTML отчёт
        await status_msg.edit_text(
            f"📦 <b>Поиск в архиве завершён!</b> 🧪 БЕТА\n\n"
            f"📊 Найдено: {len(matches)} тендеров\n"
            f"💾 Сохранено: {saved_count}\n\n"
            f"📄 Отправляю HTML отчёт...",
            parse_mode="HTML"
        )

        # Получаем message объект для отправки файла
        if hasattr(message_or_callback, 'answer_document'):
            message = message_or_callback
        else:
            message = message_or_callback

        await message.answer_document(
            document=FSInputFile(report_path),
            caption=(
                f"📦 <b>Результаты поиска в архиве</b> 🧪 БЕТА\n\n"
                f"📅 Период: <b>{period_text}</b>\n"
                f"🔑 Слова: <b>{', '.join(keywords[:3])}</b>\n"
                f"📊 Найдено: {len(matches)} архивных тендеров\n"
                f"💾 Сохранено в базу: {saved_count}\n\n"
                f"💡 Это завершённые тендеры. Используйте для анализа цен и конкурентов."
            ),
            parse_mode="HTML"
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Все мои тендеры", callback_data="sniper_all_tenders")],
            [InlineKeyboardButton(text="📦 Новый поиск в архиве", callback_data="sniper_archive_search")],
            [InlineKeyboardButton(text="🔍 Поиск актуальных", callback_data="sniper_new_search")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])

        await message.answer(
            "✅ <b>Поиск в архиве завершён!</b>\n\n"
            "Тендеры сохранены в базу данных.\n"
            "Откройте HTML отчёт для подробной информации.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

        await state.clear()

    except Exception as e:
        logger.error(f"Error in archive search: {e}", exc_info=True)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="sniper_archive_search")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])
        await status_msg.edit_text(
            f"❌ Произошла ошибка при поиске.\n\n{str(e)[:200]}\n\nПопробуйте позже.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await state.clear()


# ============================================
# 🧪 БЕТА: Обработчики черновиков
# ============================================

@router.callback_query(F.data.startswith("draft_resume_"))
async def resume_draft(callback: CallbackQuery, state: FSMContext):
    """Восстановить черновик и продолжить wizard."""
    await callback.answer("✅ Восстанавливаем...")

    try:
        with_instant_search = callback.data.endswith("_1")

        db = await get_sniper_db()
        draft = await db.get_filter_draft(callback.from_user.id)

        if not draft or not draft.get('draft_data'):
            await callback.message.edit_text(
                "❌ Черновик не найден. Начните создание фильтра заново.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🎯 Новый поиск", callback_data="sniper_new_search")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
                ])
            )
            return

        # Восстанавливаем данные FSM
        draft_data = draft['draft_data']
        draft_data['with_instant_search'] = with_instant_search
        await state.set_data(draft_data)

        # Определяем текущий шаг и продолжаем с него
        current_step = draft.get('current_step', '')

        # Маппинг шагов на состояния и сообщения
        step_mapping = {
            'Название фильтра': (FilterSearchStates.waiting_for_filter_name, "Введите название фильтра:"),
            'Ключевые слова': (FilterSearchStates.waiting_for_keywords, "Введите ключевые слова:"),
            'Слова-исключения': (FilterSearchStates.waiting_for_exclude_keywords, "Введите слова-исключения:"),
            'Цена': (FilterSearchStates.waiting_for_price_range, "Выберите ценовой диапазон:"),
            'Регионы': (FilterSearchStates.waiting_for_regions, "Выберите регионы:"),
            'Закон': (FilterSearchStates.waiting_for_law_type, "Выберите тип закона:"),
            'Этап закупки': (FilterSearchStates.waiting_for_purchase_stage, "Выберите этап закупки:"),
            'Способ закупки': (FilterSearchStates.waiting_for_purchase_method, "Выберите способ закупки:"),
            'Тип тендера': (FilterSearchStates.waiting_for_tender_type, "Выберите тип тендера:"),
        }

        filter_name = draft_data.get('filter_name', 'Ваш фильтр')

        # Восстанавливаем состояние
        if current_step in step_mapping:
            fsm_state, hint = step_mapping[current_step]
            await state.set_state(fsm_state)

            await callback.message.edit_text(
                f"✅ <b>Черновик восстановлен</b>\n\n"
                f"Фильтр: <b>{filter_name}</b>\n"
                f"Шаг: <i>{current_step}</i>\n\n"
                f"{hint}",
                parse_mode="HTML"
            )
        else:
            # По умолчанию - начинаем с ключевых слов (шаг 2)
            await state.set_state(FilterSearchStates.waiting_for_keywords)

            await callback.message.edit_text(
                f"✅ <b>Черновик восстановлен</b>\n\n"
                f"Фильтр: <b>{filter_name}</b>\n\n"
                f"Продолжите ввод ключевых слов:",
                parse_mode="HTML"
            )

        logger.info(f"📝 Черновик восстановлен для пользователя {callback.from_user.id}")

    except Exception as e:
        logger.error(f"Ошибка восстановления черновика: {e}", exc_info=True)
        await callback.message.edit_text(
            "❌ Ошибка при восстановлении черновика. Начните заново.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎯 Новый поиск", callback_data="sniper_new_search")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])
        )


@router.callback_query(F.data.startswith("draft_discard_"))
async def discard_draft(callback: CallbackQuery, state: FSMContext):
    """Отклонить черновик и начать заново."""
    await callback.answer("🗑️ Черновик удалён")

    try:
        with_instant_search = callback.data.endswith("_1")

        # Удаляем черновик
        db = await get_sniper_db()
        await db.delete_filter_draft(callback.from_user.id)

        # Очищаем FSM
        await state.clear()

        # Начинаем заново
        await state.update_data(with_instant_search=with_instant_search)
        await state.set_state(FilterSearchStates.waiting_for_filter_name)

        title = "🎯 <b>Создание фильтра с мгновенным поиском</b>" if with_instant_search else "➕ <b>Создание фильтра для автомониторинга</b>"

        await callback.message.edit_text(
            f"{title}\n\n"
            f"<b>Шаг 1/14:</b> Название фильтра\n\n"
            f"Придумайте короткое название для вашего фильтра.\n"
            f"Например: <i>IT оборудование</i>, <i>Медицинские товары</i>",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка удаления черновика: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка. Попробуйте ещё раз.")


# ============================================
# WIZARD: Обработчики шагов
# ============================================

@router.message(FilterSearchStates.waiting_for_filter_name)
async def process_filter_name_new(message: Message, state: FSMContext):
    """Обработка названия фильтра."""
    # Проверяем, не нажал ли пользователь системную кнопку
    if message.text in ["🏠 Главное меню", "🎯 Tender Sniper", "📊 Мои фильтры", "📊 Все мои тендеры", "⭐ Избранное", "📈 Статистика"]:
        # Очищаем FSM и даем обработаться основному handler
        await state.clear()
        # Не обрабатываем здесь - позволяем другому handler перехватить
        return

    filter_name = message.text.strip()

    if not filter_name or len(filter_name) > 100:
        await message.answer(
            "⚠️ Название должно быть от 1 до 100 символов. Попробуйте еще раз:"
        )
        return

    await state.update_data(filter_name=filter_name)

    # 🧪 БЕТА: Сохраняем черновик
    await save_wizard_draft(message.from_user.id, state, "Ключевые слова")

    await ask_for_keywords(message, state)


async def ask_for_keywords(message: Message, state: FSMContext):
    """Запрос ключевых слов."""
    await state.set_state(FilterSearchStates.waiting_for_keywords)

    data = await state.get_data()
    filter_name = data.get('filter_name', 'Новый фильтр')

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад к названию", callback_data="back_to_filter_name")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await message.answer(
        f"✅ Название: <b>{filter_name}</b>\n\n"
        f"<b>Шаг 2/14:</b> Ключевые слова\n\n"
        f"Введите ключевые слова через запятую.\n"
        f"Например: <i>компьютеры, ноутбуки, серверы</i>\n\n"
        f"🤖 <b>AI автоматически расширит ваш запрос</b>\n"
        f"Система добавит синонимы и связанные термины для более точного поиска.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.message(FilterSearchStates.waiting_for_keywords)
async def process_keywords_new(message: Message, state: FSMContext):
    """Обработка ключевых слов."""
    # Проверяем, не нажал ли пользователь системную кнопку
    if message.text in ["🏠 Главное меню", "🎯 Tender Sniper", "📊 Мои фильтры", "📊 Все мои тендеры", "⭐ Избранное", "📈 Статистика"]:
        await state.clear()
        return

    keywords_input = message.text.strip()

    if not keywords_input:
        await message.answer("⚠️ Введите хотя бы одно ключевое слово:")
        return

    # Парсим ключевые слова
    keywords = [kw.strip() for kw in keywords_input.split(',') if kw.strip()]

    # Валидация ключевых слов с Pydantic
    try:
        # Используем временное имя для валидации только ключевых слов
        validated = FilterCreate(
            name="temp",
            keywords=keywords,
        )
        keywords = validated.keywords  # Используем валидированные ключевые слова
        logger.info(f"✅ Валидация ключевых слов прошла успешно: {len(keywords)} слов")
    except ValidationError as e:
        error_messages = []
        for error in e.errors():
            # Фильтруем только ошибки связанные с keywords
            if 'keywords' in str(error.get('loc', [])):
                msg = error['msg']
                error_messages.append(f"• {msg}")

        if error_messages:
            await message.answer(
                f"❌ <b>Ошибка валидации ключевых слов:</b>\n\n" + "\n".join(error_messages) +
                "\n\nПопробуйте еще раз:",
                parse_mode="HTML"
            )
            return

    await state.update_data(keywords=keywords)

    # 🧪 БЕТА: Сохраняем черновик
    await save_wizard_draft(message.from_user.id, state, "Слова-исключения")

    await ask_for_exclude_keywords(message, state)


async def ask_for_exclude_keywords(message: Message, state: FSMContext):
    """Запрос исключающих слов."""
    await state.set_state(FilterSearchStates.waiting_for_exclude_keywords)

    data = await state.get_data()
    keywords = data.get('keywords', [])

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭️ Пропустить", callback_data="skip_exclude_keywords")],
        [InlineKeyboardButton(text="« Назад к ключевым словам", callback_data="back_to_keywords")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await message.answer(
        f"✅ Ключевые слова: <b>{', '.join(keywords)}</b>\n\n"
        f"<b>Шаг 3/14:</b> Исключающие слова\n\n"
        f"Введите слова, которые НЕ должны быть в тендере:\n"
        f"Например: <i>ремонт, б/у, аренда, лизинг</i>\n\n"
        f"Или нажмите «Пропустить»",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "skip_exclude_keywords", FilterSearchStates.waiting_for_exclude_keywords)
async def skip_exclude_keywords(callback: CallbackQuery, state: FSMContext):
    """Пропуск исключающих слов."""
    await callback.answer()
    await state.update_data(exclude_keywords=[])
    await ask_for_price_range(callback.message, state)


@router.message(FilterSearchStates.waiting_for_exclude_keywords)
async def process_exclude_keywords(message: Message, state: FSMContext):
    """Обработка исключающих слов."""
    # Проверяем, не нажал ли пользователь системную кнопку
    if message.text in ["🏠 Главное меню", "🎯 Tender Sniper", "📊 Мои фильтры", "📊 Все мои тендеры", "⭐ Избранное", "📈 Статистика"]:
        await state.clear()
        return

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
        [InlineKeyboardButton(text="⏭️ Любая цена", callback_data="skip_price_range")],
        [InlineKeyboardButton(text="« Назад", callback_data="back_to_exclude_keywords")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await message.answer(
        f"{exclude_text}"
        f"<b>Шаг 4/14:</b> Ценовой диапазон\n\n"
        f"Введите диапазон цен в формате: <code>мин макс</code>\n"
        f"Например: <code>100000 5000000</code> (от 100 тыс до 5 млн)\n\n"
        f"Или нажмите «Любая цена»",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "skip_price_range", FilterSearchStates.waiting_for_price_range)
async def skip_price_range(callback: CallbackQuery, state: FSMContext):
    """Пропуск ценового диапазона."""
    await callback.answer("🌍 Выбрана любая цена")
    await state.update_data(price_min=None, price_max=None)
    # Сразу переходим к регионам
    await ask_for_regions(callback.message, state)


@router.message(FilterSearchStates.waiting_for_price_range)
async def process_price_range_new(message: Message, state: FSMContext):
    """Обработка ценового диапазона."""
    # Проверяем, не нажал ли пользователь системную кнопку
    if message.text in ["🏠 Главное меню", "🎯 Tender Sniper", "📊 Мои фильтры", "📊 Все мои тендеры", "⭐ Избранное", "📈 Статистика"]:
        await state.clear()
        return

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

    # Показываем подтверждение цены
    await show_price_confirmation(message, state)


async def show_price_confirmation(message: Message, state: FSMContext):
    """Показать подтверждение ценового диапазона."""
    await state.set_state(FilterSearchStates.confirm_price_range)

    data = await state.get_data()
    price_min = data.get('price_min')
    price_max = data.get('price_max')

    if price_min is not None and price_max is not None:
        price_text = f"💰 {price_min:,} ₽ — {price_max:,} ₽"
    else:
        price_text = "💰 Любая цена"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Продолжить", callback_data="confirm_price_continue")],
        [InlineKeyboardButton(text="✏️ Изменить цену", callback_data="confirm_price_edit")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await message.answer(
        f"<b>Подтверждение ценового диапазона</b>\n\n"
        f"{price_text}\n\n"
        f"Продолжить с этими параметрами?",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "confirm_price_continue", FilterSearchStates.confirm_price_range)
async def confirm_price_continue(callback: CallbackQuery, state: FSMContext):
    """Подтверждение цены - продолжаем к регионам."""
    await callback.answer("✅ Цена подтверждена")
    await ask_for_regions(callback.message, state)


@router.callback_query(F.data == "confirm_price_edit", FilterSearchStates.confirm_price_range)
async def confirm_price_edit(callback: CallbackQuery, state: FSMContext):
    """Вернуться к редактированию цены."""
    await callback.answer("✏️ Возвращаемся к выбору цены")
    await ask_for_price_range(callback.message, state)


@router.callback_query(F.data == "back_to_exclude_keywords")
async def back_to_exclude_keywords(callback: CallbackQuery, state: FSMContext):
    """Вернуться к предыдущему шагу (исключаемые слова)."""
    await callback.answer("« Возвращаемся к исключаемым словам")
    await ask_for_exclude_keywords(callback.message, state)


@router.callback_query(F.data == "back_to_keywords")
async def back_to_keywords(callback: CallbackQuery, state: FSMContext):
    """Вернуться к шагу ключевых слов."""
    await callback.answer("« Возвращаемся к ключевым словам")
    await ask_for_keywords(callback.message, state)


@router.callback_query(F.data == "back_to_filter_name")
async def back_to_filter_name(callback: CallbackQuery, state: FSMContext):
    """Вернуться к вводу названия фильтра."""
    await callback.answer("« Возвращаемся к названию фильтра")
    await state.set_state(FilterSearchStates.waiting_for_filter_name)

    data = await state.get_data()
    with_instant_search = data.get('with_instant_search', True)

    if with_instant_search:
        text = (
            "🎯 <b>Создание фильтра с мгновенным поиском</b>\n\n"
            "<b>Шаг 1/14:</b> Название фильтра\n\n"
            "Придумайте короткое название для вашего фильтра.\n"
            "Например: <i>IT оборудование</i>, <i>Медицинские товары</i>\n\n"
            "💡 Это название поможет вам управлять фильтрами в будущем."
        )
    else:
        text = (
            "➕ <b>Создание фильтра для автомониторинга</b>\n\n"
            "<b>Шаг 1/14:</b> Название фильтра\n\n"
            "Придумайте короткое название для вашего фильтра.\n"
            "Например: <i>IT оборудование</i>, <i>Медицинские товары</i>\n\n"
            "💡 Это название поможет вам управлять фильтрами в будущем.\n\n"
            "🔔 Фильтр будет сразу активен для мониторинга."
        )

    await callback.message.edit_text(text, parse_mode="HTML")


@router.callback_query(F.data == "back_to_price")
async def back_to_price(callback: CallbackQuery, state: FSMContext):
    """Вернуться к шагу выбора цены."""
    await callback.answer("« Возвращаемся к выбору цены")
    await ask_for_price_range(callback.message, state)


@router.callback_query(F.data == "back_to_regions")
async def back_to_regions(callback: CallbackQuery, state: FSMContext):
    """Вернуться к выбору регионов."""
    await callback.answer("« Возвращаемся к выбору регионов")
    await ask_for_regions(callback.message, state)


@router.callback_query(F.data == "back_to_law_type")
async def back_to_law_type(callback: CallbackQuery, state: FSMContext):
    """Вернуться к выбору типа закона."""
    await callback.answer("« Возвращаемся к типу закона")
    await ask_for_law_type(callback.message, state)


@router.callback_query(F.data == "back_to_purchase_stage")
async def back_to_purchase_stage(callback: CallbackQuery, state: FSMContext):
    """Вернуться к выбору этапа закупки."""
    await callback.answer("« Возвращаемся к этапу закупки")
    await ask_for_purchase_stage(callback.message, state)


@router.callback_query(F.data == "back_to_purchase_method")
async def back_to_purchase_method(callback: CallbackQuery, state: FSMContext):
    """Вернуться к выбору способа закупки."""
    await callback.answer("« Возвращаемся к способу закупки")
    await ask_for_purchase_method(callback.message, state)


@router.callback_query(F.data == "back_to_tender_type")
async def back_to_tender_type(callback: CallbackQuery, state: FSMContext):
    """Вернуться к выбору типа закупки."""
    await callback.answer("« Возвращаемся к типу закупки")
    await ask_for_tender_type(callback.message, state)


@router.callback_query(F.data == "back_to_min_deadline")
async def back_to_min_deadline(callback: CallbackQuery, state: FSMContext):
    """Вернуться к выбору минимального дедлайна."""
    await callback.answer("« Возвращаемся к дедлайну")
    await ask_for_min_deadline(callback.message, state)


@router.callback_query(F.data == "back_to_customer_keywords")
async def back_to_customer_keywords(callback: CallbackQuery, state: FSMContext):
    """Вернуться к вводу ключевых слов заказчика."""
    await callback.answer("« Возвращаемся к фильтру по заказчику")
    await ask_for_customer_keywords(callback.message, state)


@router.callback_query(F.data == "back_to_okpd2")
async def back_to_okpd2(callback: CallbackQuery, state: FSMContext):
    """Вернуться к выбору ОКПД2."""
    await callback.answer("« Возвращаемся к ОКПД2")
    await ask_for_okpd2(callback.message, state)


@router.callback_query(F.data == "back_to_search_mode")
async def back_to_search_mode(callback: CallbackQuery, state: FSMContext):
    """Вернуться к выбору режима поиска."""
    await callback.answer("« Возвращаемся к режиму поиска")
    await ask_for_search_mode(callback.message, state)


async def ask_for_regions(message: Message, state: FSMContext):
    """Запрос региона."""
    await state.set_state(FilterSearchStates.waiting_for_regions)

    # Инициализируем выбранные ФО, если еще не было
    data = await state.get_data()
    if 'selected_federal_districts' not in data:
        await state.update_data(selected_federal_districts=[], region_selection_mode='initial')

    # Кнопки с переключением режима
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        # Быстрые опции (ВВЕРХУ)
        [InlineKeyboardButton(text="🌍 Все регионы России", callback_data="region_all")],
        [InlineKeyboardButton(text="✍️ Ввести вручную", callback_data="region_custom")],
        # Режимы выбора
        [InlineKeyboardButton(text="📍 Выбрать федеральные округа", callback_data="region_mode_federal")],
        [InlineKeyboardButton(text="🏙️ Выбрать отдельные регионы", callback_data="region_mode_single")],
        # Навигация
        [InlineKeyboardButton(text="« Назад к цене", callback_data="back_to_price")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await message.answer(
        f"<b>Шаг 5/14:</b> Регион заказчика\n\n"
        f"Выберите способ указания регионов:\n\n"
        f"📍 <b>Федеральные округа</b> — выбрать один или несколько ФО\n"
        f"🏙️ <b>Отдельные регионы</b> — Москва, СПб и др.\n"
        f"🌍 <b>Все регионы</b> — поиск по всей России\n"
        f"✍️ <b>Ручной ввод</b> — например: москва, спб, краснодар",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "region_mode_federal")
async def show_federal_districts_selection(callback: CallbackQuery, state: FSMContext):
    """Показать меню выбора федеральных округов."""
    await callback.answer()

    data = await state.get_data()
    selected_fos = data.get('selected_federal_districts', [])

    # Создаем клавиатуру с чекбоксами для каждого ФО
    keyboard_rows = []

    federal_districts = [
        ("Центральный", "Центральный"),
        ("Северо-Западный", "Северо-Западный"),
        ("Южный", "Южный"),
        ("Северо-Кавказский", "Северо-Кавказский"),
        ("Приволжский", "Приволжский"),
        ("Уральский", "Уральский"),
        ("Сибирский", "Сибирский"),
        ("Дальневосточный", "Дальневосточный"),
    ]

    for name, code in federal_districts:
        is_selected = code in selected_fos
        prefix = "✅" if is_selected else "⬜"
        keyboard_rows.append([
            InlineKeyboardButton(
                text=f"{prefix} {name} ФО",
                callback_data=f"region_toggle_fo_{code}"
            )
        ])

    # Кнопки подтверждения
    if selected_fos:
        keyboard_rows.append([
            InlineKeyboardButton(
                text=f"➡️ Продолжить ({len(selected_fos)} ФО)",
                callback_data="region_confirm_federal"
            )
        ])

    keyboard_rows.append([
        InlineKeyboardButton(text="« Назад", callback_data="region_back_to_modes")
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    selected_text = f"\n\n<b>Выбрано:</b> {', '.join(selected_fos)}" if selected_fos else "\n\n<i>Выберите один или несколько федеральных округов</i>"

    await callback.message.edit_text(
        f"📍 <b>Выбор федеральных округов</b>\n\n"
        f"Нажмите на округ, чтобы добавить/убрать его из выбора.{selected_text}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("region_toggle_fo_"), FilterSearchStates.waiting_for_regions)
async def toggle_federal_district(callback: CallbackQuery, state: FSMContext):
    """Переключение выбора федерального округа."""
    fo_code = callback.data.replace("region_toggle_fo_", "")

    data = await state.get_data()
    selected_fos = data.get('selected_federal_districts', [])

    if fo_code in selected_fos:
        selected_fos.remove(fo_code)
    else:
        selected_fos.append(fo_code)

    await state.update_data(selected_federal_districts=selected_fos)

    # Обновляем меню
    await show_federal_districts_selection(callback, state)


@router.callback_query(F.data == "region_confirm_federal", FilterSearchStates.waiting_for_regions)
async def confirm_federal_districts(callback: CallbackQuery, state: FSMContext):
    """Подтверждение выбора федеральных округов."""
    await callback.answer()

    data = await state.get_data()
    selected_fos = data.get('selected_federal_districts', [])

    if not selected_fos:
        await callback.answer("⚠️ Выберите хотя бы один федеральный округ", show_alert=True)
        return

    # Собираем все регионы из выбранных ФО
    all_regions = []
    for fo in selected_fos:
        regions = get_regions_by_district(fo)
        all_regions.extend(regions)

    await state.update_data(regions=all_regions)

    await callback.message.answer(
        f"✅ <b>Выбрано федеральных округов: {len(selected_fos)}</b>\n\n"
        f"📍 {', '.join(selected_fos)}\n\n"
        f"Включено регионов: {len(all_regions)}",
        parse_mode="HTML"
    )
    await ask_for_law_type(callback.message, state)


@router.callback_query(F.data == "region_mode_single")
async def show_single_regions_selection(callback: CallbackQuery, state: FSMContext):
    """Показать меню выбора отдельных регионов."""
    await callback.answer()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏙️ Москва", callback_data="region_single_Москва")],
        [InlineKeyboardButton(text="🏛️ Санкт-Петербург", callback_data="region_single_Санкт-Петербург")],
        [InlineKeyboardButton(text="🏘️ Московская область", callback_data="region_single_Московская область")],
        [InlineKeyboardButton(text="🏭 Свердловская область", callback_data="region_single_Свердловская область")],
        [InlineKeyboardButton(text="🌆 Краснодарский край", callback_data="region_single_Краснодарский край")],
        [InlineKeyboardButton(text="🏙️ Новосибирская область", callback_data="region_single_Новосибирская область")],
        [InlineKeyboardButton(text="✍️ Ввести другой регион", callback_data="region_custom")],
        [InlineKeyboardButton(text="« Назад", callback_data="region_back_to_modes")]
    ])

    await callback.message.edit_text(
        f"🏙️ <b>Выбор отдельного региона</b>\n\n"
        f"Выберите популярный регион или введите название вручную:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "region_back_to_modes")
async def back_to_region_modes(callback: CallbackQuery, state: FSMContext):
    """Возврат к выбору режима."""
    await callback.answer()

    # Сбрасываем выбранные ФО
    await state.update_data(selected_federal_districts=[])

    # Перезапускаем выбор регионов
    await ask_for_regions(callback.message, state)


@router.callback_query(F.data.startswith("region_"), FilterSearchStates.waiting_for_regions)
async def process_region_callback(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора региона."""
    await callback.answer()

    region_data = callback.data.replace("region_", "")

    if region_data == "all":
        # Все регионы
        await state.update_data(regions=[])
        await callback.message.answer("✅ <b>Все регионы России</b>", parse_mode="HTML")
        await ask_for_law_type(callback.message, state)

    elif region_data == "custom":
        # Ручной ввод
        await callback.message.answer(
            "✍️ <b>Ручной ввод регионов</b>\n\n"
            "Введите один или несколько регионов через запятую.\n\n"
            "<b>Примеры:</b>\n"
            "• <code>москва</code>\n"
            "• <code>спб, москва</code>\n"
            "• <code>краснодар, ростов, волгоград</code>\n"
            "• <code>екатеринбург, новосибирск, красноярск</code>\n\n"
            "💡 Система автоматически распознает сокращения и альтернативные названия!",
            parse_mode="HTML"
        )

    elif region_data.startswith("fo_"):
        # Федеральный округ
        district_name = region_data.replace("fo_", "")
        district_regions = get_regions_by_district(district_name)

        await state.update_data(regions=district_regions)

        await callback.message.answer(
            f"✅ <b>{district_name} федеральный округ</b>\n\n"
            f"Включено регионов: {len(district_regions)}\n"
            f"📍 {format_regions_list(district_regions, max_display=5)}",
            parse_mode="HTML"
        )
        await ask_for_law_type(callback.message, state)

    elif region_data.startswith("single_"):
        # Одиночный регион
        region_name = region_data.replace("single_", "")
        await state.update_data(regions=[region_name])
        await callback.message.answer(f"✅ <b>Регион:</b> {region_name}", parse_mode="HTML")
        await ask_for_law_type(callback.message, state)


@router.message(FilterSearchStates.waiting_for_regions)
async def process_region_text(message: Message, state: FSMContext):
    """Обработка текстового ввода региона с распознаванием."""
    # Проверяем, не нажал ли пользователь системную кнопку
    if message.text in ["🏠 Главное меню", "🎯 Tender Sniper", "📊 Мои фильтры", "📊 Все мои тендеры", "⭐ Избранное", "📈 Статистика"]:
        await state.clear()
        return

    regions_text = message.text.strip()

    if not regions_text:
        await state.update_data(regions=[])
        await message.answer("⚠️ <b>Регионы не указаны</b>\nБудем искать по всей России.", parse_mode="HTML")
        await ask_for_law_type(message, state)
        return

    # Парсим и распознаем регионы
    recognized, unrecognized = parse_regions_input(regions_text)

    if not recognized and not unrecognized:
        await state.update_data(regions=[])
        await message.answer("⚠️ <b>Регионы не распознаны</b>\nБудем искать по всей России.", parse_mode="HTML")
        await ask_for_law_type(message, state)
        return

    # Сохраняем распознанные регионы
    await state.update_data(regions=recognized if recognized else [])

    # Формируем ответ
    response = ""

    if recognized:
        response += f"✅ <b>Распознано регионов: {len(recognized)}</b>\n"
        response += f"📍 {format_regions_list(recognized, max_display=8)}\n"

    if unrecognized:
        response += f"\n⚠️ <b>Не распознано: {len(unrecognized)}</b>\n"
        response += f"❌ {', '.join(unrecognized)}\n"
        response += f"\n<i>Эти регионы будут пропущены при поиске.</i>"

    await message.answer(response, parse_mode="HTML")
    await ask_for_law_type(message, state)


async def ask_for_law_type(message: Message, state: FSMContext):
    """Запрос типа закона (множественный выбор)."""
    await state.set_state(FilterSearchStates.waiting_for_law_type)

    # Получаем текущий выбор
    data = await state.get_data()
    selected_laws = data.get('selected_laws', [])

    # Формируем кнопки с галочками
    law_44_text = "✅ 44-ФЗ (госзакупки)" if "44-ФЗ" in selected_laws else "☐ 44-ФЗ (госзакупки)"
    law_223_text = "✅ 223-ФЗ (корпоративные)" if "223-ФЗ" in selected_laws else "☐ 223-ФЗ (корпоративные)"

    # Кнопка "Выбрать все" / "Снять все"
    all_selected = len(selected_laws) == 2
    select_all_text = "❌ Снять все" if all_selected else "☑️ Выбрать все"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=law_44_text, callback_data="law_toggle_44")],
        [InlineKeyboardButton(text=law_223_text, callback_data="law_toggle_223")],
        [InlineKeyboardButton(text=select_all_text, callback_data="law_select_all")],
        [InlineKeyboardButton(text="✅ Продолжить", callback_data="law_confirm")],
        [InlineKeyboardButton(text="« Назад к регионам", callback_data="back_to_regions")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    status_text = ""
    if selected_laws:
        status_text = f"\n\n<b>Выбрано:</b> {', '.join(selected_laws)}"
    else:
        status_text = "\n\n<i>Не выбрано (будут показаны оба закона)</i>"

    await message.answer(
        f"<b>Шаг 6/14:</b> Тип закона\n\n"
        f"<b>44-ФЗ</b> — государственные закупки (бюджетные организации)\n"
        f"<b>223-ФЗ</b> — закупки госкомпаний (Газпром, РЖД и др.)\n\n"
        f"💡 Нажмите на закон для выбора. Можно выбрать оба.{status_text}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("law_toggle_"), FilterSearchStates.waiting_for_law_type)
async def process_law_toggle(callback: CallbackQuery, state: FSMContext):
    """Переключение выбора типа закона."""
    await callback.answer()

    law_value = callback.data.replace("law_toggle_", "")
    law_name = "44-ФЗ" if law_value == "44" else "223-ФЗ"

    data = await state.get_data()
    selected_laws = data.get('selected_laws', [])

    if law_name in selected_laws:
        selected_laws.remove(law_name)
    else:
        selected_laws.append(law_name)

    await state.update_data(selected_laws=selected_laws)

    # Обновляем клавиатуру
    law_44_text = "✅ 44-ФЗ (госзакупки)" if "44-ФЗ" in selected_laws else "☐ 44-ФЗ (госзакупки)"
    law_223_text = "✅ 223-ФЗ (корпоративные)" if "223-ФЗ" in selected_laws else "☐ 223-ФЗ (корпоративные)"

    # Кнопка "Выбрать все" / "Снять все"
    all_selected = len(selected_laws) == 2
    select_all_text = "❌ Снять все" if all_selected else "☑️ Выбрать все"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=law_44_text, callback_data="law_toggle_44")],
        [InlineKeyboardButton(text=law_223_text, callback_data="law_toggle_223")],
        [InlineKeyboardButton(text=select_all_text, callback_data="law_select_all")],
        [InlineKeyboardButton(text="✅ Продолжить", callback_data="law_confirm")],
        [InlineKeyboardButton(text="« Назад к регионам", callback_data="back_to_regions")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    status_text = ""
    if selected_laws:
        status_text = f"\n\n<b>Выбрано:</b> {', '.join(selected_laws)}"
    else:
        status_text = "\n\n<i>Не выбрано (будут показаны оба закона)</i>"

    await callback.message.edit_text(
        f"<b>Шаг 6/14:</b> Тип закона\n\n"
        f"<b>44-ФЗ</b> — государственные закупки (бюджетные организации)\n"
        f"<b>223-ФЗ</b> — закупки госкомпаний (Газпром, РЖД и др.)\n\n"
        f"💡 Нажмите на закон для выбора. Можно выбрать оба.{status_text}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "law_select_all", FilterSearchStates.waiting_for_law_type)
async def process_law_select_all(callback: CallbackQuery, state: FSMContext):
    """Выбрать все / Снять все для типа закона."""
    await callback.answer()

    data = await state.get_data()
    selected_laws = data.get('selected_laws', [])

    # Если все выбраны - снимаем все, иначе выбираем все
    if len(selected_laws) == 2:
        selected_laws = []
    else:
        selected_laws = ["44-ФЗ", "223-ФЗ"]

    await state.update_data(selected_laws=selected_laws)

    # Обновляем клавиатуру
    law_44_text = "✅ 44-ФЗ (госзакупки)" if "44-ФЗ" in selected_laws else "☐ 44-ФЗ (госзакупки)"
    law_223_text = "✅ 223-ФЗ (корпоративные)" if "223-ФЗ" in selected_laws else "☐ 223-ФЗ (корпоративные)"

    all_selected = len(selected_laws) == 2
    select_all_text = "❌ Снять все" if all_selected else "☑️ Выбрать все"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=law_44_text, callback_data="law_toggle_44")],
        [InlineKeyboardButton(text=law_223_text, callback_data="law_toggle_223")],
        [InlineKeyboardButton(text=select_all_text, callback_data="law_select_all")],
        [InlineKeyboardButton(text="✅ Продолжить", callback_data="law_confirm")],
        [InlineKeyboardButton(text="« Назад к регионам", callback_data="back_to_regions")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    status_text = ""
    if selected_laws:
        status_text = f"\n\n<b>Выбрано:</b> {', '.join(selected_laws)}"
    else:
        status_text = "\n\n<i>Не выбрано (будут показаны оба закона)</i>"

    await callback.message.edit_text(
        f"<b>Шаг 6/14:</b> Тип закона\n\n"
        f"<b>44-ФЗ</b> — государственные закупки (бюджетные организации)\n"
        f"<b>223-ФЗ</b> — закупки госкомпаний (Газпром, РЖД и др.)\n\n"
        f"💡 Нажмите на закон для выбора. Можно выбрать оба.{status_text}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "law_confirm", FilterSearchStates.waiting_for_law_type)
async def process_law_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение выбора типа закона."""
    await callback.answer()

    data = await state.get_data()
    selected_laws = data.get('selected_laws', [])

    # Сохраняем law_type для совместимости (None если оба или ничего не выбрано)
    if len(selected_laws) == 1:
        law_type = selected_laws[0]
    else:
        law_type = None  # Оба закона или ничего

    await state.update_data(law_type=law_type, law_types=selected_laws)
    await ask_for_purchase_stage(callback.message, state)


async def ask_for_purchase_stage(message: Message, state: FSMContext):
    """Запрос этапа закупки."""
    await state.set_state(FilterSearchStates.waiting_for_purchase_stage)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Все этапы", callback_data="stage_all")],
        [InlineKeyboardButton(text="📝 Только подача заявок (актуальные)", callback_data="stage_submission")],
        [InlineKeyboardButton(text="« Назад к типу закона", callback_data="back_to_law_type")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await message.answer(
        f"<b>Шаг 7/14:</b> Этап закупки\n\n"
        f"<b>Подача заявок</b> — можно подать заявку прямо сейчас\n"
        f"<b>Все этапы</b> — включая завершённые и на рассмотрении\n\n"
        f"💡 Рекомендуем «Только подача заявок»",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("stage_"), FilterSearchStates.waiting_for_purchase_stage)
async def process_purchase_stage(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора этапа закупки."""
    await callback.answer()

    stage_value = callback.data.replace("stage_", "")
    purchase_stage = "submission" if stage_value == "submission" else None

    await state.update_data(purchase_stage=purchase_stage)
    await ask_for_purchase_method(callback.message, state)


async def ask_for_purchase_method(message: Message, state: FSMContext):
    """Запрос способа закупки (множественный выбор)."""
    await state.set_state(FilterSearchStates.waiting_for_purchase_method)

    # Получаем текущий выбор
    data = await state.get_data()
    selected_methods = data.get('selected_methods', [])

    # Определяем методы
    methods = [
        ("auction", "🔨 Электронный аукцион"),
        ("tender", "📋 Открытый конкурс"),
        ("quotation", "💬 Запрос котировок"),
        ("request", "📝 Запрос предложений"),
    ]

    # Формируем кнопки с галочками
    buttons = []
    for method_id, method_name in methods:
        is_selected = method_id in selected_methods
        text = f"✅ {method_name.split(' ', 1)[1]}" if is_selected else f"☐ {method_name.split(' ', 1)[1]}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"method_toggle_{method_id}")])

    # Кнопка "Выбрать все" / "Снять все"
    all_selected = len(selected_methods) == len(methods)
    select_all_text = "❌ Снять все" if all_selected else "☑️ Выбрать все"
    buttons.append([InlineKeyboardButton(text=select_all_text, callback_data="method_select_all")])

    buttons.append([InlineKeyboardButton(text="✅ Продолжить", callback_data="method_confirm")])
    buttons.append([InlineKeyboardButton(text="« Назад к этапу закупки", callback_data="back_to_purchase_stage")])
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    status_text = ""
    if selected_methods:
        method_names = {"auction": "Аукцион", "tender": "Конкурс", "quotation": "Котировки", "request": "Запрос предложений"}
        selected_names = [method_names.get(m, m) for m in selected_methods]
        status_text = f"\n\n<b>Выбрано:</b> {', '.join(selected_names)}"
    else:
        status_text = "\n\n<i>Не выбрано (будут показаны все способы)</i>"

    await message.answer(
        f"<b>Шаг 8/14:</b> Способ закупки\n\n"
        f"<b>Электронный аукцион</b> — побеждает минимальная цена\n"
        f"<b>Открытый конкурс</b> — оценка по критериям\n"
        f"<b>Запрос котировок</b> — до 3 млн руб\n"
        f"<b>Запрос предложений</b> — сложные закупки\n\n"
        f"💡 Нажмите для выбора. Можно выбрать несколько.{status_text}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("method_toggle_"), FilterSearchStates.waiting_for_purchase_method)
async def process_method_toggle(callback: CallbackQuery, state: FSMContext):
    """Переключение выбора способа закупки."""
    await callback.answer()

    method_id = callback.data.replace("method_toggle_", "")

    data = await state.get_data()
    selected_methods = data.get('selected_methods', [])

    if method_id in selected_methods:
        selected_methods.remove(method_id)
    else:
        selected_methods.append(method_id)

    await state.update_data(selected_methods=selected_methods)

    # Обновляем клавиатуру
    methods = [
        ("auction", "Электронный аукцион"),
        ("tender", "Открытый конкурс"),
        ("quotation", "Запрос котировок"),
        ("request", "Запрос предложений"),
    ]

    buttons = []
    for mid, mname in methods:
        is_selected = mid in selected_methods
        text = f"✅ {mname}" if is_selected else f"☐ {mname}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"method_toggle_{mid}")])

    # Кнопка "Выбрать все" / "Снять все"
    all_selected = len(selected_methods) == len(methods)
    select_all_text = "❌ Снять все" if all_selected else "☑️ Выбрать все"
    buttons.append([InlineKeyboardButton(text=select_all_text, callback_data="method_select_all")])

    buttons.append([InlineKeyboardButton(text="✅ Продолжить", callback_data="method_confirm")])
    buttons.append([InlineKeyboardButton(text="« Назад к этапу закупки", callback_data="back_to_purchase_stage")])
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    status_text = ""
    if selected_methods:
        method_names = {"auction": "Аукцион", "tender": "Конкурс", "quotation": "Котировки", "request": "Запрос предложений"}
        selected_names = [method_names.get(m, m) for m in selected_methods]
        status_text = f"\n\n<b>Выбрано:</b> {', '.join(selected_names)}"
    else:
        status_text = "\n\n<i>Не выбрано (будут показаны все способы)</i>"

    await callback.message.edit_text(
        f"<b>Шаг 8/14:</b> Способ закупки\n\n"
        f"<b>Электронный аукцион</b> — побеждает минимальная цена\n"
        f"<b>Открытый конкурс</b> — оценка по критериям\n"
        f"<b>Запрос котировок</b> — до 3 млн руб\n"
        f"<b>Запрос предложений</b> — сложные закупки\n\n"
        f"💡 Нажмите для выбора. Можно выбрать несколько.{status_text}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "method_select_all", FilterSearchStates.waiting_for_purchase_method)
async def process_method_select_all(callback: CallbackQuery, state: FSMContext):
    """Выбрать все / Снять все для способа закупки."""
    await callback.answer()

    all_methods = ["auction", "tender", "quotation", "request"]

    data = await state.get_data()
    selected_methods = data.get('selected_methods', [])

    # Если все выбраны - снимаем все, иначе выбираем все
    if len(selected_methods) == len(all_methods):
        selected_methods = []
    else:
        selected_methods = all_methods.copy()

    await state.update_data(selected_methods=selected_methods)

    # Обновляем клавиатуру
    methods = [
        ("auction", "Электронный аукцион"),
        ("tender", "Открытый конкурс"),
        ("quotation", "Запрос котировок"),
        ("request", "Запрос предложений"),
    ]

    buttons = []
    for mid, mname in methods:
        is_selected = mid in selected_methods
        text = f"✅ {mname}" if is_selected else f"☐ {mname}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"method_toggle_{mid}")])

    all_selected = len(selected_methods) == len(methods)
    select_all_text = "❌ Снять все" if all_selected else "☑️ Выбрать все"
    buttons.append([InlineKeyboardButton(text=select_all_text, callback_data="method_select_all")])

    buttons.append([InlineKeyboardButton(text="✅ Продолжить", callback_data="method_confirm")])
    buttons.append([InlineKeyboardButton(text="« Назад к этапу закупки", callback_data="back_to_purchase_stage")])
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    status_text = ""
    if selected_methods:
        method_names = {"auction": "Аукцион", "tender": "Конкурс", "quotation": "Котировки", "request": "Запрос предложений"}
        selected_names = [method_names.get(m, m) for m in selected_methods]
        status_text = f"\n\n<b>Выбрано:</b> {', '.join(selected_names)}"
    else:
        status_text = "\n\n<i>Не выбрано (будут показаны все способы)</i>"

    await callback.message.edit_text(
        f"<b>Шаг 8/14:</b> Способ закупки\n\n"
        f"<b>Электронный аукцион</b> — побеждает минимальная цена\n"
        f"<b>Открытый конкурс</b> — оценка по критериям\n"
        f"<b>Запрос котировок</b> — до 3 млн руб\n"
        f"<b>Запрос предложений</b> — сложные закупки\n\n"
        f"💡 Нажмите для выбора. Можно выбрать несколько.{status_text}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "method_confirm", FilterSearchStates.waiting_for_purchase_method)
async def process_method_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение выбора способа закупки."""
    await callback.answer()

    data = await state.get_data()
    selected_methods = data.get('selected_methods', [])

    # Сохраняем purchase_method для совместимости
    if len(selected_methods) == 1:
        purchase_method = selected_methods[0]
    else:
        purchase_method = None

    await state.update_data(purchase_method=purchase_method, purchase_methods=selected_methods)
    await ask_for_tender_type(callback.message, state)


async def ask_for_tender_type(message: Message, state: FSMContext):
    """Запрос типа закупки (множественный выбор)."""
    await state.set_state(FilterSearchStates.waiting_for_tender_type)

    # Получаем текущий выбор
    data = await state.get_data()
    selected_types = data.get('selected_tender_types', [])

    # Определяем типы
    types = [
        ("goods", "📦 Товары (поставка)"),
        ("services", "🔧 Услуги"),
        ("works", "🏗️ Работы"),
    ]

    # Формируем кнопки с галочками
    buttons = []
    for type_id, type_name in types:
        is_selected = type_id in selected_types
        text = f"✅ {type_name.split(' ', 1)[1]}" if is_selected else f"☐ {type_name.split(' ', 1)[1]}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"ttype_toggle_{type_id}")])

    # Кнопка "Выбрать все" / "Снять все"
    all_selected = len(selected_types) == len(types)
    select_all_text = "❌ Снять все" if all_selected else "☑️ Выбрать все"
    buttons.append([InlineKeyboardButton(text=select_all_text, callback_data="ttype_select_all")])

    buttons.append([InlineKeyboardButton(text="✅ Продолжить", callback_data="ttype_confirm")])
    buttons.append([InlineKeyboardButton(text="« Назад к способу закупки", callback_data="back_to_purchase_method")])
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    status_text = ""
    if selected_types:
        type_names = {"goods": "Товары", "services": "Услуги", "works": "Работы"}
        selected_names = [type_names.get(t, t) for t in selected_types]
        status_text = f"\n\n<b>Выбрано:</b> {', '.join(selected_names)}"
    else:
        status_text = "\n\n<i>Не выбрано (будут показаны все типы)</i>"

    await message.answer(
        f"<b>Шаг 9/14:</b> Тип закупки\n\n"
        f"<b>Товары</b> — поставка продукции\n"
        f"<b>Услуги</b> — обслуживание, консалтинг\n"
        f"<b>Работы</b> — строительство, ремонт\n\n"
        f"💡 Нажмите для выбора. Можно выбрать несколько.{status_text}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("ttype_toggle_"), FilterSearchStates.waiting_for_tender_type)
async def process_ttype_toggle(callback: CallbackQuery, state: FSMContext):
    """Переключение выбора типа закупки."""
    await callback.answer()

    type_id = callback.data.replace("ttype_toggle_", "")

    data = await state.get_data()
    selected_types = data.get('selected_tender_types', [])

    if type_id in selected_types:
        selected_types.remove(type_id)
    else:
        selected_types.append(type_id)

    await state.update_data(selected_tender_types=selected_types)

    # Обновляем клавиатуру
    types = [
        ("goods", "Товары (поставка)"),
        ("services", "Услуги"),
        ("works", "Работы"),
    ]

    buttons = []
    for tid, tname in types:
        is_selected = tid in selected_types
        text = f"✅ {tname}" if is_selected else f"☐ {tname}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"ttype_toggle_{tid}")])

    # Кнопка "Выбрать все" / "Снять все"
    all_selected = len(selected_types) == len(types)
    select_all_text = "❌ Снять все" if all_selected else "☑️ Выбрать все"
    buttons.append([InlineKeyboardButton(text=select_all_text, callback_data="ttype_select_all")])

    buttons.append([InlineKeyboardButton(text="✅ Продолжить", callback_data="ttype_confirm")])
    buttons.append([InlineKeyboardButton(text="« Назад к способу закупки", callback_data="back_to_purchase_method")])
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    status_text = ""
    if selected_types:
        type_names = {"goods": "Товары", "services": "Услуги", "works": "Работы"}
        selected_names = [type_names.get(t, t) for t in selected_types]
        status_text = f"\n\n<b>Выбрано:</b> {', '.join(selected_names)}"
    else:
        status_text = "\n\n<i>Не выбрано (будут показаны все типы)</i>"

    await callback.message.edit_text(
        f"<b>Шаг 9/14:</b> Тип закупки\n\n"
        f"<b>Товары</b> — поставка продукции\n"
        f"<b>Услуги</b> — обслуживание, консалтинг\n"
        f"<b>Работы</b> — строительство, ремонт\n\n"
        f"💡 Нажмите для выбора. Можно выбрать несколько.{status_text}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "ttype_select_all", FilterSearchStates.waiting_for_tender_type)
async def process_ttype_select_all(callback: CallbackQuery, state: FSMContext):
    """Выбрать все / Снять все для типа закупки."""
    await callback.answer()

    all_types = ["goods", "services", "works"]

    data = await state.get_data()
    selected_types = data.get('selected_tender_types', [])

    # Если все выбраны - снимаем все, иначе выбираем все
    if len(selected_types) == len(all_types):
        selected_types = []
    else:
        selected_types = all_types.copy()

    await state.update_data(selected_tender_types=selected_types)

    # Обновляем клавиатуру
    types = [
        ("goods", "Товары (поставка)"),
        ("services", "Услуги"),
        ("works", "Работы"),
    ]

    buttons = []
    for tid, tname in types:
        is_selected = tid in selected_types
        text = f"✅ {tname}" if is_selected else f"☐ {tname}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"ttype_toggle_{tid}")])

    all_selected = len(selected_types) == len(types)
    select_all_text = "❌ Снять все" if all_selected else "☑️ Выбрать все"
    buttons.append([InlineKeyboardButton(text=select_all_text, callback_data="ttype_select_all")])

    buttons.append([InlineKeyboardButton(text="✅ Продолжить", callback_data="ttype_confirm")])
    buttons.append([InlineKeyboardButton(text="« Назад к способу закупки", callback_data="back_to_purchase_method")])
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    status_text = ""
    if selected_types:
        type_names = {"goods": "Товары", "services": "Услуги", "works": "Работы"}
        selected_names = [type_names.get(t, t) for t in selected_types]
        status_text = f"\n\n<b>Выбрано:</b> {', '.join(selected_names)}"
    else:
        status_text = "\n\n<i>Не выбрано (будут показаны все типы)</i>"

    await callback.message.edit_text(
        f"<b>Шаг 9/14:</b> Тип закупки\n\n"
        f"<b>Товары</b> — поставка продукции\n"
        f"<b>Услуги</b> — обслуживание, консалтинг\n"
        f"<b>Работы</b> — строительство, ремонт\n\n"
        f"💡 Нажмите для выбора. Можно выбрать несколько.{status_text}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "ttype_confirm", FilterSearchStates.waiting_for_tender_type)
async def process_ttype_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение выбора типа закупки."""
    await callback.answer()

    data = await state.get_data()
    selected_types = data.get('selected_tender_types', [])

    # Преобразуем в формат для сохранения
    tender_types_map = {"goods": "товары", "services": "услуги", "works": "работы"}
    tender_types = [tender_types_map.get(t, t) for t in selected_types]

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
        [InlineKeyboardButton(text="⏭️ Без ограничений", callback_data="deadline_skip")],
        [InlineKeyboardButton(text="« Назад к типу закупки", callback_data="back_to_tender_type")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await message.answer(
        f"<b>Шаг 10/14:</b> Минимум дней до дедлайна\n\n"
        f"Сколько дней минимум должно оставаться до окончания подачи заявок?\n\n"
        f"💡 Это поможет отфильтровать тендеры, на которые не успеете подать заявку",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("deadline_"), FilterSearchStates.waiting_for_min_deadline)
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
        [InlineKeyboardButton(text="⏭️ Пропустить", callback_data="customer_skip")],
        [InlineKeyboardButton(text="« Назад к дедлайну", callback_data="back_to_min_deadline")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await message.answer(
        f"<b>Шаг 11/14:</b> Фильтр по заказчику\n\n"
        f"Введите ключевые слова для фильтрации по названию заказчика:\n"
        f"Например: <i>больница, школа, университет</i>\n\n"
        f"Или нажмите «Пропустить» для поиска среди всех заказчиков",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "customer_skip", FilterSearchStates.waiting_for_customer_keywords)
async def skip_customer_keywords(callback: CallbackQuery, state: FSMContext):
    """Пропуск фильтра по заказчику."""
    await callback.answer()
    await state.update_data(customer_keywords=[])
    await ask_for_okpd2(callback.message, state)


@router.message(FilterSearchStates.waiting_for_customer_keywords)
async def process_customer_keywords(message: Message, state: FSMContext):
    """Обработка ключевых слов заказчика."""
    # Проверяем, не нажал ли пользователь системную кнопку
    if message.text in ["🏠 Главное меню", "🎯 Tender Sniper", "📊 Мои фильтры", "📊 Все мои тендеры", "⭐ Избранное", "📈 Статистика"]:
        await state.clear()
        return

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
        [InlineKeyboardButton(text="⏭️ Пропустить", callback_data="okpd_skip")],
        [InlineKeyboardButton(text="✍️ Ввести код вручную", callback_data="okpd_custom")],
        [InlineKeyboardButton(text="💻 26 - Компьютеры и электроника", callback_data="okpd_26")],
        [InlineKeyboardButton(text="🏗️ 41-43 - Строительство", callback_data="okpd_41")],
        [InlineKeyboardButton(text="🚗 29 - Автотранспорт", callback_data="okpd_29")],
        [InlineKeyboardButton(text="💊 21 - Лекарства", callback_data="okpd_21")],
        [InlineKeyboardButton(text="🍞 10 - Продукты питания", callback_data="okpd_10")],
        [InlineKeyboardButton(text="« Назад к заказчику", callback_data="back_to_customer_keywords")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await message.answer(
        f"<b>Шаг 12/14:</b> Код ОКПД2\n\n"
        f"ОКПД2 — классификатор продукции для точного поиска.\n\n"
        f"Выберите категорию или введите код вручную:\n"
        f"Например: <code>26.20</code> (компьютеры)\n\n"
        f"💡 Можете пропустить для поиска по всем категориям",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("okpd_"), FilterSearchStates.waiting_for_okpd2)
async def process_okpd2_callback(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора ОКПД2."""
    await callback.answer()

    okpd_value = callback.data.replace("okpd_", "")

    if okpd_value == "skip":
        await state.update_data(okpd2_codes=[])
        await ask_for_search_mode(callback.message, state)
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
        await ask_for_search_mode(callback.message, state)


@router.message(FilterSearchStates.waiting_for_okpd2)
async def process_okpd2_text(message: Message, state: FSMContext):
    """Обработка текстового ввода ОКПД2."""
    # Проверяем, не нажал ли пользователь системную кнопку
    if message.text in ["🏠 Главное меню", "🎯 Tender Sniper", "📊 Мои фильтры", "📊 Все мои тендеры", "⭐ Избранное", "📈 Статистика"]:
        await state.clear()
        return

    okpd_input = message.text.strip()

    if okpd_input:
        okpd2_codes = [code.strip() for code in okpd_input.split(',') if code.strip()]
    else:
        okpd2_codes = []

    await state.update_data(okpd2_codes=okpd2_codes)
    await ask_for_search_mode(message, state)


async def ask_for_search_mode(message: Message, state: FSMContext):
    """Запрос режима поиска (точный или расширенный)."""
    await state.set_state(FilterSearchStates.waiting_for_search_mode)

    # Получаем ключевые слова для подсказки
    data = await state.get_data()
    keywords = data.get('keywords', [])
    keywords_str = ', '.join(keywords[:3])
    if len(keywords) > 3:
        keywords_str += f' (+{len(keywords) - 3})'

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔍 Расширенный поиск (рекомендуется)",
            callback_data="search_mode_expanded"
        )],
        [InlineKeyboardButton(
            text="🎯 Точный поиск",
            callback_data="search_mode_exact"
        )],
        [InlineKeyboardButton(text="« Назад к ОКПД2", callback_data="back_to_okpd2")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await message.answer(
        f"<b>Шаг 13/14:</b> Режим поиска\n\n"
        f"Ваши ключевые слова: <code>{keywords_str}</code>\n\n"
        f"<b>🔍 Расширенный поиск</b>\n"
        f"AI добавит синонимы и связанные термины.\n"
        f"Подходит для: <i>компьютеры, мебель, канцелярия</i>\n\n"
        f"<b>🎯 Точный поиск</b>\n"
        f"Только указанные вами слова, без расширения.\n"
        f"Подходит для: <i>Atlas Copco, Komatsu, Linux, SAP</i>\n\n"
        f"💡 Для брендов и узкоспециализированных терминов выбирайте точный поиск",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "search_mode_expanded", FilterSearchStates.waiting_for_search_mode)
async def process_search_mode_expanded(callback: CallbackQuery, state: FSMContext):
    """Выбран расширенный поиск."""
    await callback.answer()
    await state.update_data(exact_match=False)
    await ask_for_tender_count(callback.message, state)


@router.callback_query(F.data == "search_mode_exact", FilterSearchStates.waiting_for_search_mode)
async def process_search_mode_exact(callback: CallbackQuery, state: FSMContext):
    """Выбран точный поиск."""
    await callback.answer("🎯 Точный поиск выбран")
    await state.update_data(exact_match=True)
    await ask_for_tender_count(callback.message, state)


async def ask_for_tender_count(message: Message, state: FSMContext):
    """Запрос количества тендеров."""
    await state.set_state(FilterSearchStates.waiting_for_tender_count)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Назад к режиму поиска", callback_data="back_to_search_mode")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ])

    await message.answer(
        f"<b>Шаг 14/14:</b> Количество тендеров\n\n"
        f"Сколько тендеров найти?\n"
        f"Введите число от <code>1</code> до <code>25</code>\n\n"
        f"💡 Рекомендуем 10-15 для быстрого результата",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.message(FilterSearchStates.waiting_for_tender_count)
async def process_tender_count(message: Message, state: FSMContext):
    """Обработка количества тендеров."""
    # Проверяем, не нажал ли пользователь системную кнопку
    if message.text in ["🏠 Главное меню", "🎯 Tender Sniper", "📊 Мои фильтры", "📊 Все мои тендеры", "⭐ Избранное", "📈 Статистика"]:
        await state.clear()
        return

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

    # ВАЖНО: Проверяем что ключевые слова не потерялись
    keywords = data.get('keywords', [])
    if not keywords:
        logger.error(f"❌ Keywords потеряны! Data: {data}")
        await message.answer(
            "⚠️ <b>Произошла ошибка</b>\n\n"
            "Данные сессии были потеряны (возможно, бот перезапускался).\n\n"
            "Пожалуйста, начните заново:\n"
            "• Нажмите 🎯 <b>Tender Sniper</b>\n"
            "• Затем <b>Мгновенный поиск</b>",
            parse_mode="HTML"
        )
        await state.clear()
        return

    logger.info(f"✅ Keywords сохранены: {keywords}")

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

        # Генерируем название фильтра если не указано
        filter_name = data.get('filter_name')
        if not filter_name:
            # Автоматическое название на основе ключевых слов
            keywords = data.get('keywords', [])
            if keywords:
                filter_name = ', '.join(keywords[:3])  # Первые 3 ключевых слова
                if len(filter_name) > 50:
                    filter_name = filter_name[:47] + '...'
            else:
                # Если нет ключевых слов - используем дату
                from datetime import datetime
                filter_name = f"Фильтр {datetime.now().strftime('%d.%m.%Y %H:%M')}"

            logger.info(f"Автоматически сгенерировано название фильтра: {filter_name}")

        # Валидация входных данных
        try:
            validated_data = FilterCreate(
                name=filter_name,
                keywords=data.get('keywords', []),
                price_min=data.get('price_min'),
                price_max=data.get('price_max'),
                regions=data.get('regions', [])
            )
            logger.info(f"✅ Валидация данных фильтра прошла успешно")
        except ValidationError as e:
            error_messages = []
            for error in e.errors():
                field = error['loc'][0] if error['loc'] else 'unknown'
                msg = error['msg']
                error_messages.append(f"• {field}: {msg}")

            await message.answer(
                f"❌ <b>Ошибка валидации данных:</b>\n\n" + "\n".join(error_messages),
                parse_mode="HTML"
            )
            await state.clear()
            return

        # 1. Сохраняем фильтр в БД с новыми критериями
        # is_active=False для with_instant_search (требует подтверждения)
        # is_active=True для прямого создания (сразу активен)
        exact_match = data.get('exact_match', False)
        filter_id = await db.create_filter(
            user_id=user['id'],
            name=filter_name,
            keywords=data.get('keywords', []),
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
            exact_match=exact_match,  # Режим поиска
            is_active=False if with_instant_search else True  # Активен только если без поиска
        )

        # РЕЖИМ 1: С мгновенным поиском
        if with_instant_search:
            # 2. AI расширение критериев (только если не точный поиск)
            expanded_keywords = []

            if exact_match:
                # Точный поиск - без AI расширения
                await progress_msg.edit_text(
                    "🔄 <b>Обработка вашего запроса...</b>\n\n"
                    "✅ Шаг 1/3: Фильтр сохранен\n"
                    "🎯 Режим: Точный поиск (без расширения)\n"
                    "⏳ Шаг 2/3: Поиск тендеров на zakupki.gov.ru...",
                    parse_mode="HTML"
                )
            else:
                # Расширенный поиск - с AI
                await progress_msg.edit_text(
                    "🔄 <b>Обработка вашего запроса...</b>\n\n"
                    "✅ Шаг 1/4: Фильтр сохранен\n"
                    "⏳ Шаг 2/4: AI расширяет критерии поиска...",
                    parse_mode="HTML"
                )

                expander = QueryExpander()
                expansion = await expander.expand_keywords(data.get('keywords', []))
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

            # 🧪 БЕТА: Для архивного поиска используем purchase_stage='archive'
            archive_mode = data.get('archive_mode', False)
            if archive_mode:
                purchase_stage = 'archive'
                logger.info("📦 Режим архивного поиска активирован")
            else:
                purchase_stage = data.get('purchase_stage')

            filter_data = {
                'id': filter_id,
                'name': filter_name,
                'keywords': json.dumps(data.get('keywords', []), ensure_ascii=False),
                'exclude_keywords': json.dumps(data.get('exclude_keywords', []), ensure_ascii=False),
                'price_min': data.get('price_min'),
                'price_max': data.get('price_max'),
                'regions': json.dumps(data.get('regions', []), ensure_ascii=False),
                'tender_types': json.dumps(data.get('tender_types', []), ensure_ascii=False),
                'law_type': data.get('law_type'),
                'purchase_stage': purchase_stage,
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

            # Сохраняем результаты поиска в БД (включая архивные тендеры)
            source_type = 'archive_search' if archive_mode else 'instant_search'
            logger.info(f"💾 Сохранение {len(search_results['matches'])} тендеров в БД (источник: {source_type})...")
            saved_count = 0
            skipped_count = 0
            error_count = 0

            for i, match in enumerate(search_results['matches'], 1):
                tender_number = match.get('number', '')

                # DEBUG: Показываем первый тендер полностью
                if i == 1:
                    logger.info(f"   🔍 DEBUG первого тендера:")
                    logger.info(f"      number: {match.get('number')}")
                    logger.info(f"      name: {match.get('name', '')[:50]}...")
                    logger.info(f"      customer: {match.get('customer')}")
                    logger.info(f"      customer_name: {match.get('customer_name')}")
                    logger.info(f"      customer_region: {match.get('customer_region')}")
                    logger.info(f"      region: {match.get('region')}")
                    logger.info(f"      price: {match.get('price')}")
                    logger.info(f"      published: {match.get('published')}")

                # Проверяем дубликат
                already_saved = await db.is_tender_notified(tender_number, user['id'])
                if already_saved:
                    logger.debug(f"   ⏭️  {tender_number} уже сохранен, пропускаем")
                    skipped_count += 1
                    continue

                try:
                    # Формируем данные тендера
                    tender_data = {
                        'number': tender_number,
                        'name': match.get('name', ''),
                        'price': match.get('price'),
                        'url': match.get('url', ''),
                        'region': match.get('customer_region', match.get('region', '')),
                        'customer_name': match.get('customer', match.get('customer_name', '')),
                        'published_date': match.get('published', match.get('published_date', ''))
                    }

                    logger.info(f"   💾 [{i}/{len(search_results['matches'])}] {tender_number}: "
                              f"region='{tender_data['region']}', customer='{tender_data['customer_name'][:30] if tender_data['customer_name'] else 'None'}...'")

                    await db.save_notification(
                        user_id=user['id'],
                        filter_id=filter_id,
                        filter_name=filter_name,
                        tender_data=tender_data,
                        score=match.get('match_score', 0),
                        matched_keywords=match.get('match_reasons', []),
                        source=source_type
                    )
                    saved_count += 1

                except Exception as e:
                    logger.error(f"   ❌ Не удалось сохранить {tender_number}: {e}", exc_info=True)
                    error_count += 1

            logger.info(f"✅ Тендеры обработаны: сохранено {saved_count}, пропущено {skipped_count}, ошибок {error_count}")

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

            # Получаем лимиты тарифа для отображения (хардкод, пока не мигрирован на PostgreSQL)
            daily_limit = 10 if user['subscription_tier'] == 'free' else 50

            # Отправляем результаты
            await progress_msg.edit_text(
                "✅ <b>Готово!</b>\n\n"
                f"📊 Найдено тендеров: {search_results['total_found']}\n"
                f"🎯 Релевантных: {len(search_results['matches'])}\n"
                f"🔥 Отличных (≥70): {search_results['stats'].get('high_score_count', 0)}\n\n"
                f"📄 Отправляю HTML отчет...",
                parse_mode="HTML"
            )

            # 🧪 БЕТА: Разные сообщения для архива и обычного поиска
            if archive_mode:
                # Архивный поиск - тендеры уже завершены
                await message.answer_document(
                    document=FSInputFile(report_path),
                    caption=(
                        f"📦 <b>Результаты поиска в архиве</b> 🧪 БЕТА\n\n"
                        f"Поиск: <b>{filter_name}</b>\n"
                        f"Найдено: {search_results['total_found']} архивных тендеров\n"
                        f"💾 Сохранено в базу: {saved_count}\n\n"
                        f"💡 Это завершённые тендеры с прошедшим сроком подачи заявок.\n"
                        f"Используйте для анализа цен и конкурентов."
                    ),
                    parse_mode="HTML"
                )

                # Для архивного поиска - ссылки на все тендеры и новый поиск
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="📊 Все мои тендеры",
                        callback_data="sniper_all_tenders"
                    )],
                    [InlineKeyboardButton(
                        text="📦 Новый поиск в архиве",
                        callback_data="sniper_archive_search"
                    )],
                    [InlineKeyboardButton(
                        text="🔍 Поиск актуальных",
                        callback_data="sniper_new_search"
                    )],
                    [InlineKeyboardButton(
                        text="🏠 Главное меню",
                        callback_data="main_menu"
                    )]
                ])

                await message.answer(
                    "📦 <b>Поиск в архиве завершён</b>\n\n"
                    f"✅ Тендеры сохранены в базу данных.\n"
                    "Мониторинг для архивных тендеров недоступен.\n\n"
                    "Используйте данные для анализа рынка и конкурентов.",
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )

                # Удаляем временный фильтр (архивный поиск - разовый)
                try:
                    await db.delete_filter(filter_id)
                    logger.info(f"🗑️ Временный фильтр {filter_id} удален (архивный поиск)")
                except Exception as e:
                    logger.warning(f"Не удалось удалить временный фильтр: {e}")

            else:
                # Обычный поиск - предлагаем мониторинг
                await message.answer_document(
                    document=FSInputFile(report_path),
                    caption=(
                        f"📊 <b>Результаты поиска</b>\n\n"
                        f"Фильтр: <b>{filter_name}</b>\n"
                        f"Найдено: {search_results['total_found']} тендеров\n\n"
                        f"🤖 AI расширил ваш запрос с {len(data.get('keywords', []))} до {len(data.get('keywords', [])) + len(expanded_keywords)} терминов"
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
                    )],
                    [InlineKeyboardButton(
                        text="🏠 Главное меню",
                        callback_data="main_menu"
                    )]
                ])

                await message.answer(
                    "💡 <b>Хотите получать автоматические уведомления?</b>\n\n"
                    "Включите автоматический мониторинг, и бот будет присылать вам\n"
                    "уведомления о новых тендерах по этим критериям каждые 5 минут.\n\n"
                    f"🆓 Ваш лимит: {daily_limit} уведомлений в день",
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

            # Получаем лимиты (хардкод, пока не мигрирован на PostgreSQL)
            daily_limit = 10 if user['subscription_tier'] == 'free' else 50

            # Формируем описание фильтра
            filter_summary = f"📝 <b>{filter_name}</b>\n\n"
            keywords = data.get('keywords', [])
            if keywords:
                filter_summary += f"🔑 Ключевые слова: {', '.join(keywords)}\n"

            if data.get('price_min') or data.get('price_max'):
                price_min = f"{data.get('price_min'):,}" if data.get('price_min') else "0"
                price_max = f"{data.get('price_max'):,}" if data.get('price_max') else "∞"
                filter_summary += f"💰 Цена: {price_min} - {price_max} ₽\n"

            if data.get('regions'):
                filter_summary += f"📍 Регионы: {', '.join(data.get('regions', []))}\n"

            if data.get('min_deadline_days'):
                filter_summary += f"⏰ Минимум дней до дедлайна: {data['min_deadline_days']}\n"

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Мои фильтры", callback_data="sniper_my_filters")],
                [InlineKeyboardButton(text="🎯 Меню Sniper", callback_data="sniper_menu")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])

            await message.answer(
                f"✅ <b>Фильтр успешно создан и активирован!</b>\n\n"
                f"{filter_summary}\n"
                f"🔔 <b>Автоматический мониторинг включен</b>\n\n"
                f"Вы будете получать уведомления о новых подходящих тендерах каждые 5 минут.\n\n"
                f"🆓 Ваш лимит: {daily_limit} уведомлений в день",
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
        await db.update_filter(filter_id, is_active=True)

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
                [InlineKeyboardButton(text="🎯 Меню Sniper", callback_data="sniper_menu")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ])
        )

    except Exception as e:
        logger.error(f"Error enabling monitoring for filter {filter_id}: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка. Попробуйте позже.")
