"""
Обработчики для работы с историей поисков.
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime
import asyncio

from bot.database import get_database
from bot.states import SearchStates, HistoryStates
from bot.keyboards import get_main_menu_keyboard, get_tenders_list_keyboard

router = Router()


def format_datetime(iso_string: str) -> str:
    """Форматирование даты и времени для отображения."""
    try:
        dt = datetime.fromisoformat(iso_string)
        return dt.strftime("%d.%m.%Y %H:%M")
    except:
        return iso_string


def get_history_keyboard(searches: list) -> InlineKeyboardMarkup:
    """
    Создание клавиатуры со списком истории поисков.

    Args:
        searches: Список поисков из БД

    Returns:
        Inline клавиатура
    """
    builder = InlineKeyboardBuilder()

    for search in searches:
        search_id = search['id']
        query = search['query']
        timestamp = format_datetime(search['timestamp'])
        result_count = search['result_count']

        # Обрезаем запрос, если слишком длинный
        if len(query) > 30:
            query = query[:27] + "..."

        button_text = f"🔍 {query} | {result_count} тендеров | {timestamp}"
        builder.row(InlineKeyboardButton(
            text=button_text,
            callback_data=f"history_{search_id}"
        ))

    # Кнопка возврата в главное меню
    builder.row(InlineKeyboardButton(
        text="🏠 Главное меню",
        callback_data="main_menu"
    ))

    return builder.as_markup()


def get_history_details_keyboard(search_id: int, has_html_report: bool = False) -> InlineKeyboardMarkup:
    """
    Клавиатура для деталей истории поиска.

    Args:
        search_id: ID поиска
        has_html_report: Есть ли HTML отчет для этого поиска

    Returns:
        Inline клавиатура
    """
    builder = InlineKeyboardBuilder()

    builder.row(InlineKeyboardButton(
        text="🔄 Повторить поиск",
        callback_data=f"repeat_{search_id}"
    ))

    builder.row(InlineKeyboardButton(
        text="📋 Посмотреть результаты",
        callback_data=f"view_results_{search_id}"
    ))

    # Добавляем кнопку HTML отчета, если он существует
    if has_html_report:
        builder.row(InlineKeyboardButton(
            text="📄 Открыть HTML отчет",
            callback_data=f"open_html_{search_id}"
        ))

    builder.row(InlineKeyboardButton(
        text="« Назад к истории",
        callback_data="back_to_history"
    ))

    return builder.as_markup()


@router.message(F.text == "📂 Мои поиски")
async def show_search_history(message: Message, state: FSMContext):
    """
    Показать историю поисков пользователя.
    """
    await state.clear()

    try:
        db = await get_database()
        searches = await db.get_user_searches(
            user_id=message.from_user.id,
            limit=10
        )

        if not searches:
            await message.answer(
                "📂 <b>История поисков пуста</b>\n\n"
                "У вас пока нет сохраненных поисков.\n"
                "Используйте <b>🔍 Новый поиск</b>, чтобы начать!",
                reply_markup=get_main_menu_keyboard(),
                parse_mode="HTML"
            )
            return

        # Получаем статистику
        stats = await db.get_user_stats(message.from_user.id)

        stats_text = (
            "📊 <b>ИСТОРИЯ ПОИСКОВ</b>\n\n"
            f"📈 Всего поисков: <b>{stats['total_searches']}</b>\n"
            f"📋 Найдено тендеров: <b>{stats['total_tenders_found']}</b>\n\n"
            "<i>💡 Выберите поиск для просмотра деталей:</i>"
        )

        await message.answer(
            stats_text,
            reply_markup=get_history_keyboard(searches),
            parse_mode="HTML"
        )

        await state.set_state(HistoryStates.viewing_history)

    except Exception as e:
        await message.answer(
            f"❌ <b>Ошибка загрузки истории:</b>\n\n"
            f"<code>{str(e)}</code>",
            reply_markup=get_main_menu_keyboard(),
            parse_mode="HTML"
        )


@router.callback_query(F.data == "back_to_history")
async def back_to_history(callback: CallbackQuery, state: FSMContext):
    """Возврат к списку истории."""
    await callback.answer()

    try:
        db = await get_database()
        searches = await db.get_user_searches(
            user_id=callback.from_user.id,
            limit=10
        )

        stats = await db.get_user_stats(callback.from_user.id)

        stats_text = (
            "📊 <b>ИСТОРИЯ ПОИСКОВ</b>\n\n"
            f"📈 Всего поисков: <b>{stats['total_searches']}</b>\n"
            f"📋 Найдено тендеров: <b>{stats['total_tenders_found']}</b>\n\n"
            "<i>💡 Выберите поиск для просмотра деталей:</i>"
        )

        await callback.message.edit_text(
            stats_text,
            reply_markup=get_history_keyboard(searches),
            parse_mode="HTML"
        )

        await state.set_state(HistoryStates.viewing_history)

    except Exception as e:
        await callback.message.answer(
            f"❌ <b>Ошибка загрузки истории:</b>\n\n"
            f"<code>{str(e)}</code>",
            parse_mode="HTML"
        )


@router.callback_query(HistoryStates.viewing_history, F.data.startswith("history_"))
async def show_history_details(callback: CallbackQuery, state: FSMContext):
    """
    Показать детали конкретного поиска из истории.
    """
    await callback.answer()

    search_id = int(callback.data.replace("history_", ""))

    try:
        db = await get_database()
        search = await db.get_search_by_id(search_id)

        if not search:
            await callback.message.edit_text(
                "❌ Поиск не найден",
                parse_mode="HTML"
            )
            return

        # Проверяем наличие HTML отчета
        has_html_report = False
        if search.get('search_data'):
            report_path = search['search_data'].get('report_path')
            if report_path:
                from pathlib import Path
                has_html_report = Path(report_path).exists()

        # Форматируем информацию о поиске
        details_text = (
            "🔍 <b>ДЕТАЛИ ПОИСКА</b>\n\n"
            f"<b>Запрос:</b> {search['query']}\n"
            f"<b>Цена:</b> {search['price_min']:,} - {search['price_max']:,} ₽\n"
            f"<b>Найдено:</b> {search['result_count']} тендеров\n"
            f"<b>Дата:</b> {format_datetime(search['timestamp'])}\n\n"
        )

        if has_html_report:
            details_text += "📄 <b>HTML отчет:</b> Доступен\n\n"

        details_text += "<i>Выберите действие:</i>"

        await callback.message.edit_text(
            details_text,
            reply_markup=get_history_details_keyboard(search_id, has_html_report),
            parse_mode="HTML"
        )

        # Сохраняем search_id в состоянии
        await state.update_data(viewing_search_id=search_id)

    except Exception as e:
        await callback.message.answer(
            f"❌ <b>Ошибка загрузки деталей:</b>\n\n"
            f"<code>{str(e)}</code>",
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("repeat_"))
async def repeat_search(callback: CallbackQuery, state: FSMContext):
    """
    Повторить поиск из истории.
    """
    await callback.answer("🔄 Повторяю поиск...")

    search_id = int(callback.data.replace("repeat_", ""))

    try:
        db = await get_database()
        search = await db.get_search_by_id(search_id)

        if not search:
            await callback.message.edit_text("❌ Поиск не найден")
            return

        # Запускаем новый поиск с теми же параметрами
        from bot.handlers.search import get_tender_system

        await callback.message.edit_text(
            f"🔄 <b>Повторяю поиск...</b>\n\n"
            f"🔍 Запрос: <b>{search['query']}</b>\n"
            f"💰 Цена: <b>{search['price_min']:,} - {search['price_max']:,} ₽</b>\n\n"
            f"<i>Это может занять некоторое время...</i>",
            parse_mode="HTML"
        )

        # Выполняем поиск
        system = get_tender_system()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: system.search_and_analyze(
                search_query=search['query'],
                price_min=search['price_min'],
                price_max=search['price_max'],
                max_tenders=search['tender_count'],
                analyze_documents=False,
                download_documents=False
            )
        )

        # Сохраняем новые результаты
        tenders_found = result.get('tenders_found', 0)

        if tenders_found == 0:
            await callback.message.edit_text(
                "😔 <b>Тендеры не найдены</b>\n\n"
                "К сожалению, по этим параметрам сейчас ничего не найдено.",
                parse_mode="HTML"
            )
            return

        # Сохраняем новый поиск в БД
        new_search_id = await db.save_search(
            user_id=callback.from_user.id,
            query=search['query'],
            price_min=search['price_min'],
            price_max=search['price_max'],
            tender_count=search['tender_count'],
            result_count=tenders_found,
            search_data=result
        )

        # Сохраняем результаты в состоянии
        await state.update_data(
            search_results=result,
            last_search_id=new_search_id
        )
        await state.set_state(SearchStates.viewing_results)

        # Показываем результаты
        results_text = f"✅ <b>Найдено тендеров: {tenders_found}</b>\n\n"

        for i, tender_data in enumerate(result['results'][:tenders_found], 1):
            tender = tender_data['tender_info']
            number = tender.get('number', 'N/A')
            name = tender.get('name', 'Без названия')
            price = tender.get('price_formatted', 'N/A')

            if len(name) > 80:
                name = name[:77] + "..."

            results_text += f"{i}. <b>№ {number}</b>\n"
            results_text += f"   {name}\n"
            results_text += f"   💰 {price}\n\n"

        results_text += "<i>💡 Выберите тендер для просмотра деталей:</i>"

        await callback.message.edit_text(
            results_text,
            reply_markup=get_tenders_list_keyboard(tenders_found),
            parse_mode="HTML"
        )

    except Exception as e:
        await callback.message.edit_text(
            f"❌ <b>Ошибка повтора поиска:</b>\n\n"
            f"<code>{str(e)}</code>",
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("open_html_"))
async def open_html_report(callback: CallbackQuery, state: FSMContext):
    """
    Открыть HTML отчет поиска.
    """
    await callback.answer()

    search_id = int(callback.data.replace("open_html_", ""))

    try:
        db = await get_database()
        search = await db.get_search_by_id(search_id)

        if not search or not search.get('search_data'):
            await callback.message.edit_text(
                "⚠️ <b>Отчет не найден</b>\n\n"
                "К сожалению, данные этого поиска не сохранены.",
                parse_mode="HTML"
            )
            return

        # Получаем путь к HTML отчету
        report_path = search['search_data'].get('report_path')

        if not report_path:
            await callback.answer(
                "⚠️ HTML отчет не был создан для этого поиска",
                show_alert=True
            )
            return

        # Проверяем существование файла
        from pathlib import Path
        html_file = Path(report_path)

        if not html_file.exists():
            await callback.answer(
                "⚠️ HTML файл не найден. Возможно, он был удален.",
                show_alert=True
            )
            return

        # Открываем в браузере
        import webbrowser
        import os
        webbrowser.open(f'file://{os.path.abspath(report_path)}')

        await callback.answer("✅ HTML отчет открыт в браузере!")

        # Обновляем сообщение
        await callback.message.edit_text(
            f"📄 <b>HTML отчет открыт!</b>\n\n"
            f"Отчет для поиска: <b>{search['query']}</b>\n"
            f"Дата: {format_datetime(search['timestamp'])}\n\n"
            f"<i>💡 Проверьте браузер</i>",
            parse_mode="HTML"
        )

    except Exception as e:
        await callback.message.answer(
            f"❌ <b>Ошибка открытия отчета:</b>\n\n"
            f"<code>{str(e)}</code>",
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("view_results_"))
async def view_saved_results(callback: CallbackQuery, state: FSMContext):
    """
    Просмотр сохраненных результатов поиска.
    """
    await callback.answer()

    search_id = int(callback.data.replace("view_results_", ""))

    try:
        db = await get_database()
        search = await db.get_search_by_id(search_id)

        if not search or not search.get('search_data'):
            await callback.message.edit_text(
                "⚠️ <b>Результаты не найдены</b>\n\n"
                "К сожалению, полные данные этого поиска не сохранены.",
                parse_mode="HTML"
            )
            return

        result = search['search_data']
        tenders_found = result.get('tenders_found', 0)

        if tenders_found == 0:
            await callback.message.edit_text(
                "📂 В этом поиске не было найдено тендеров.",
                parse_mode="HTML"
            )
            return

        # Сохраняем результаты в состоянии
        await state.update_data(search_results=result)
        await state.set_state(SearchStates.viewing_results)

        # Показываем результаты
        results_text = f"📂 <b>Сохраненные результаты ({tenders_found}):</b>\n\n"

        for i, tender_data in enumerate(result['results'][:tenders_found], 1):
            tender = tender_data['tender_info']
            number = tender.get('number', 'N/A')
            name = tender.get('name', 'Без названия')
            price = tender.get('price_formatted', 'N/A')

            if len(name) > 80:
                name = name[:77] + "..."

            results_text += f"{i}. <b>№ {number}</b>\n"
            results_text += f"   {name}\n"
            results_text += f"   💰 {price}\n\n"

        results_text += "<i>💡 Выберите тендер для просмотра деталей:</i>"

        await callback.message.edit_text(
            results_text,
            reply_markup=get_tenders_list_keyboard(tenders_found),
            parse_mode="HTML"
        )

    except Exception as e:
        await callback.message.edit_text(
            f"❌ <b>Ошибка загрузки результатов:</b>\n\n"
            f"<code>{str(e)}</code>",
            parse_mode="HTML"
        )
