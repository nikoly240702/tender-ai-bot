"""
Обработчики экспорта тендеров в Google Sheets.

- Кнопка "📊 В таблицу" на каждом уведомлении — экспорт 1 тендера
- Команда /export — массовый экспорт за период
"""

import logging
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from tender_sniper.database import get_sniper_db

logger = logging.getLogger(__name__)

router = Router(name="sheets_export")


# ============================================
# КНОПКА "📊 В таблицу" на уведомлении
# ============================================

@router.callback_query(F.data.startswith("sheets_") & ~F.data.startswith("sheets_done_"))
async def export_single_tender(callback: CallbackQuery):
    """Экспортирует один тендер в Google Sheets по нажатию кнопки."""
    tender_number = callback.data.replace("sheets_", "")
    telegram_id = callback.from_user.id

    await callback.answer("Экспортирую в Google Sheets...")

    try:
        db = get_sniper_db()
        user = await db.get_user_by_telegram_id(telegram_id)
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        user_id = user.get('id')

        # Проверяем Google Sheets config
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
            await callback.answer("Тендер не найден в истории", show_alert=True)
            return

        # Проверяем, не экспортирован ли уже
        if notification.get('sheets_exported'):
            await callback.answer("Уже в таблице ✅", show_alert=True)
            return

        # Экспортируем
        from tender_sniper.google_sheets_sync import get_sheets_sync, AI_COLUMNS, enrich_tender_with_ai
        sheets_sync = get_sheets_sync()
        if not sheets_sync:
            await callback.answer("Google Sheets сервис недоступен", show_alert=True)
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

        # AI enrichment для Premium
        ai_data = {}
        user_columns = set(gs_config.get('columns', []))
        has_ai_columns = bool(user_columns & AI_COLUMNS)
        subscription_tier = user.get('subscription_tier', 'trial')

        if has_ai_columns and subscription_tier == 'premium' and gs_config.get('ai_enrichment'):
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

        await sheets_sync.append_tender(
            spreadsheet_id=gs_config['spreadsheet_id'],
            tender_data=tender_data,
            match_data=match_data,
            columns=gs_config.get('columns', []),
            sheet_name=gs_config.get('sheet_name', 'Тендеры')
        )

        # Помечаем как экспортированный
        await db.mark_notification_exported(notification.get('id'))

        # Обновляем кнопку
        await callback.answer("✅ Добавлено в Google Sheets!", show_alert=True)

        # Заменяем кнопку на "✅ В таблице"
        try:
            if callback.message and callback.message.reply_markup:
                new_buttons = []
                for row in callback.message.reply_markup.inline_keyboard:
                    new_row = []
                    for btn in row:
                        if btn.callback_data == f"sheets_{tender_number}":
                            new_row.append(InlineKeyboardButton(
                                text="✅ В таблице",
                                callback_data=f"sheets_done_{tender_number}"
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
        await callback.answer("Ошибка экспорта. Попробуйте позже.", show_alert=True)


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
        db = get_sniper_db()
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

        from tender_sniper.google_sheets_sync import get_sheets_sync, AI_COLUMNS, enrich_tender_with_ai
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

        total = len(notifications)
        exported = 0
        failed = 0
        subscription_tier = user.get('subscription_tier', 'trial')
        user_columns = set(gs_config.get('columns', []))
        has_ai_columns = bool(user_columns & AI_COLUMNS)
        is_premium = subscription_tier == 'premium'

        # Обновляем статус каждые 5 тендеров
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

                ai_data = {}
                if has_ai_columns and is_premium and gs_config.get('ai_enrichment'):
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
                    sheet_name=gs_config.get('sheet_name', 'Тендеры')
                )

                await db.mark_notification_exported(notif.get('id'))
                exported += 1

                # Обновляем статус
                if (i + 1) % 5 == 0 or i == total - 1:
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
