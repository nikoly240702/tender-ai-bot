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
from bot.utils.access_check import require_feature
from bot.utils.excel_export import generate_tenders_excel_async

# Импортируем AI генератор названий
try:
    from tender_sniper.ai_name_generator import generate_tender_name
except ImportError:
    # Fallback если модуль недоступен
    def generate_tender_name(name, *args, **kwargs):
        return name[:80] + '...' if len(name) > 80 else name

logger = logging.getLogger(__name__)
router = Router()

# Контакт для связи
DEVELOPER_CONTACT = "@nikolai_chizhik"

# Сообщение об ошибке для бета-теста
BETA_ERROR_MESSAGE = (
    "❌ <b>Произошла ошибка</b>\n\n"
    "🧪 Бот находится в стадии бета-тестирования.\n\n"
    f"Если вы столкнулись с ошибкой или багом, пожалуйста, "
    f"свяжитесь с разработчиком: {DEVELOPER_CONTACT}\n\n"
    "Попробуйте нажать /start для перезапуска."
)


class AllTendersStates(StatesGroup):
    """Состояния для просмотра всех тендеров."""
    viewing_list = State()
    viewing_details = State()
    filtering = State()


# ============================================
# ФУНКЦИИ ДЛЯ ОБЪЕДИНЕНИЯ И ФИЛЬТРАЦИИ
# ============================================

