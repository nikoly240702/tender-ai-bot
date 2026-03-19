"""
Обработчик Tender-GPT — чат с AI-ассистентом.

FSM-состояние TenderGPTStates.chatting:
- Все текстовые сообщения направляются в LangGraph агент
- Выход из чата: кнопка меню, команда /start, /menu
"""

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

from bot.states import TenderGPTStates
from tender_sniper.database import get_sniper_db

logger = logging.getLogger(__name__)

router = Router(name="tender_gpt")

# Lazy-load service to avoid import-time initialization
_service = None


async def _get_service():
    """Lazy-load TenderGPTService."""
    global _service
    if _service is None:
        from tender_sniper.tender_gpt.service import TenderGPTService
        _service = TenderGPTService()
    return _service


# ============================================
# ВХОД В TENDER-GPT ЧАТ
# ============================================

@router.message(F.text == "Tender-GPT")
async def enter_tender_gpt(message: Message, state: FSMContext):
    """Вход в режим Tender-GPT чата через ReplyKeyboard кнопку."""
    service = await _get_service()

    # Set FSM state
    await state.set_state(TenderGPTStates.chatting)
    await state.update_data(tender_number=None)

    greeting = await service.get_greeting()
    await message.answer(greeting, parse_mode="HTML")
    logger.info(f"User {message.from_user.id} entered Tender-GPT chat")


@router.callback_query(F.data == "tender_gpt_start")
async def enter_tender_gpt_inline(callback: CallbackQuery, state: FSMContext):
    """Вход в Tender-GPT из inline-меню."""
    await callback.answer()
    service = await _get_service()

    await state.set_state(TenderGPTStates.chatting)
    await state.update_data(tender_number=None)

    greeting = await service.get_greeting()
    await callback.message.answer(greeting, parse_mode="HTML")
    logger.info(f"User {callback.from_user.id} entered Tender-GPT chat (inline)")


# ============================================
# ОБРАБОТКА СООБЩЕНИЙ В ЧАТЕ
# ============================================

@router.message(StateFilter(TenderGPTStates.chatting), F.text)
async def handle_gpt_message(message: Message, state: FSMContext):
    """
    Обработка текстового сообщения в режиме Tender-GPT.

    Проверяет квоту, отправляет в LangGraph, возвращает ответ.
    """
    service = await _get_service()
    user_text = message.text.strip()

    if not user_text:
        return

    # Get user from DB
    db = await get_sniper_db()
    sniper_user = await db.get_user_by_telegram_id(message.from_user.id)
    if not sniper_user:
        await message.answer("Пользователь не найден. Нажмите /start для регистрации.")
        await state.clear()
        return

    # Get tender context from state
    state_data = await state.get_data()
    tender_number = state_data.get('tender_number')

    # Send "typing" indicator
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        result = await service.chat(
            telegram_id=message.from_user.id,
            user_id=sniper_user['id'],
            user_message=user_text,
            tender_number=tender_number,
        )

        response_text = result['response']
        quota = result.get('quota', {})

        # Add quota footer for non-unlimited users
        remaining = quota.get('remaining', 0)
        limit = quota.get('limit', 0)
        if limit < 999999 and remaining <= 5 and remaining > 0:
            response_text += f"\n\n<i>Осталось сообщений: {remaining}/{limit}</i>"

        await message.answer(response_text, parse_mode="HTML")

        # If quota just ran out — exit chat
        if not quota.get('allowed', True) and result.get('session_id') is None:
            await state.clear()

    except Exception as e:
        logger.error(f"Tender-GPT error for user {message.from_user.id}: {e}", exc_info=True)
        await message.answer(
            "Произошла ошибка. Попробуйте ещё раз или нажмите /start для перезапуска.",
            parse_mode="HTML",
        )
