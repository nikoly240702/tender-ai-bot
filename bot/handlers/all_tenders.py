"""
Все мои тендеры - единая история найденных тендеров.

Объединяет результаты из:
- Мгновенного поиска (instant search)
- Автомониторинга (sniper notifications)

С возможностью фильтрации по:
- Срокам подачи заявок
- Цене (от большей к меньшей и наоборот)
- Региону
- Дате публикации
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from tender_sniper.database import get_sniper_db

logger = logging.getLogger(__name__)
router = Router()


class AllTendersStates(StatesGroup):
    """Состояния для просмотра всех тендеров."""
    viewing_list = State()
    viewing_details = State()
    filtering = State()


# ============================================
# ФУНКЦИИ ДЛЯ ОБЪЕДИНЕНИЯ И ФИЛЬТРАЦИИ
# ============================================

async def get_all_user_tenders(user_id: int) -> List[Dict[str, Any]]:
    """
    Получить все тендеры пользователя из всех источников.

    Args:
        user_id: Telegram ID пользователя

    Returns:
        Список тендеров с метаинформацией
    """
    db = await get_sniper_db()
    user = await db.get_user_by_telegram_id(user_id)

    if not user:
        return []

    # Получаем тендеры из sniper notifications
    sniper_tenders = await db.get_user_tenders(user['id'], limit=1000)

    # Преобразуем в единый формат
    all_tenders = []

    for tender in sniper_tenders:
        all_tenders.append({
            'number': tender['number'],
            'name': tender['name'],
            'price': tender.get('price'),
            'url': tender.get('url'),
            'region': tender.get('region'),
            'customer_name': tender.get('customer_name'),
            'score': tender.get('score', 0),
            'filter_name': tender.get('filter_name'),
            'published_date': tender.get('published_date'),
            'sent_at': tender.get('sent_at'),
            'source': 'automonitoring'
        })

    # TODO: Добавить тендеры из instant search results
    # Когда будет БД для instant search results

    return all_tenders


def filter_tenders(
    tenders: List[Dict[str, Any]],
    sort_by: str = 'date_desc',
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    deadline_days: Optional[int] = None,
    region: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Фильтрация и сортировка тендеров.

    Args:
        tenders: Список тендеров
        sort_by: Тип сортировки (date_desc, date_asc, price_desc, price_asc, deadline_asc)
        price_min: Минимальная цена
        price_max: Максимальная цена
        deadline_days: Минимум дней до дедлайна
        region: Фильтр по региону

    Returns:
        Отфильтрованный и отсортированный список
    """
    filtered = tenders.copy()

    # Фильтр по цене
    if price_min is not None:
        filtered = [t for t in filtered if t.get('price') and t['price'] >= price_min]

    if price_max is not None:
        filtered = [t for t in filtered if t.get('price') and t['price'] <= price_max]

    # Фильтр по региону
    if region:
        filtered = [t for t in filtered if region.lower() in (t.get('region') or '').lower()]

    # Фильтр по дедлайну (пока пропускаем, нужно добавить поле deadline в БД)
    # if deadline_days is not None:
    #     ...

    # Сортировка
    if sort_by == 'date_desc':
        filtered.sort(key=lambda x: x.get('sent_at') or x.get('published_date') or '', reverse=True)
    elif sort_by == 'date_asc':
        filtered.sort(key=lambda x: x.get('sent_at') or x.get('published_date') or '')
    elif sort_by == 'price_desc':
        filtered.sort(key=lambda x: x.get('price') or 0, reverse=True)
    elif sort_by == 'price_asc':
        filtered.sort(key=lambda x: x.get('price') or 0)
    elif sort_by == 'score_desc':
        filtered.sort(key=lambda x: x.get('score') or 0, reverse=True)

    return filtered


