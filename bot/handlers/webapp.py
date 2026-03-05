"""
Обработчики экспорта тендеров в Google Sheets + AI анализ.

- Кнопка "📊 В таблицу" на каждом уведомлении — экспорт 1 тендера
- Команда /export — массовый экспорт за период
- Команда /export_selected — массовый экспорт по номерам из HTML-отчёта
- Команда /analyze — самостоятельный AI-анализ тендера
"""

import re
import logging
import asyncio
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from tender_sniper.database import get_sniper_db
from bot.utils import safe_callback_data

logger = logging.getLogger(__name__)

router = Router(name="sheets_export")


# ============================================
# FSM для /analyze
# ============================================

class AnalyzeStates(StatesGroup):
    waiting_for_tender = State()


# ============================================
# ОБЩИЙ HELPER для экспорта уведомлений
# ============================================

async def _export_notifications(
    notifications: list,
    gs_config: dict,
    subscription_tier: str,
    status_msg,
    db,
) -> tuple[int, int, int]:
    """
    Экспортирует список уведомлений в Google Sheets.

    Returns:
        (exported, failed, not_found) counts
    """
    from tender_sniper.google_sheets_sync import (
        get_sheets_sync, AI_COLUMNS, enrich_tender_with_ai, get_weekly_sheet_name,
        format_ai_for_sheets,
    )

    sheets_sync = get_sheets_sync()
    if not sheets_sync:
        raise RuntimeError("Google Sheets сервис недоступен")

    total = len(notifications)
    exported = 0
    failed = 0

    user_columns = set(gs_config.get('columns', []))
    has_ai_columns = bool(user_columns & AI_COLUMNS)
    is_ai_eligible = subscription_tier == 'premium' or gs_config.get('has_ai_unlimited')

    for i, notif in enumerate(notifications):
        try:
            tender_data = {
                'number': notif.get('tender_number', ''),
                'name': notif.get('tender_name', ''),
                'price': notif.get('tender_price'),
                'url': notif.get('tender_url', ''),
                'region': notif.get('tender_region', ''),
                'customer_name': notif.get('tender_customer', ''),
                'published_date': notif.get('published_date', ''),
                'submission_deadline': notif.get('submission_deadline', ''),
            }

            # Сначала используем сохранённые AI-данные мониторинга (бесплатно, без запроса)
            stored_match_info = notif.get('match_info') or {}
            ai_data = format_ai_for_sheets(stored_match_info)

            # Если колонки AI включены и пользователь Premium — дополняем глубоким анализом документов
            if has_ai_columns and is_ai_eligible and gs_config.get('ai_enrichment') and not ai_data:
                try:
                    ai_data = await enrich_tender_with_ai(
                        tender_number=tender_data['number'],
                        tender_price=tender_data.get('price'),
                        customer_name=tender_data.get('customer_name', ''),
                        subscription_tier='premium'
                    )
                except Exception:
                    pass

            match_data = {
                'score': notif.get('score', 0),
                'red_flags': [],
                'filter_name': notif.get('filter_name', ''),
                'ai_data': ai_data,
            }

            await sheets_sync.append_tender(
                spreadsheet_id=gs_config['spreadsheet_id'],
                tender_data=tender_data,
                match_data=match_data,
                columns=gs_config.get('columns', []),
                sheet_name=get_weekly_sheet_name()
            )

            await db.mark_notification_exported(notif.get('id'))
            exported += 1

            # Обновляем статус каждые 5 тендеров
            if status_msg and ((i + 1) % 5 == 0 or i == total - 1):
                try:
                    ai_label = " + AI анализ" if ai_data else ""
                    await status_msg.edit_text(
                        f"⏳ Экспорт: {i + 1}/{total}{ai_label}..."
                    )
                except Exception:
                    pass

        except Exception as e:
            logger.warning(f"Export error for {notif.get('tender_number')}: {e}")
            failed += 1

    return exported, failed, 0


# ============================================
# КНОПКА "📊 В таблицу" на уведомлении
# ============================================

