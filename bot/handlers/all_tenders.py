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
    Генерация HTML отчета со всеми тендерами.

    Args:
        tenders: Список тендеров
        user_id: ID пользователя
        filter_params: Параметры фильтрации

    Returns:
        Путь к HTML файлу
    """
    # Создаем директорию для отчетов
    reports_dir = Path(f"reports/user_{user_id}")
    reports_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = reports_dir / f"all_tenders_{timestamp}.html"

    # Формируем HTML
    html_content = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Все мои тендеры - {datetime.now().strftime("%d.%m.%Y")}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
        }}
        .summary {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }}
        .tender-card {{
            background: white;
            padding: 20px;
            margin-bottom: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            border-left: 4px solid #3498db;
        }}
        .tender-header {{
            display: flex;
            justify-content: space-between;
            align-items: start;
            margin-bottom: 15px;
        }}
        .tender-number {{
            font-size: 0.9em;
            color: #7f8c8d;
            font-weight: 600;
        }}
        .tender-score {{
            background: #3498db;
            color: white;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 0.9em;
            font-weight: bold;
        }}
        .tender-score.high {{ background: #27ae60; }}
        .tender-score.medium {{ background: #f39c12; }}
        .tender-score.low {{ background: #95a5a6; }}
        .tender-name {{
            font-size: 1.1em;
            font-weight: 600;
            color: #2c3e50;
            margin-bottom: 10px;
        }}
        .tender-info {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 10px;
            margin-bottom: 10px;
        }}
        .info-item {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .info-label {{
            font-weight: 600;
            color: #7f8c8d;
        }}
        .info-value {{
            color: #2c3e50;
        }}
        .price {{
            color: #27ae60;
            font-weight: 700;
            font-size: 1.1em;
        }}
        .filter-badge {{
            display: inline-block;
            background: #ecf0f1;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 0.85em;
            margin-right: 5px;
            color: #34495e;
        }}
        .source-badge {{
            display: inline-block;
            background: #e8f4f8;
            color: #2980b9;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 0.85em;
            font-weight: 600;
        }}
        a {{
            color: #3498db;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <h1>📊 Все мои тендеры</h1>

    <div class="summary">
        <h2>Сводка</h2>
        <p><strong>Всего тендеров:</strong> {len(tenders)}</p>
        <p><strong>Дата формирования:</strong> {datetime.now().strftime("%d.%m.%Y %H:%M")}</p>
"""

    # Добавляем информацию о фильтрах
    if filter_params.get('sort_by'):
        sort_labels = {
            'date_desc': 'По дате (новые первые)',
            'date_asc': 'По дате (старые первые)',
            'price_desc': 'По цене (от большей к меньшей)',
            'price_asc': 'По цене (от меньшей к большей)',
            'score_desc': 'По релевантности (лучшие первые)'
        }
        html_content += f"<p><strong>Сортировка:</strong> {sort_labels.get(filter_params['sort_by'], filter_params['sort_by'])}</p>"

    if filter_params.get('price_min') or filter_params.get('price_max'):
        price_range = f"{filter_params.get('price_min', 0):,.0f} - {filter_params.get('price_max', '∞'):,.0f} ₽"
        html_content += f"<p><strong>Ценовой диапазон:</strong> {price_range}</p>"

    html_content += "</div>"

    # Добавляем тендеры
    for i, tender in enumerate(tenders, 1):
        score = tender.get('score', 0)
        score_class = 'high' if score >= 70 else 'medium' if score >= 50 else 'low'

        price_text = f"{tender.get('price'):,.0f} ₽" if tender.get('price') else 'Не указана'

        source_label = "🤖 Автомониторинг" if tender.get('source') == 'automonitoring' else "🔍 Мгновенный поиск"

        html_content += f"""
    <div class="tender-card">
        <div class="tender-header">
            <div class="tender-number">#{i} • {tender.get('number', 'N/A')}</div>
            <div class="tender-score {score_class}">{score}%</div>
        </div>

        <div class="tender-name">{tender.get('name', 'Без названия')}</div>

        <div class="tender-info">
            <div class="info-item">
                <span class="info-label">💰 Цена:</span>
                <span class="info-value price">{price_text}</span>
            </div>
            <div class="info-item">
                <span class="info-label">📍 Регион:</span>
                <span class="info-value">{tender.get('region', 'Не указан')}</span>
            </div>
            <div class="info-item">
                <span class="info-label">🏢 Заказчик:</span>
                <span class="info-value">{tender.get('customer_name', 'Не указан')}</span>
            </div>
            <div class="info-item">
                <span class="info-label">📅 Дата:</span>
                <span class="info-value">{tender.get('published_date', 'N/A')}</span>
            </div>
        </div>

        <div>
            <span class="source-badge">{source_label}</span>
            {f'<span class="filter-badge">Фильтр: {tender.get("filter_name")}</span>' if tender.get('filter_name') else ''}
        </div>

        {f'<p style="margin-top: 10px;"><a href="{tender.get("url")}" target="_blank">🔗 Открыть на zakupki.gov.ru</a></p>' if tender.get('url') else ''}
    </div>
"""

    html_content += """
</body>
</html>
"""

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