async def get_all_user_tenders(user_id: int, filter_expired: bool = True) -> List[Dict[str, Any]]:
    """
    Получить все тендеры пользователя из всех источников.

    Args:
        user_id: Telegram ID пользователя
        filter_expired: Фильтровать тендеры с истёкшим дедлайном (по умолчанию True)

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
    now = datetime.now()

    for tender in sniper_tenders:
        # Фильтруем тендеры с истёкшим дедлайном
        if filter_expired:
            deadline = tender.get('submission_deadline')
            if deadline:
                try:
                    if isinstance(deadline, str):
                        # Пробуем разные форматы
                        deadline_date = None
                        for fmt in ['%Y-%m-%dT%H:%M:%S', '%Y-%m-%d', '%d.%m.%Y']:
                            try:
                                deadline_date = datetime.strptime(deadline[:len(fmt.replace('%', ''))], fmt)
                                break
                            except:
                                continue
                        if deadline_date and deadline_date < now:
                            continue  # Пропускаем просроченные
                    elif hasattr(deadline, 'date') and deadline < now:
                        continue  # datetime объект
                except:
                    pass  # Если не удалось распарсить - не фильтруем

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
            'submission_deadline': tender.get('submission_deadline'),
            'sent_at': tender.get('sent_at'),
            'source': tender.get('source', 'automonitoring')
        })

    # TODO: Добавить тендеры из instant search results
    # Когда будет БД для instant search results

    return all_tenders


def group_tenders_by_filter(tenders: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Группировка тендеров по названию фильтра.

    Args:
        tenders: Список тендеров

    Returns:
        Словарь {filter_name: [список тендеров]}
    """
    from collections import defaultdict

    grouped = defaultdict(list)

    for tender in tenders:
        filter_name = tender.get('filter_name') or 'Без фильтра'
        grouped[filter_name].append(tender)

    return dict(grouped)


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
        sort_by: Тип сортировки (date_desc, date_asc, price_desc, price_asc, deadline_asc, score_desc)
        price_min: Минимальная цена
        price_max: Максимальная цена
        deadline_days: Показать тендеры с дедлайном через X дней или раньше
        region: Фильтр по региону

    Returns:
        Отфильтрованный и отсортированный список
    """
    from datetime import datetime, timezone

    filtered = tenders.copy()

    # Фильтр по цене
    if price_min is not None:
        filtered = [t for t in filtered if t.get('price') and t['price'] >= price_min]

    if price_max is not None:
        filtered = [t for t in filtered if t.get('price') and t['price'] <= price_max]

    # Фильтр по региону
    if region:
        filtered = [t for t in filtered if region.lower() in (t.get('region') or '').lower()]

    # Фильтр по дедлайну
    if deadline_days is not None:
        now = datetime.now(timezone.utc)
        cutoff_date = now + timedelta(days=deadline_days)

        def has_upcoming_deadline(tender):
            deadline_str = tender.get('submission_deadline')
            if not deadline_str:
                return False
            try:
                deadline = datetime.fromisoformat(deadline_str.replace('Z', '+00:00'))
                return deadline <= cutoff_date
            except:
                return False

        filtered = [t for t in filtered if has_upcoming_deadline(t)]

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
    elif sort_by == 'deadline_asc':
        # Сортировка по дедлайну: тендеры с дедлайном первыми, по возрастанию
        def deadline_key(tender):
            deadline_str = tender.get('submission_deadline')
            if not deadline_str:
                return ('z', '')  # Тендеры без дедлайна в конец
            return ('a', deadline_str)
        filtered.sort(key=deadline_key)

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

@router.callback_query(F.data == "alltenders_last_24h")
async def show_tenders_last_24h(callback: CallbackQuery, state: FSMContext):
    """Показать тендеры за последние 24 часа (из дайджеста)."""
    await callback.answer()

    try:
        await callback.message.edit_text("⏳ Загрузка тендеров за сутки...")

        # Получаем все тендеры БЕЗ фильтрации по дедлайну
        # (дайджест считает все уведомления, не фильтруя по дедлайну)
        all_tenders = await get_all_user_tenders(callback.from_user.id, filter_expired=False)

        # Фильтруем за последние 24 часа
        # Используем naive UTC — БД хранит даты без timezone
        cutoff = datetime.utcnow() - timedelta(hours=24)

        filtered_tenders = []
        for tender in all_tenders:
            sent_at = tender.get('sent_at')
            if sent_at:
                try:
                    if isinstance(sent_at, str):
                        # Убираем timezone info для naive сравнения
                        clean = sent_at.replace('Z', '').replace('+00:00', '')
                        tender_date = datetime.fromisoformat(clean)
                    else:
                        tender_date = sent_at
                        if tender_date.tzinfo is not None:
                            tender_date = tender_date.replace(tzinfo=None)

                    if tender_date >= cutoff:
                        filtered_tenders.append(tender)
                except Exception as e:
                    logger.debug(f"Ошибка парсинга даты '{sent_at}': {e}")

        if not filtered_tenders:
            await callback.message.edit_text(
                "📊 <b>Тендеры за последние 24 часа</b>\n\n"
                "За это время новых тендеров не найдено.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📊 Все тендеры", callback_data="sniper_all_tenders")],
                    [InlineKeyboardButton(text="« Назад", callback_data="sniper_menu")]
                ])
            )
            return

        # Сохраняем и показываем
        await state.update_data(all_tenders=filtered_tenders, filter_params={'sort_by': 'date_desc'})
        await state.set_state(AllTendersStates.viewing_list)

        # Показываем меню с указанием периода
        await show_tenders_menu(callback.message, filtered_tenders, {'period': '24h'}, state)

    except Exception as e:
        logger.error(f"Ошибка показа тендеров за 24ч: {e}", exc_info=True)
        await callback.message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


@router.callback_query(F.data == "sniper_all_tenders")
async def show_all_tenders(callback: CallbackQuery, state: FSMContext):
    """Показать все тендеры пользователя."""
    await callback.answer()

    try:
        # Показываем промежуточное сообщение
        await callback.message.edit_text("⏳ Загрузка ваших тендеров...")

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
        await callback.message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


async def show_tenders_menu(message: Message, tenders: List[Dict], filter_params: Dict, state: FSMContext, page: int = 0):
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

    # Группировка по фильтрам
    grouped_tenders = group_tenders_by_filter(filtered_tenders)

    # Пагинация: 2 группы на странице
    groups_per_page = 2
    total_pages = (len(grouped_tenders) + groups_per_page - 1) // groups_per_page
    page = max(0, min(page, total_pages - 1))  # Ограничиваем диапазон страниц

    # Получаем группы для текущей страницы
    groups_items = list(grouped_tenders.items())
    start_idx = page * groups_per_page
    end_idx = start_idx + groups_per_page
    page_groups = groups_items[start_idx:end_idx]

    # Сохраняем текущую страницу в state
    await state.update_data(current_page=page)

    text = (
        f"📊 <b>Все мои тендеры</b>\n\n"
        f"<b>Всего:</b> {total_count} тендеров\n"
        f"🤖 Автомониторинг: {automonitoring_count}\n"
        f"🔍 Мгновенный поиск: {instant_search_count}\n"
        f"🎨 Фильтров: {len(grouped_tenders)}\n\n"
    )

    if total_pages > 1:
        text += f"📄 Страница {page + 1} из {total_pages}\n\n"

    # Показываем тендеры по группам
    text += "<b>Тендеры по фильтрам:</b>\n\n"

    shown_tenders = 0

    for filter_name, group_tenders in page_groups:
        # Определяем иконку источника
        if any(t.get('source') == 'instant_search' for t in group_tenders):
            icon = "🔍"
        else:
            icon = "🎨"

        text += f"{icon} <b>{filter_name}</b> ({len(group_tenders)} тендеров)\n"

        for tender in group_tenders[:3]:  # Показываем до 3 тендеров в группе
            shown_tenders += 1
            price = f"{tender.get('price'):,.0f} ₽" if tender.get('price') else "Не указана"
            # Генерируем короткое AI-название
            short_name = generate_tender_name(
                tender.get('name', 'Без названия'),
                tender_data=tender,
                max_length=50
            )
            text += f"   • <b>{short_name}</b>\n"
            text += f"      💰 {price} | ⭐ {tender.get('score', 0)}%\n"

        if len(group_tenders) > 3:
            text += f"      <i>... и еще {len(group_tenders) - 3} тендеров</i>\n"

        text += "\n"

    text += "💡 Скачайте отчёт (Excel/HTML) для просмотра всех тендеров"

    # Кнопки навигации и фильтрации
    keyboard_rows = [
        [InlineKeyboardButton(text="📥 Скачать отчёт", callback_data="alltenders_download_menu")],
        [
            InlineKeyboardButton(text="📅 Сортировка", callback_data="alltenders_sort"),
            InlineKeyboardButton(text="💰 Цена", callback_data="alltenders_filter_price")
        ],
        [InlineKeyboardButton(text="🔄 Сбросить фильтры", callback_data="alltenders_reset_filters")],
        [InlineKeyboardButton(text="🗑️ Очистить историю", callback_data="alltenders_clear_history")]
    ]

    # Добавляем кнопки пагинации если страниц больше 1
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"alltenders_page_{page - 1}"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"alltenders_page_{page + 1}"))

        if nav_buttons:
            keyboard_rows.append(nav_buttons)

    keyboard_rows.append([InlineKeyboardButton(text="« Назад в Sniper", callback_data="sniper_menu")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    try:
        await message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except:
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "alltenders_download_menu")
async def show_download_menu(callback: CallbackQuery, state: FSMContext):
    """Показать меню выбора формата для скачивания."""
    # Проверяем доступ к экспорту (Basic+)
    if not await require_feature(callback, 'excel_export'):
        return

    try:
        await callback.answer()

        data = await state.get_data()
        tenders = data.get('all_tenders', [])

        # Если тендеров нет в state - перенаправляем на загрузку
        if not tenders:
            logger.info(f"[DOWNLOAD] State пустой для user {callback.from_user.id}, перенаправляем")
            await callback.message.edit_text(
                "⚠️ <b>Данные устарели</b>\n\n"
                "Нажмите кнопку ниже, чтобы обновить список тендеров.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Обновить список", callback_data="sniper_all_tenders")],
                    [InlineKeyboardButton(text="« Меню", callback_data="sniper_menu")]
                ])
            )
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"📊 Excel (.xlsx) - {len(tenders)} тендеров", callback_data="alltenders_download_excel")],
            [InlineKeyboardButton(text=f"🌐 HTML отчёт - {len(tenders)} тендеров", callback_data="alltenders_download_html_menu")],
            [InlineKeyboardButton(text="« Назад", callback_data="alltenders_back")]
        ])

        await callback.message.edit_text(
            "📥 <b>Скачать отчёт</b>\n\n"
            "Выберите формат:\n\n"
            "📊 <b>Excel</b> - таблица с тендерами для работы\n"
            "🌐 <b>HTML</b> - интерактивный отчёт с фильтрацией\n\n"
            f"📋 <b>Всего тендеров:</b> {len(tenders)}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в show_download_menu: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "alltenders_download_excel")
async def download_excel(callback: CallbackQuery, state: FSMContext):
    """Скачать Excel файл с тендерами."""
    await callback.answer("Генерирую Excel файл...")

    try:
        data = await state.get_data()
        tenders = data.get('all_tenders', [])
        filter_params = data.get('filter_params', {})

        # Если тендеров нет в state - перенаправляем
        if not tenders:
            await callback.message.answer(
                "⚠️ Данные устарели. Нажмите «Все тендеры» для обновления.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📊 Все тендеры", callback_data="sniper_all_tenders")]
                ])
            )
            return

        # Применяем фильтры
        filtered_tenders = filter_tenders(
            tenders,
            sort_by=filter_params.get('sort_by', 'date_desc'),
            price_min=filter_params.get('price_min'),
            price_max=filter_params.get('price_max'),
            region=filter_params.get('region')
        )

        if not filtered_tenders:
            await callback.message.answer("❌ Нет тендеров для экспорта")
            return

        # Генерируем Excel
        excel_path = await generate_tenders_excel_async(
            tenders=filtered_tenders,
            user_id=callback.from_user.id,
            title="Все тендеры"
        )

        # Отправляем файл
        await callback.message.answer_document(
            document=FSInputFile(excel_path),
            caption=f"📊 <b>Экспорт тендеров в Excel</b>\n\n"
                    f"📋 Тендеров: {len(filtered_tenders)}\n"
                    f"💡 Файл содержит кликабельные ссылки на тендеры",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка генерации Excel: {e}", exc_info=True)
        await callback.message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


@router.callback_query(F.data == "alltenders_download_html_menu")
async def show_html_download_menu(callback: CallbackQuery, state: FSMContext):
    """Показать меню выбора типа HTML отчёта."""
    try:
        await callback.answer()

        data = await state.get_data()
        tenders = data.get('all_tenders', [])

        # Если тендеров нет в state - перенаправляем
        if not tenders:
            await callback.message.edit_text(
                "⚠️ <b>Данные устарели</b>\n\nНажмите кнопку ниже для обновления.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Обновить список", callback_data="sniper_all_tenders")]
                ])
            )
            return

        # Получаем список уникальных фильтров
        filter_names = set(t.get('filter_name') or 'Без фильтра' for t in tenders)
        filters_count = len(filter_names)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"📊 Все тендеры ({len(tenders)} шт.)", callback_data="alltenders_download_all")],
            [InlineKeyboardButton(text=f"🎨 По фильтру ({filters_count} фильтров)", callback_data="alltenders_download_by_filter")],
            [InlineKeyboardButton(text="📅 За период", callback_data="alltenders_download_by_period")],
            [InlineKeyboardButton(text="« Назад", callback_data="alltenders_download_menu")]
        ])

        await callback.message.edit_text(
            "🌐 <b>Скачать HTML отчёт</b>\n\n"
            "Выберите какие тендеры включить в отчёт:\n\n"
            f"📊 <b>Всего тендеров:</b> {len(tenders)}\n"
            f"🎨 <b>Фильтров:</b> {filters_count}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в show_html_download_menu: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "alltenders_download_all")
async def download_all_tenders_html(callback: CallbackQuery, state: FSMContext):
    """Скачать HTML отчет всех тендеров."""
    await callback.answer("Генерирую HTML отчет...")

    try:
        data = await state.get_data()
        tenders = data.get('all_tenders', [])
        filter_params = data.get('filter_params', {})

        # Если тендеров нет в state - перенаправляем
        if not tenders:
            await callback.message.answer(
                "⚠️ Данные устарели. Нажмите «Все тендеры» для обновления.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📊 Все тендеры", callback_data="sniper_all_tenders")]
                ])
            )
            return

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
        await callback.message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


@router.callback_query(F.data == "alltenders_download_by_filter")
async def show_filter_selection(callback: CallbackQuery, state: FSMContext):
    """Показать список фильтров для выбора."""
    try:
        await callback.answer()

        data = await state.get_data()
        tenders = data.get('all_tenders', [])

        # Если тендеров нет в state - перенаправляем
        if not tenders:
            await callback.message.edit_text(
                "⚠️ <b>Данные устарели</b>\n\nНажмите кнопку ниже для обновления.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Обновить список", callback_data="sniper_all_tenders")]
                ])
            )
            return

        # Группируем тендеры по фильтрам и считаем количество
        filter_counts = {}
        for tender in tenders:
            filter_name = tender.get('filter_name') or 'Без фильтра'
            filter_counts[filter_name] = filter_counts.get(filter_name, 0) + 1

        # Создаём кнопки для каждого фильтра
        keyboard_rows = []
        for filter_name, count in sorted(filter_counts.items(), key=lambda x: -x[1]):
            # Обрезаем длинные названия
            display_name = filter_name[:25] + "..." if len(filter_name) > 25 else filter_name
            callback_data = f"alltenders_dl_filter:{filter_name[:50]}"
            keyboard_rows.append([
                InlineKeyboardButton(
                    text=f"🎨 {display_name} ({count})",
                    callback_data=callback_data
                )
            ])

        keyboard_rows.append([InlineKeyboardButton(text="« Назад", callback_data="alltenders_download_menu")])

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

        await callback.message.edit_text(
            "🎨 <b>Выберите фильтр для отчёта</b>\n\n"
            "Нажмите на фильтр, чтобы скачать отчёт только по нему:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в show_filter_selection: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("alltenders_dl_filter:"))
async def download_by_filter(callback: CallbackQuery, state: FSMContext):
    """Скачать HTML отчет по выбранному фильтру."""
    await callback.answer("Генерирую HTML отчет...")

    try:
        # Получаем название фильтра
        selected_filter = callback.data.replace("alltenders_dl_filter:", "")

        data = await state.get_data()
        tenders = data.get('all_tenders', [])

        # Если тендеров нет в state - перенаправляем
        if not tenders:
            await callback.message.answer(
                "⚠️ Данные устарели. Нажмите «Все тендеры» для обновления.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📊 Все тендеры", callback_data="sniper_all_tenders")]
                ])
            )
            return

        # Фильтруем тендеры по выбранному фильтру
        if selected_filter == "Без фильтра":
            filtered_tenders = [t for t in tenders if not t.get('filter_name')]
        else:
            filtered_tenders = [t for t in tenders if (t.get('filter_name') or '').startswith(selected_filter)]

        if not filtered_tenders:
            await callback.message.answer("❌ Нет тендеров по выбранному фильтру")
            return

        # Генерируем HTML
        report_path = await generate_all_tenders_html(
            filtered_tenders,
            callback.from_user.id,
            {'filter': selected_filter}
        )

        # Отправляем файл
        await callback.message.answer_document(
            document=FSInputFile(report_path),
            caption=f"📊 <b>Тендеры по фильтру</b>\n\n"
                    f"🎨 Фильтр: {selected_filter}\n"
                    f"📋 Тендеров: {len(filtered_tenders)}",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка генерации HTML по фильтру: {e}", exc_info=True)
        await callback.message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


@router.callback_query(F.data == "alltenders_download_by_period")
async def show_period_selection(callback: CallbackQuery, state: FSMContext):
    """Показать выбор временного периода."""
    try:
        await callback.answer()

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📅 Сегодня", callback_data="alltenders_dl_period:1")],
            [InlineKeyboardButton(text="📅 Последние 3 дня", callback_data="alltenders_dl_period:3")],
            [InlineKeyboardButton(text="📅 Последняя неделя", callback_data="alltenders_dl_period:7")],
            [InlineKeyboardButton(text="📅 Последние 2 недели", callback_data="alltenders_dl_period:14")],
            [InlineKeyboardButton(text="📅 Последний месяц", callback_data="alltenders_dl_period:30")],
            [InlineKeyboardButton(text="📅 Последние 3 месяца", callback_data="alltenders_dl_period:90")],
            [InlineKeyboardButton(text="« Назад", callback_data="alltenders_download_menu")]
        ])

        await callback.message.edit_text(
            "📅 <b>Выберите период для отчёта</b>\n\n"
            "За какой период включить тендеры в отчёт?",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в show_period_selection: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("alltenders_dl_period:"))
async def download_by_period(callback: CallbackQuery, state: FSMContext):
    """Скачать HTML отчет за выбранный период."""
    await callback.answer("Генерирую HTML отчет...")

    try:
        # Получаем количество дней
        days = int(callback.data.replace("alltenders_dl_period:", ""))

        # ВАЖНО: Загружаем данные напрямую из БД, а не из state
        # Это гарантирует актуальные данные даже если пользователь
        # пришёл напрямую по ссылке из дайджеста
        tenders = await get_all_user_tenders(callback.from_user.id, filter_expired=True)

        if not tenders:
            await callback.message.answer("❌ У вас пока нет найденных тендеров")
            return

        # Вычисляем дату отсечки (naive UTC для совместимости с БД)
        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Фильтруем тендеры по дате
        filtered_tenders = []
        for tender in tenders:
            sent_at = tender.get('sent_at')
            if sent_at:
                try:
                    if isinstance(sent_at, str):
                        # Парсим строку даты, убираем timezone info для naive сравнения
                        clean = sent_at.replace('Z', '').replace('+00:00', '')
                        tender_date = datetime.fromisoformat(clean)
                    else:
                        tender_date = sent_at
                        if tender_date.tzinfo is not None:
                            tender_date = tender_date.replace(tzinfo=None)

                    if tender_date >= cutoff_date:
                        filtered_tenders.append(tender)
                except Exception as e:
                    logger.debug(f"Ошибка парсинга даты '{sent_at}': {e}")
            # Если нет даты - не включаем (это старые данные)

        if not filtered_tenders:
            await callback.message.answer(f"❌ Нет тендеров за последние {days} дней")
            return

        # Название периода для отчёта
        period_names = {
            1: "сегодня",
            3: "последние 3 дня",
            7: "последнюю неделю",
            14: "последние 2 недели",
            30: "последний месяц",
            90: "последние 3 месяца"
        }
        period_name = period_names.get(days, f"последние {days} дней")

        # Генерируем HTML
        report_path = await generate_all_tenders_html(
            filtered_tenders,
            callback.from_user.id,
            {'period_days': days}
        )

        # Отправляем файл
        await callback.message.answer_document(
            document=FSInputFile(report_path),
            caption=f"📊 <b>Тендеры за {period_name}</b>\n\n"
                    f"📋 Тендеров: {len(filtered_tenders)}",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка генерации HTML за период: {e}", exc_info=True)
        await callback.message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


@router.callback_query(F.data == "alltenders_sort")
async def show_sort_menu(callback: CallbackQuery, state: FSMContext):
    """Показать меню сортировки."""
    try:
        await callback.answer()

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📅 Новые первые", callback_data="alltenders_sort_date_desc")],
            [InlineKeyboardButton(text="📅 Старые первые", callback_data="alltenders_sort_date_asc")],
            [InlineKeyboardButton(text="💰 Цена ↓ (дорогие первые)", callback_data="alltenders_sort_price_desc")],
            [InlineKeyboardButton(text="💰 Цена ↑ (дешевые первые)", callback_data="alltenders_sort_price_asc")],
            [InlineKeyboardButton(text="⭐ Релевантность", callback_data="alltenders_sort_score_desc")],
            [InlineKeyboardButton(text="⏰ По дедлайну (скоро истекают)", callback_data="alltenders_sort_deadline_asc")],
            [InlineKeyboardButton(text="« Назад", callback_data="alltenders_back")]
        ])

        await callback.message.edit_text(
            "📊 <b>Сортировка тендеров</b>\n\nВыберите тип сортировки:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в show_sort_menu: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("alltenders_sort_"))
async def apply_sort(callback: CallbackQuery, state: FSMContext):
    """Применить сортировку."""
    try:
        await callback.answer()

        sort_type = callback.data.replace("alltenders_sort_", "")

        data = await state.get_data()
        filter_params = data.get('filter_params', {})
        filter_params['sort_by'] = sort_type

        await state.update_data(filter_params=filter_params)

        # Обновляем меню (сбрасываем на первую страницу при изменении сортировки)
        tenders = data.get('all_tenders', [])
        await show_tenders_menu(callback.message, tenders, filter_params, state, page=0)
    except Exception as e:
        logger.error(f"Ошибка в apply_sort: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "alltenders_reset_filters")
async def reset_filters(callback: CallbackQuery, state: FSMContext):
    """Сбросить все фильтры."""
    try:
        await callback.answer("Фильтры сброшены")

        data = await state.get_data()
        tenders = data.get('all_tenders', [])

        await state.update_data(filter_params={'sort_by': 'date_desc'})

        await show_tenders_menu(callback.message, tenders, {'sort_by': 'date_desc'}, state, page=0)
    except Exception as e:
        logger.error(f"Ошибка в reset_filters: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "alltenders_filter_price")
async def show_price_filter_menu(callback: CallbackQuery, state: FSMContext):
    """Показать меню фильтрации по цене."""
    try:
        await callback.answer()

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="До 500 000 ₽", callback_data="alltenders_price_0_500000")],
            [InlineKeyboardButton(text="500 000 - 1 млн ₽", callback_data="alltenders_price_500000_1000000")],
            [InlineKeyboardButton(text="1 - 5 млн ₽", callback_data="alltenders_price_1000000_5000000")],
            [InlineKeyboardButton(text="5 - 10 млн ₽", callback_data="alltenders_price_5000000_10000000")],
            [InlineKeyboardButton(text="Более 10 млн ₽", callback_data="alltenders_price_10000000_0")],
            [InlineKeyboardButton(text="🔄 Без фильтра по цене", callback_data="alltenders_price_reset")],
            [InlineKeyboardButton(text="« Назад", callback_data="alltenders_back")]
        ])

        await callback.message.edit_text(
            "💰 <b>Фильтр по цене</b>\n\n"
            "Выберите ценовой диапазон для отображения тендеров:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в show_price_filter_menu: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("alltenders_price_"))
async def apply_price_filter(callback: CallbackQuery, state: FSMContext):
    """Применить фильтр по цене."""
    try:
        await callback.answer("Применяю фильтр...")

        price_data = callback.data.replace("alltenders_price_", "")

        data = await state.get_data()
        filter_params = data.get('filter_params', {})
        tenders = data.get('all_tenders', [])

        if price_data == "reset":
            # Убираем фильтр по цене
            filter_params.pop('price_min', None)
            filter_params.pop('price_max', None)
        else:
            # Парсим диапазон
            parts = price_data.split("_")
            price_min = int(parts[0])
            price_max = int(parts[1]) if parts[1] != "0" else None

            filter_params['price_min'] = price_min if price_min > 0 else None
            filter_params['price_max'] = price_max

        await state.update_data(filter_params=filter_params)
        await show_tenders_menu(callback.message, tenders, filter_params, state, page=0)
    except Exception as e:
        logger.error(f"Ошибка в apply_price_filter: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "alltenders_back")
async def back_to_tenders(callback: CallbackQuery, state: FSMContext):
    """Вернуться к списку тендеров."""
    try:
        await callback.answer()

        data = await state.get_data()
        tenders = data.get('all_tenders', [])
        filter_params = data.get('filter_params', {})
        page = data.get('current_page', 0)

        await show_tenders_menu(callback.message, tenders, filter_params, state, page)
    except Exception as e:
        logger.error(f"Ошибка в back_to_tenders: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("alltenders_page_"))
async def navigate_page(callback: CallbackQuery, state: FSMContext):
    """Навигация по страницам тендеров."""
    try:
        # Показываем уведомление пользователю что запрос обрабатывается
        await callback.answer("⏳ Загрузка страницы...", show_alert=False)

        # Получаем номер страницы из callback_data
        page = int(callback.data.replace("alltenders_page_", ""))

        data = await state.get_data()
        tenders = data.get('all_tenders', [])
        filter_params = data.get('filter_params', {})

        await show_tenders_menu(callback.message, tenders, filter_params, state, page)
    except Exception as e:
        logger.error(f"Ошибка в navigate_page: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "alltenders_clear_history")
async def show_clear_history_menu(callback: CallbackQuery, state: FSMContext):
    """Показать меню очистки истории."""
    try:
        await callback.answer()

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Очистить все", callback_data="alltenders_clear_all")],
            [InlineKeyboardButton(text="📅 Старше 30 дней", callback_data="alltenders_clear_30")],
            [InlineKeyboardButton(text="📅 Старше 60 дней", callback_data="alltenders_clear_60")],
            [InlineKeyboardButton(text="📅 Старше 90 дней", callback_data="alltenders_clear_90")],
            [InlineKeyboardButton(text="« Назад", callback_data="alltenders_back")]
        ])

        await callback.message.edit_text(
            "🗑️ <b>Очистка истории тендеров</b>\n\n"
            "Выберите период для удаления:\n\n"
            "⚠️ <b>Внимание:</b> это действие необратимо!\n"
            "После удаления тендеры нельзя будет восстановить.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка в show_clear_history_menu: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "alltenders_clear_all")
async def clear_all_history(callback: CallbackQuery, state: FSMContext):
    """Очистить всю историю тендеров."""
    await callback.answer()

    try:
        db = await get_sniper_db()
        deleted_count = await db.clear_all_notifications(callback.from_user.id)

        await callback.message.answer(
            f"✅ <b>История очищена</b>\n\n"
            f"Удалено {deleted_count} тендеров из истории.",
            parse_mode="HTML"
        )

        # Возвращаемся в главное меню Sniper
        from bot.handlers.sniper import show_sniper_menu
        await show_sniper_menu(callback)

    except Exception as e:
        logger.error(f"Ошибка при очистке истории: {e}", exc_info=True)
        await callback.message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


@router.callback_query(F.data.startswith("alltenders_clear_"))
async def clear_old_history(callback: CallbackQuery, state: FSMContext):
    """Очистить старые тендеры из истории."""
    await callback.answer()

    # Получаем количество дней из callback_data
    days_str = callback.data.replace("alltenders_clear_", "")

    # Пропускаем "all", т.к. это обрабатывается отдельно
    if days_str == "all":
        return

    try:
        days = int(days_str)
    except ValueError:
        await callback.message.answer("❌ Неверный период")
        return

    try:
        db = await get_sniper_db()
        deleted_count = await db.clear_old_notifications(callback.from_user.id, days=days)

        await callback.message.answer(
            f"✅ <b>История очищена</b>\n\n"
            f"Удалено {deleted_count} тендеров старше {days} дней.",
            parse_mode="HTML"
        )

        # Возвращаемся в главное меню Sniper
        from bot.handlers.sniper import show_sniper_menu
        await show_sniper_menu(callback)

    except Exception as e:
        logger.error(f"Ошибка при очистке истории: {e}", exc_info=True)
        await callback.message.answer(BETA_ERROR_MESSAGE, parse_mode="HTML")


__all__ = ['router']