@router.callback_query(F.data.startswith("sheets_") & ~F.data.startswith("sheets_done_"))
async def export_single_tender(callback: CallbackQuery):
    """Экспортирует один тендер в Google Sheets по нажатию кнопки."""
    tender_number = callback.data.replace("sheets_", "")
    telegram_id = callback.from_user.id

    # НЕ отвечаем на callback сразу — ответим с результатом в конце,
    # иначе Telegram не покажет popup с ошибкой/успехом (callback можно ответить только раз)

    try:
        # Определяем контекст: группа или личный чат
        chat = callback.message.chat if callback.message else None
        is_group = chat is not None and chat.type in ('group', 'supergroup')

        db = await get_sniper_db()

        if is_group:
            # В группе: ищем конфиг через админа группы
            group_user = await db.get_user_by_telegram_id(chat.id)
            if not group_user:
                await callback.answer("Группа не зарегистрирована", show_alert=True)
                return
            admin_tg_id = group_user.get('group_admin_id')
            if not admin_tg_id:
                await callback.answer("Админ группы не найден", show_alert=True)
                return
            user = await db.get_user_by_telegram_id(admin_tg_id)
        else:
            user = await db.get_user_by_telegram_id(telegram_id)

        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        user_id = user.get('id')

        # Проверяем Google Sheets config — одна общая таблица для группы
        gs_config = await db.get_google_sheets_config(user_id)
        if not gs_config or not gs_config.get('enabled'):
            await callback.answer(
                "Google Sheets не настроен.\nИспользуйте /settings → Google Sheets",
                show_alert=True
            )
            return

        # Получаем данные тендера из уведомлений
        notification = await db.get_notification_by_tender_number(user_id, tender_number)
        if not notification:
            # Fallback: ищем без привязки к user_id (на случай если уведомление сохранилось под другим user_id)
            notification = await db.find_notification_by_tender_number(tender_number)
        if not notification:
            await callback.answer("Тендер не найден в истории", show_alert=True)
            return

        # Проверяем дубль — если уже экспортирован
        if notification.get('sheets_exported', False):
            if is_group:
                # В группе показываем кто добавил
                exported_by_id = notification.get('sheets_exported_by')
                if exported_by_id:
                    try:
                        member = await callback.bot.get_chat_member(chat.id, exported_by_id)
                        name = member.user.full_name or f"@{member.user.username}" or str(exported_by_id)
                    except Exception:
                        name = str(exported_by_id)
                    await callback.answer(f"Уже в таблице (добавил {name})", show_alert=True)
                else:
                    await callback.answer("Уже в таблице ✅", show_alert=True)
            else:
                await callback.answer("Уже в таблице ✅", show_alert=True)
            return

        # Отвечаем на callback (показываем toast)
        await callback.answer("Экспортирую в Google Sheets...")

        # Экспортируем
        from tender_sniper.google_sheets_sync import (
            get_sheets_sync, AI_COLUMNS, enrich_tender_with_ai, get_weekly_sheet_name,
            format_ai_for_sheets,
        )
        sheets_sync = get_sheets_sync()
        if not sheets_sync:
            await callback.message.answer("❌ Google Sheets сервис недоступен")
            return

        tender_data = {
            'number': notification.get('tender_number', ''),
            'name': notification.get('tender_name', ''),
            'price': notification.get('tender_price'),
            'url': notification.get('tender_url', ''),
            'region': notification.get('tender_region', ''),
            'customer_name': notification.get('tender_customer', ''),
            'published_date': notification.get('published_date', ''),
            'submission_deadline': notification.get('submission_deadline', ''),
        }

        # Сначала используем сохранённые AI-данные мониторинга
        stored_match_info = notification.get('match_info') or {}
        ai_data = format_ai_for_sheets(stored_match_info)

        # Дополняем глубоким анализом для Premium, только если нет данных мониторинга
        user_columns = set(gs_config.get('columns', []))
        has_ai_columns = bool(user_columns & AI_COLUMNS)
        subscription_tier = user.get('subscription_tier', 'trial')
        is_ai_eligible = subscription_tier == 'premium' or user.get('has_ai_unlimited')

        if has_ai_columns and is_ai_eligible and gs_config.get('ai_enrichment') and not ai_data:
            try:
                ai_data = await enrich_tender_with_ai(
                    tender_number=tender_data['number'],
                    tender_price=tender_data.get('price'),
                    customer_name=tender_data.get('customer_name', ''),
                    subscription_tier='premium'
                )
            except Exception as ai_err:
                logger.warning(f"AI enrichment error: {ai_err}")

        match_data = {
            'score': notification.get('score', 0),
            'red_flags': [],
            'filter_name': notification.get('filter_name', ''),
            'ai_data': ai_data,
        }

        success = await sheets_sync.append_tender(
            spreadsheet_id=gs_config['spreadsheet_id'],
            tender_data=tender_data,
            match_data=match_data,
            columns=gs_config.get('columns', []),
            sheet_name=get_weekly_sheet_name()
        )

        if not success:
            await callback.message.answer("❌ Ошибка записи в Google Sheets. Попробуйте позже.")
            return

        # Помечаем как экспортированный + кто экспортировал
        await db.mark_notification_exported(notification.get('id'), exported_by=telegram_id)

        # Заменяем кнопку на "✅ В таблице" — в группе это обновляет для ВСЕХ участников
        try:
            if callback.message and callback.message.reply_markup:
                new_buttons = []
                for row in callback.message.reply_markup.inline_keyboard:
                    new_row = []
                    for btn in row:
                        if btn.callback_data and btn.callback_data.startswith("sheets_") and not btn.callback_data.startswith("sheets_done_"):
                            new_row.append(InlineKeyboardButton(
                                text="✅ В таблице",
                                callback_data=safe_callback_data("sheets_done", tender_number)
                            ))
                        else:
                            new_row.append(btn)
                    new_buttons.append(new_row)
                await callback.message.edit_reply_markup(
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=new_buttons)
                )
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Export single tender error: {e}", exc_info=True)
        try:
            await callback.answer("Ошибка экспорта", show_alert=True)
        except Exception:
            pass