async def generate_all_tenders_html(
    tenders: List[Dict[str, Any]],
    user_id: int,
    filter_params: Dict[str, Any]
) -> str:
    """
    Генерация HTML отчета со всеми тендерами используя all_tenders_report.

    Args:
        tenders: Список тендеров
        user_id: ID пользователя
        filter_params: Параметры фильтрации

    Returns:
        Путь к HTML файлу
    """
    from tender_sniper.all_tenders_report import generate_html_report

    # Создаем директорию для отчетов
    reports_dir = Path(f"reports/user_{user_id}")
    reports_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = reports_dir / f"all_tenders_{timestamp}.html"

    # Преобразуем данные в нужный формат (убираем None, заменяем на дефолты)
    formatted_tenders = []
    for tender in tenders:
        formatted_tenders.append({
            'number': tender.get('number') or 'N/A',
            'name': tender.get('name') or 'Без названия',
            'price': tender.get('price'),  # None это OK для цены
            'url': tender.get('url') or '',
            'customer_name': tender.get('customer_name') or 'Не указан',
            'region': tender.get('region') or 'Не указан',
            'published_date': tender.get('published_date') or '',
            'sent_at': tender.get('sent_at') or datetime.now().isoformat(),
            'filter_name': tender.get('filter_name') or 'Без фильтра',
            'source': tender.get('source') or 'automonitoring'
        })

    # Используем готовый генератор HTML с JavaScript фильтрацией
    html_content = generate_html_report(
        tenders=formatted_tenders,
        username=f"User {user_id}",
        total_count=len(formatted_tenders)
    )

    # Сохраняем файл
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html_content)

    return str(report_path)


# ============================================
# HANDLERS
# ============================================

@router.callback_query(F.data == "sniper_all_tenders")
async def show_all_tenders(callback: CallbackQuery, state: FSMContext):
    """Показать все тендеры пользователя."""
    await callback.answer()

    try:
        # Получаем все тендеры
        tenders = await get_all_user_tenders(callback.from_user.id)

        if not tenders:
            await callback.message.edit_text(
                "📊 <b>Все мои тендеры</b>\n\n"
                "У вас пока нет найденных тендеров.\n\n"
                "Используйте:\n"
                "• 🔍 <b>Мгновенный поиск</b> для быстрого поиска\n"
                "• 🎨 <b>Фильтры</b> для автоматического мониторинга",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")]
                ])
            )
            return

        # Сохраняем тендеры в состоянии
        await state.update_data(all_tenders=tenders, filter_params={'sort_by': 'date_desc'})
        await state.set_state(AllTendersStates.viewing_list)

        # Показываем меню фильтрации
        await show_tenders_menu(callback.message, tenders, {}, state)

    except Exception as e:
        logger.error(f"Ошибка загрузки тендеров: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка при загрузке тендеров")


