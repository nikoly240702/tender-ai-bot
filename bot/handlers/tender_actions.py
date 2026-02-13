"""
Обработчики inline кнопок для действий с тендерами.

Обрабатывает: детали, избранное, напоминания, скрытие.
"""

import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from tender_sniper.database import get_sniper_db
from bot.utils import safe_callback_data
from bot.utils.access_check import require_feature
from bot.utils.tender_notifications import (
    format_detailed_tender_info,
    format_reminder_options
)
from bot.utils.tender_db_helpers import (
    add_to_favorites,
    remove_from_favorites,
    hide_tender,
    create_reminder,
    is_tender_hidden
)

logger = logging.getLogger(__name__)
router = Router()

# Временное хранилище для выбора напоминаний (tender_number -> user_id)
_reminder_context = {}


# ============================================
# ДЕТАЛИ ТЕНДЕРА
# ============================================

@router.callback_query(F.data.startswith("tender_details_"))
async def show_tender_details(callback: CallbackQuery):
    """Показывает детальную информацию о тендере."""
    await callback.answer()

    try:
        # Извлекаем tender_number
        tender_number = callback.data.replace("tender_details_", "")

        # Получаем данные тендера из БД
        db = await get_sniper_db()

        # Получаем user_id из sniper_users
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)
        if not sniper_user:
            await callback.message.answer("❌ Пользователь не найден в системе")
            return

        # Ищем тендер в уведомлениях пользователя
        tender_data = None

        # Используем DatabaseSession напрямую
        from database import DatabaseSession, SniperNotification
        from sqlalchemy import select

        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperNotification).where(
                    SniperNotification.user_id == sniper_user['id'],
                    SniperNotification.tender_number == tender_number
                ).order_by(SniperNotification.sent_at.desc())
            )
            notification = result.scalars().first()

            if notification:
                tender_data = {
                    'tender_number': notification.tender_number,
                    'tender_name': notification.tender_name,
                    'tender_price': notification.tender_price,
                    'tender_url': notification.tender_url,
                    'tender_region': notification.tender_region,
                    'tender_customer': notification.tender_customer,
                    'score': notification.score,
                    'matched_keywords': notification.matched_keywords if isinstance(notification.matched_keywords, list) else [],
                    'published_date': notification.published_date
                }

        if not tender_data:
            await callback.message.answer("❌ Тендер не найден")
            return

        # Форматируем детальное сообщение
        detailed_message = format_detailed_tender_info(tender_data)

        # Создаем клавиатуру с кнопками
        keyboard = [[InlineKeyboardButton(text="« Назад", callback_data=safe_callback_data("tender_back", tender_number))]]

        # Добавляем ссылку
        tender_url = tender_data.get('tender_url')
        if tender_url:
            if not tender_url.startswith('http'):
                tender_url = f"https://zakupki.gov.ru{tender_url}"
            keyboard.insert(0, [InlineKeyboardButton(text="🔗 Открыть на zakupki.gov.ru", url=tender_url)])

        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        # Track tender viewed
        import asyncio
        try:
            from bot.analytics import track_event, EventType
            asyncio.create_task(track_event(
                EventType.TENDER_VIEWED, telegram_id=callback.from_user.id,
                data={'tender_number': tender_number, 'score': tender_data.get('score')}
            ))
        except Exception:
            pass

        # Отправляем детали
        await callback.message.edit_text(
            text=detailed_message,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

    except Exception as e:
        logger.error(f"Ошибка показа деталей тендера: {e}", exc_info=True)
        await callback.message.answer("❌ Произошла ошибка при получении деталей тендера")


# ============================================
# ИЗБРАННОЕ
# ============================================

@router.callback_query(F.data.startswith("tender_favorite_"))
async def add_tender_to_favorites(callback: CallbackQuery):
    """Добавляет тендер в избранное."""
    await callback.answer()

    try:
        # Извлекаем tender_number
        tender_number = callback.data.replace("tender_favorite_", "")

        # Получаем user_id
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)
        if not sniper_user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return

        # Получаем данные тендера
        from database import DatabaseSession, SniperNotification
        from sqlalchemy import select

        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperNotification).where(
                    SniperNotification.user_id == sniper_user['id'],
                    SniperNotification.tender_number == tender_number
                ).order_by(SniperNotification.sent_at.desc())
            )
            notification = result.scalars().first()

        if not notification:
            await callback.answer("❌ Тендер не найден", show_alert=True)
            return

        # Добавляем в избранное
        success = await add_to_favorites(
            user_id=sniper_user['id'],
            tender_number=tender_number,
            tender_name=notification.tender_name,
            tender_price=notification.tender_price,
            tender_url=notification.tender_url
        )

        if success:
            # Обновляем клавиатуру (помечаем как "в избранном")
            keyboard = callback.message.reply_markup.inline_keyboard.copy() if callback.message.reply_markup else []

            # Заменяем кнопку "В избранное" на "⭐ В избранном"
            for row in keyboard:
                for button in row:
                    if button.callback_data and button.callback_data.startswith("tender_favorite_"):
                        button.text = "⭐ В избранном"
                        button.callback_data = "noop"  # Делаем неактивной

            new_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

            # Track tender favorited
            import asyncio
            try:
                from bot.analytics import track_event, EventType
                asyncio.create_task(track_event(
                    EventType.TENDER_FAVORITED, telegram_id=callback.from_user.id,
                    data={'tender_number': tender_number}
                ))
            except Exception:
                pass

            await callback.message.edit_reply_markup(reply_markup=new_markup)
            await callback.answer("⭐ Тендер добавлен в избранное!", show_alert=False)
        else:
            await callback.answer("ℹ️ Тендер уже в избранном", show_alert=True)

    except Exception as e:
        logger.error(f"Ошибка добавления в избранное: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


# ============================================
# НАПОМИНАНИЯ
# ============================================

@router.callback_query(F.data.startswith("tender_remind_"))
async def show_reminder_options_handler(callback: CallbackQuery):
    """Показывает опции для установки напоминания."""
    # Проверяем доступ к напоминаниям (Basic+)
    if not await require_feature(callback, 'reminders'):
        return

    try:
        # Извлекаем tender_number
        tender_number = callback.data.replace("tender_remind_", "")

        # Сохраняем контекст (какой тендер выбран)
        _reminder_context[callback.from_user.id] = tender_number

        # Показываем опции напоминаний
        keyboard = format_reminder_options()

        await callback.message.edit_text(
            text="🔔 <b>Когда напомнить о тендере?</b>\n\n"
                 "Напоминание будет отправлено за указанное количество дней до дедлайна.",
            reply_markup=keyboard,
            parse_mode='HTML'
        )

    except Exception as e:
        logger.error(f"Ошибка показа опций напоминания: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data.in_(["reminder_1d", "reminder_3d", "reminder_7d"]))
async def set_reminder_handler(callback: CallbackQuery):
    """Устанавливает напоминание."""
    await callback.answer()

    try:
        # Определяем количество дней
        days_map = {
            "reminder_1d": 1,
            "reminder_3d": 3,
            "reminder_7d": 7
        }
        days_before = days_map.get(callback.data, 3)

        # Получаем tender_number из контекста
        tender_number = _reminder_context.get(callback.from_user.id)
        if not tender_number:
            await callback.answer("❌ Тендер не выбран", show_alert=True)
            return

        # Получаем user_id
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)
        if not sniper_user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return

        # Получаем данные тендера
        from database import DatabaseSession, SniperNotification
        from sqlalchemy import select

        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperNotification).where(
                    SniperNotification.user_id == sniper_user['id'],
                    SniperNotification.tender_number == tender_number
                ).order_by(SniperNotification.sent_at.desc())
            )
            notification = result.scalars().first()

        if not notification:
            await callback.answer("❌ Тендер не найден", show_alert=True)
            return

        # Создаем напоминание
        success = await create_reminder(
            user_id=sniper_user['id'],
            tender_number=tender_number,
            days_before=days_before,
            tender_name=notification.tender_name,
            tender_url=notification.tender_url
        )

        # Очищаем контекст
        _reminder_context.pop(callback.from_user.id, None)

        if success:
            await callback.message.edit_text(
                text=f"✅ <b>Напоминание установлено!</b>\n\n"
                     f"Я напомню вам о тендере за <b>{days_before} {'день' if days_before == 1 else 'дня' if days_before < 5 else 'дней'}</b> до дедлайна.",
                parse_mode='HTML'
            )
        else:
            await callback.message.edit_text(
                text="❌ Не удалось установить напоминание. Попробуйте позже.",
                parse_mode='HTML'
            )

    except Exception as e:
        logger.error(f"Ошибка установки напоминания: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


@router.callback_query(F.data == "reminder_cancel")
async def cancel_reminder_handler(callback: CallbackQuery):
    """Отменяет установку напоминания."""
    await callback.answer()

    # Очищаем контекст
    _reminder_context.pop(callback.from_user.id, None)

    await callback.message.edit_text(
        text="❌ Установка напоминания отменена.",
        parse_mode='HTML'
    )


# ============================================
# СКРЫТИЕ ТЕНДЕРА
# ============================================

@router.callback_query(F.data.startswith("tender_hide_"))
async def hide_tender_handler(callback: CallbackQuery):
    """Скрывает тендер."""
    await callback.answer()

    try:
        # Извлекаем tender_number
        tender_number = callback.data.replace("tender_hide_", "")

        # Получаем user_id
        db = await get_sniper_db()
        sniper_user = await db.get_user_by_telegram_id(callback.from_user.id)
        if not sniper_user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return

        # Скрываем тендер
        success = await hide_tender(
            user_id=sniper_user['id'],
            tender_number=tender_number,
            reason="Скрыт пользователем через кнопку"
        )

        if success:
            await callback.message.edit_text(
                text="👎 <b>Тендер скрыт</b>\n\n"
                     "Подобные тендеры будут показываться реже.\n\n"
                     "Используйте /hidden для управления скрытыми тендерами.",
                parse_mode='HTML'
            )
        else:
            await callback.answer("ℹ️ Тендер уже скрыт", show_alert=True)

    except Exception as e:
        logger.error(f"Ошибка скрытия тендера: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка", show_alert=True)


# ============================================
# ВСПОМОГАТЕЛЬНЫЕ ОБРАБОТЧИКИ
# ============================================

@router.callback_query(F.data == "noop")
async def noop_handler(callback: CallbackQuery):
    """Обработчик для неактивных кнопок."""
    await callback.answer()


@router.callback_query(F.data.startswith("tender_back_"))
async def back_to_tender_handler(callback: CallbackQuery):
    """Возврат к краткому отображению тендера."""
    await callback.answer()

    # TODO: Можно восстановить исходное сообщение с кнопками
    # Пока просто информируем
    await callback.message.edit_text(
        text="ℹ️ Используйте /all_tenders для просмотра всех тендеров",
        parse_mode='HTML'
    )


# Экспортируем router
__all__ = ['router']