@router.callback_query(F.data.startswith("sheets_done_"))
async def sheets_already_exported(callback: CallbackQuery):
    """Тендер уже экспортирован."""
    await callback.answer("Уже в таблице ✅", show_alert=True)


# ============================================
# МАССОВЫЙ ЭКСПОРТ /export
# ============================================

@router.message(Command("export"))
async def cmd_export(message: Message):
    """Массовый экспорт тендеров в Google Sheets."""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Сегодня", callback_data="export_period_1"),
            InlineKeyboardButton(text="3 дня", callback_data="export_period_3"),
            InlineKeyboardButton(text="Неделя", callback_data="export_period_7"),
        ]
    ])
    await message.answer(
        "📊 <b>Экспорт тендеров в Google Sheets</b>\n\n"
        "Выберите период — все тендеры за этот период будут добавлены в вашу таблицу.\n"
        "Уже экспортированные тендеры будут пропущены.",
        reply_markup=kb,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("export_period_"))
async def export_by_period(callback: CallbackQuery):
    """Экспорт всех тендеров за выбранный период."""
    days = int(callback.data.replace("export_period_", ""))
    telegram_id = callback.from_user.id
    period_name = {1: "сегодня", 3: "3 дня", 7: "неделю"}[days]

    await callback.answer()
    status_msg = await callback.message.edit_text(
        f"⏳ Экспортирую тендеры за {period_name}..."
    )

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(telegram_id)
        if not user:
            await status_msg.edit_text("❌ Пользователь не найден")
            return

        user_id = user.get('id')

        # Проверяем Google Sheets
        gs_config = await db.get_google_sheets_config(user_id)
        if not gs_config or not gs_config.get('enabled'):
            await status_msg.edit_text(
                "❌ Google Sheets не настроен.\n"
                "Используйте /settings → Google Sheets для настройки."
            )
            return

        from tender_sniper.google_sheets_sync import get_sheets_sync
        sheets_sync = get_sheets_sync()
        if not sheets_sync:
            await status_msg.edit_text("❌ Google Sheets сервис недоступен")
            return

        # Получаем неэкспортированные тендеры за период
        notifications = await db.get_unexported_notifications(user_id, days=days)

        if not notifications:
            await status_msg.edit_text(
                f"Нет новых тендеров для экспорта за {period_name}.\n"
                "Все тендеры уже в таблице ✅"
            )
            return

        subscription_tier = user.get('subscription_tier', 'trial')

        exported, failed, _ = await _export_notifications(
            notifications=notifications,
            gs_config=gs_config,
            subscription_tier=subscription_tier,
            status_msg=status_msg,
            db=db,
        )

        # Финальный результат
        result = f"✅ <b>Экспорт завершён!</b>\n\n"
        result += f"📊 Добавлено в таблицу: {exported}\n"
        if failed:
            result += f"❌ Ошибок: {failed}\n"
        result += f"\nПериод: {period_name}"

        await status_msg.edit_text(result, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Mass export error: {e}", exc_info=True)
        await status_msg.edit_text("❌ Ошибка при экспорте. Попробуйте позже.")


# ============================================
# МАССОВЫЙ ЭКСПОРТ /export_selected (из HTML-отчёта)
# ============================================

@router.message(Command("export_selected"))
async def cmd_export_selected(message: Message):
    """Массовый экспорт выбранных тендеров по номерам из HTML-отчёта."""
    args = message.text.split()[1:]  # номера тендеров
    if not args:
        await message.answer(
            "Укажите номера тендеров после команды.\n"
            "Пример: <code>/export_selected 0123456789 9876543210</code>\n\n"
            "Используйте кнопку «Скопировать команду» в HTML-отчёте.",
            parse_mode="HTML"
        )
        return

    tender_numbers = args[:50]  # Лимит 50 за раз
    telegram_id = message.from_user.id

    status_msg = await message.answer(
        f"⏳ Ищу {len(tender_numbers)} тендеров для экспорта..."
    )

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(telegram_id)
        if not user:
            await status_msg.edit_text("❌ Пользователь не найден")
            return

        user_id = user.get('id')

        # Проверяем Google Sheets
        gs_config = await db.get_google_sheets_config(user_id)
        if not gs_config or not gs_config.get('enabled'):
            await status_msg.edit_text(
                "❌ Google Sheets не настроен.\n"
                "Используйте /settings → Google Sheets для настройки."
            )
            return

        from tender_sniper.google_sheets_sync import get_sheets_sync
        sheets_sync = get_sheets_sync()
        if not sheets_sync:
            await status_msg.edit_text("❌ Google Sheets сервис недоступен")
            return

        # Собираем уведомления по номерам
        notifications = []
        not_found = []
        already_exported = []

        for num in tender_numbers:
            notif = await db.get_notification_by_tender_number(user_id, num)
            if not notif:
                not_found.append(num)
            elif notif.get('sheets_exported'):
                already_exported.append(num)
            else:
                notifications.append(notif)

        if not notifications:
            parts = []
            if already_exported:
                parts.append(f"✅ Уже в таблице: {len(already_exported)}")
            if not_found:
                parts.append(f"❓ Не найдено: {len(not_found)}")
            await status_msg.edit_text(
                "Нет новых тендеров для экспорта.\n" + "\n".join(parts)
            )
            return

        subscription_tier = user.get('subscription_tier', 'trial')

        exported, failed, _ = await _export_notifications(
            notifications=notifications,
            gs_config=gs_config,
            subscription_tier=subscription_tier,
            status_msg=status_msg,
            db=db,
        )

        # Финальный результат
        result = f"✅ <b>Экспорт завершён!</b>\n\n"
        result += f"📊 Добавлено в таблицу: {exported}\n"
        if failed:
            result += f"❌ Ошибок: {failed}\n"
        if already_exported:
            result += f"✅ Уже в таблице: {len(already_exported)}\n"
        if not_found:
            result += f"❓ Не найдено в истории: {len(not_found)}\n"

        await status_msg.edit_text(result, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Export selected error: {e}", exc_info=True)
        await status_msg.edit_text("❌ Ошибка при экспорте. Попробуйте позже.")


# ============================================
# AI АНАЛИЗ ТЕНДЕРА /analyze
# ============================================

def _extract_tender_number(text: str) -> str | None:
    """Извлекает номер закупки из текста или URL."""
    # URL: regNumber=(\d+)
    m = re.search(r'regNumber=(\d+)', text)
    if m:
        return m.group(1)
    # Чистый номер (18-25 цифр — номера закупок)
    m = re.search(r'\b(\d{18,25})\b', text)
    if m:
        return m.group(1)
    return None


async def _run_ai_analysis(tender_number: str, subscription_tier: str) -> tuple[str, bool, dict]:
    """
    Скачать документы → извлечь текст → AI анализ → форматировать.

    Returns:
        (formatted_text, is_ai)

    Raises:
        ImportError: если модули не установлены
        RuntimeError: если документация недоступна или текст не извлечён
    """
    from src.parsers.zakupki_document_downloader import ZakupkiDocumentDownloader
    from src.document_processor.text_extractor import TextExtractor
    from tender_sniper.ai_document_extractor import (
        get_document_extractor,
        format_extraction_for_telegram
    )

    downloader = ZakupkiDocumentDownloader()
    tender_url = f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber={tender_number}"

    # Запускаем синхронный downloader в отдельном потоке
    result = await asyncio.to_thread(
        downloader.download_documents,
        tender_url,
        tender_number,
        None
    )

    if not result or result.get('downloaded', 0) == 0:
        raise RuntimeError("Не удалось загрузить документацию")

    # Извлекаем текст из документов
    combined_text = ""
    files = result.get('files', [])[:3]  # Анализируем до 3 документов
    for doc_info in files:
        doc_path = doc_info.get('path')
        if not doc_path:
            continue
        try:
            extract_result = TextExtractor.extract_text(doc_path)
            if extract_result['text'] and not extract_result['text'].startswith('[Не удалось'):
                combined_text += f"\n\n=== {extract_result['file_name']} ===\n{extract_result['text']}"
        except Exception as e:
            logger.warning(f"Не удалось извлечь текст из {doc_path}: {e}")

    if not combined_text:
        raise RuntimeError("Не удалось извлечь текст из документации")

    # AI анализ
    extractor = get_document_extractor()
    extraction, is_ai = await extractor.extract_from_text(
        combined_text,
        subscription_tier,
        {'number': tender_number}
    )

    formatted = format_extraction_for_telegram(extraction, is_ai)
    return formatted, is_ai, extraction


@router.message(Command("analyze"))
async def cmd_analyze(message: Message, state: FSMContext):
    """Начать самостоятельный AI-анализ тендера."""
    telegram_id = message.from_user.id

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(telegram_id)
        if not user:
            await message.answer("❌ Пользователь не найден")
            return

        subscription_tier = user.get('subscription_tier', 'trial')

        # Проверка Premium
        from tender_sniper.ai_features import AIFeatureGate, format_ai_feature_locked_message
        gate = AIFeatureGate(subscription_tier)

        if not gate.can_use('document_extraction'):
            await message.answer(
                format_ai_feature_locked_message('document_extraction'),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⭐ Перейти на Premium", callback_data="upgrade_plan")],
                    [InlineKeyboardButton(text="« Меню", callback_data="sniper_menu")]
                ])
            )
            return

        # Проверяем, передан ли номер прямо в команде: /analyze 0372200197326000002
        args_text = message.text.split(maxsplit=1)
        if len(args_text) > 1:
            tender_number = _extract_tender_number(args_text[1])
            if tender_number:
                await _do_analyze(message, tender_number, subscription_tier)
                return

        # Переходим в FSM
        await state.set_state(AnalyzeStates.waiting_for_tender)
        await message.answer(
            "🔬 <b>AI Анализ тендера</b>\n\n"
            "Пришлите номер закупки или ссылку на zakupki.gov.ru\n\n"
            "Пример: <code>0372200197326000002</code>\n"
            "Или: ссылку с сайта zakupki.gov.ru",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="sniper_menu")]
            ])
        )
    except Exception as e:
        logger.error(f"cmd_analyze error: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка")


