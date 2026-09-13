"""Кнопка «🗂 Взять в работу» на карточке тендера — кладёт процедуру на
канбан кабинета (аналог кнопки «В Б24», но для команд без Bitrix).

Компания резолвится по company_id самого уведомления/тендера (т.е. по
той компании, чей фильтр его поймал), а не по контексту чата или по
дефолтной компании нажавшего — иначе для владельца, состоящего сразу
в нескольких командах, карточка из ЛИЧКИ всегда падала бы в его самую
старую компанию, независимо от того, какой команде реально принадлежит
фильтр, который прислал это уведомление.
"""

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest

from tender_sniper.database import get_sniper_db

logger = logging.getLogger(__name__)

router = Router(name="pipeline_take")


async def _safe_answer(callback: CallbackQuery, text: str, show_alert: bool = False):
    """См. bot/handlers/bitrix24.py::_safe_answer — тот же паттерн."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest as e:
        if 'query is too old' in str(e) or 'query ID is invalid' in str(e):
            try:
                await callback.message.answer(text)
            except Exception:
                pass
        else:
            raise


async def _replace_take_button(callback: CallbackQuery, tender_number: str):
    """Заменяет «🗂 Взять в работу» на «✅ В пайплайне»."""
    try:
        from bot.utils import safe_callback_data
        markup = callback.message.reply_markup
        if not markup:
            return

        old_cd = safe_callback_data("take_work", tender_number)
        new_cd = safe_callback_data("take_done", tender_number)

        new_rows = []
        for row in markup.inline_keyboard:
            new_row = []
            for btn in row:
                if btn.callback_data == old_cd:
                    new_row.append(InlineKeyboardButton(
                        text="✅ В пайплайне",
                        callback_data=new_cd,
                    ))
                else:
                    new_row.append(btn)
            new_rows.append(new_row)

        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=new_rows)
        )
    except Exception as e:
        logger.debug(f"_replace_take_button: {e}")


@router.callback_query(F.data.startswith("take_work_") | F.data.startswith("take_done_"))
async def handle_take_work(callback: CallbackQuery):
    """Создаёт карточку тендера на канбане компании нажавшего пользователя."""
    data = callback.data

    if data.startswith("take_done_"):
        await callback.answer("✅ Уже в пайплайне")
        return

    tender_number = data[len("take_work_"):]
    telegram_id = callback.from_user.id

    try:
        db = await get_sniper_db()
        user = await db.get_user_by_telegram_id(telegram_id)
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        from cabinet.team_service import get_active_company

        company_id = None
        # Основной источник: company_id самого уведомления — это компания,
        # чей фильтр реально поймал этот тендер. Сначала пробуем найти
        # уведомление под нажавшим (быстрее, точнее), затем — вообще
        # любое по этому номеру (нажавший мог не быть тем, кто изначально
        # получил уведомление — например, в групповом чате).
        notif = await db.get_notification_by_tender_number(user['id'], tender_number)
        if not notif or not notif.get('company_id'):
            notif = await db.find_notification_by_tender_number(tender_number)
        if notif and notif.get('company_id'):
            company_id = notif['company_id']

        company = None
        if company_id:
            from database import DatabaseSession, Company
            async with DatabaseSession() as session:
                company_row = await session.get(Company, company_id)
                if company_row:
                    company = {
                        'id': company_row.id,
                        'name': company_row.name,
                        'owner_user_id': company_row.owner_user_id,
                    }

        # Фолбэк — если уведомления нет вовсе (например, тендер добавлен
        # вручную, не через автомониторинг): компания нажавшего по умолчанию.
        if not company:
            company = await get_active_company(user['id'], None)
        if not company:
            await callback.answer(
                "Вы не состоите ни в одной команде кабинета. "
                "Обратитесь к тому, кто вас пригласил.",
                show_alert=True,
            )
            return

        from cabinet import pipeline_service
        result = await pipeline_service.create_card_from_tender(
            company_id=company['id'],
            tender_number=tender_number,
            creator_user_id=user['id'],
            source=pipeline_service.SOURCE_FEED,
        )

        if 'error' in result:
            if result['error'] == 'already_exists':
                await _replace_take_button(callback, tender_number)
                await _safe_answer(callback, "✅ Уже в пайплайне")
            else:
                await _safe_answer(callback, "⚠️ Не удалось добавить в пайплайн")
            return

        await _replace_take_button(callback, tender_number)
        await _safe_answer(callback, f"✅ Добавлено в пайплайн «{company['name']}»")

    except Exception as e:
        logger.error(f"handle_take_work error: {e}", exc_info=True)
        try:
            await _safe_answer(callback, "⚠️ Ошибка при добавлении в пайплайн")
        except Exception:
            pass