async def show_tenders_menu(message: Message, tenders: List[Dict], filter_params: Dict, state: FSMContext):
    """Показать меню с тендерами и фильтрами."""
    # Применяем фильтры
    filtered_tenders = filter_tenders(
        tenders,
        sort_by=filter_params.get('sort_by', 'date_desc'),
        price_min=filter_params.get('price_min'),
        price_max=filter_params.get('price_max'),
        region=filter_params.get('region')
    )

    # Статистика
    total_count = len(filtered_tenders)
    automonitoring_count = len([t for t in filtered_tenders if t.get('source') == 'automonitoring'])
    instant_search_count = len([t for t in filtered_tenders if t.get('source') == 'instant_search'])

    # Кнопки фильтрации
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Скачать HTML отчет", callback_data="alltenders_download_html")],
        [
            InlineKeyboardButton(text="📅 Сортировка", callback_data="alltenders_sort"),
            InlineKeyboardButton(text="💰 Цена", callback_data="alltenders_filter_price")
        ],
        [InlineKeyboardButton(text="🔄 Сбросить фильтры", callback_data="alltenders_reset_filters")],
        [InlineKeyboardButton(text="« Назад в Sniper", callback_data="sniper_menu")]
    ])

    text = (
        f"📊 <b>Все мои тендеры</b>\n\n"
        f"<b>Всего:</b> {total_count} тендеров\n"
        f"🤖 Автомониторинг: {automonitoring_count}\n"
        f"🔍 Мгновенный поиск: {instant_search_count}\n\n"
    )

    # Показываем первые 5 тендеров
    text += "<b>Последние тендеры:</b>\n\n"
    for i, tender in enumerate(filtered_tenders[:5], 1):
        price = f"{tender.get('price'):,.0f} ₽" if tender.get('price') else "Не указана"
        text += f"{i}. <b>{tender.get('name', 'Без названия')[:60]}...</b>\n"
        text += f"   💰 {price} | ⭐ {tender.get('score', 0)}%\n\n"

    if total_count > 5:
        text += f"<i>... и еще {total_count - 5} тендеров</i>\n\n"

    text += "💡 Скачайте HTML отчет для просмотра всех тендеров"

    try:
        await message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except:
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "alltenders_download_html")
async def download_all_tenders_html(callback: CallbackQuery, state: FSMContext):
    """Скачать HTML отчет всех тендеров."""
    await callback.answer("Генерирую HTML отчет...")

    try:
        data = await state.get_data()
        tenders = data.get('all_tenders', [])
        filter_params = data.get('filter_params', {})

        # Применяем фильтры
        filtered_tenders = filter_tenders(
            tenders,
            sort_by=filter_params.get('sort_by', 'date_desc'),
            price_min=filter_params.get('price_min'),
            price_max=filter_params.get('price_max'),
            region=filter_params.get('region')
        )

        # Генерируем HTML
        report_path = await generate_all_tenders_html(
            filtered_tenders,
            callback.from_user.id,
            filter_params
        )

        # Отправляем файл
        await callback.message.answer_document(
            document=FSInputFile(report_path),
            caption=f"📊 <b>Все мои тендеры</b>\n\nВсего: {len(filtered_tenders)} тендеров",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка генерации HTML: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка при генерации отчета")


@router.callback_query(F.data == "alltenders_sort")
async def show_sort_menu(callback: CallbackQuery, state: FSMContext):
    """Показать меню сортировки."""
    await callback.answer()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Новые первые", callback_data="alltenders_sort_date_desc")],
        [InlineKeyboardButton(text="📅 Старые первые", callback_data="alltenders_sort_date_asc")],
        [InlineKeyboardButton(text="💰 Цена ↓ (дорогие первые)", callback_data="alltenders_sort_price_desc")],
        [InlineKeyboardButton(text="💰 Цена ↑ (дешевые первые)", callback_data="alltenders_sort_price_asc")],
        [InlineKeyboardButton(text="⭐ Релевантность", callback_data="alltenders_sort_score_desc")],
        [InlineKeyboardButton(text="« Назад", callback_data="alltenders_back")]
    ])

    await callback.message.edit_text(
        "📊 <b>Сортировка тендеров</b>\n\nВыберите тип сортировки:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("alltenders_sort_"))
async def apply_sort(callback: CallbackQuery, state: FSMContext):
    """Применить сортировку."""
    await callback.answer()

    sort_type = callback.data.replace("alltenders_sort_", "")

    data = await state.get_data()
    filter_params = data.get('filter_params', {})
    filter_params['sort_by'] = sort_type

    await state.update_data(filter_params=filter_params)

    # Обновляем меню
    tenders = data.get('all_tenders', [])
    await show_tenders_menu(callback.message, tenders, filter_params, state)


@router.callback_query(F.data == "alltenders_reset_filters")
async def reset_filters(callback: CallbackQuery, state: FSMContext):
    """Сбросить все фильтры."""
    await callback.answer("Фильтры сброшены")

    data = await state.get_data()
    tenders = data.get('all_tenders', [])

    await state.update_data(filter_params={'sort_by': 'date_desc'})

    await show_tenders_menu(callback.message, tenders, {'sort_by': 'date_desc'}, state)


@router.callback_query(F.data == "alltenders_back")
async def back_to_tenders(callback: CallbackQuery, state: FSMContext):
    """Вернуться к списку тендеров."""
    await callback.answer()

    data = await state.get_data()
    tenders = data.get('all_tenders', [])
    filter_params = data.get('filter_params', {})

    await show_tenders_menu(callback.message, tenders, filter_params, state)


__all__ = ['router']