@router.callback_query(F.data == "analyze_start")
async def analyze_start_callback(callback: CallbackQuery, state: FSMContext):
    """Начать AI-анализ через кнопку меню."""
    telegram_id = callback.from_user.id
    await callback.answer()

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(telegram_id)
        if not user:
            await callback.message.answer("❌ Пользователь не найден")
            return

        subscription_tier = user.get('subscription_tier', 'trial')

        # Проверка Premium
        from tender_sniper.ai_features import AIFeatureGate, format_ai_feature_locked_message
        gate = AIFeatureGate(subscription_tier)

        if not gate.can_use('document_extraction'):
            await callback.message.edit_text(
                format_ai_feature_locked_message('document_extraction'),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⭐ Перейти на Premium", callback_data="upgrade_plan")],
                    [InlineKeyboardButton(text="« Меню", callback_data="sniper_menu")]
                ])
            )
            return

        # Переходим в FSM
        await state.set_state(AnalyzeStates.waiting_for_tender)
        await callback.message.edit_text(
            "🔬 <b>AI Анализ тендера</b>\n\n"
            "Пришлите номер закупки или ссылку на zakupki.gov.ru\n\n"
            "Пример: <code>0372200197326000002</code>\n"
            "Или: ссылку с сайта zakupki.gov.ru",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="sniper_menu")]
            ])
        )
    except Exception as e:
        logger.error(f"analyze_start_callback error: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.message(AnalyzeStates.waiting_for_tender)
async def process_analyze_input(message: Message, state: FSMContext):
    """Обработка ввода номера тендера для AI-анализа."""
    text = message.text or ""
    tender_number = _extract_tender_number(text)

    if not tender_number:
        await message.answer(
            "❌ Не удалось распознать номер закупки.\n\n"
            "Пришлите номер (18-25 цифр) или ссылку с zakupki.gov.ru\n"
            "Пример: <code>0372200197326000002</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="sniper_menu")]
            ])
        )
        return

    await state.clear()

    # Получаем subscription_tier
    db = await get_sniper_db()
    user = await db.get_user_by_telegram_id(message.from_user.id)
    subscription_tier = user.get('subscription_tier', 'trial') if user else 'trial'

    await _do_analyze(message, tender_number, subscription_tier)


async def _do_analyze(message: Message, tender_number: str, subscription_tier: str):
    """Выполняет AI-анализ и отправляет результат."""
    status_msg = await message.answer(
        f"🔍 <b>Анализирую документацию тендера {tender_number}...</b>\n\n"
        f"Это может занять некоторое время.",
        parse_mode="HTML"
    )

    try:
        formatted, is_ai, extraction = await _run_ai_analysis(tender_number, subscription_tier)

        await status_msg.edit_text(
            formatted,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📄 Открыть на zakupki.gov.ru",
                    url=f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber={tender_number}"
                )],
                [InlineKeyboardButton(text="🔬 Ещё анализ", callback_data="analyze_start")],
                [InlineKeyboardButton(text="« Меню", callback_data="sniper_menu")]
            ])
        )

        # Если сделка уже в Битрикс24 — обновляем AI поля и перемещаем на AI-этап
        try:
            from bot.handlers.bitrix24 import update_bitrix24_deal_ai_results
            from tender_sniper.database import get_sniper_db as _get_db
            _db = await _get_db()
            _user = await _db.get_user_by_telegram_id(message.from_user.id)
            if _user:
                _webhook = (_user.get('data') or {}).get('bitrix24_webhook_url', '')
                if _webhook:
                    _notif = await _db.get_notification_by_tender_number(_user['id'], tender_number)
                    _deal_id = _notif.get('bitrix24_deal_id') if _notif else None
                    if _deal_id:
                        await update_bitrix24_deal_ai_results(_webhook, _deal_id, extraction, formatted)
                        logger.info(f"Bitrix24 deal {_deal_id} updated with AI results after _do_analyze")
        except Exception as _bx_err:
            logger.debug(f"Bitrix24 AI results update after _do_analyze: {_bx_err}")

    except ImportError as ie:
        logger.error(f"Модуль не найден: {ie}")
        await status_msg.edit_text(
            "❌ Функция анализа документации временно недоступна.\n\n"
            "Необходимые модули не установлены.",
            parse_mode="HTML"
        )

    except RuntimeError as re_err:
        await status_msg.edit_text(
            f"❌ {re_err}\n\n"
            f"Тендер: {tender_number}\n"
            "Возможно, документы недоступны или тендер завершён.",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"AI analysis error for {tender_number}: {e}", exc_info=True)
        await status_msg.edit_text(
            "❌ Не удалось проанализировать документацию.\n\n"
            "Попробуйте позже.",
            parse_mode="HTML"
        )
