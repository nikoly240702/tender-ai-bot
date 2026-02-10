"""
Обработчик для Telegram Mini App (Web App).

Команда /tenders открывает Mini App для просмотра и экспорта тендеров.
"""

import os
import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

logger = logging.getLogger(__name__)

router = Router(name="webapp")

WEBAPP_BASE_URL = os.getenv('WEBAPP_BASE_URL', 'https://tender-ai-bot-fresh-production.up.railway.app')


@router.message(Command("tenders"))
async def open_tender_webapp(message: Message):
    """Открывает Mini App со списком тендеров."""
    webapp_url = f"{WEBAPP_BASE_URL}/webapp/tenders"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="📊 Открыть мои тендеры",
            web_app=WebAppInfo(url=webapp_url)
        )
    ]])
    await message.answer(
        "Нажмите кнопку, чтобы открыть список ваших тендеров.\n"
        "Вы сможете просмотреть, отметить нужные и экспортировать в Google Sheets.",
        reply_markup=kb
    )


@router.callback_query(F.data == "open_webapp_tenders")
async def callback_open_webapp(callback: CallbackQuery):
    """Открывает Mini App по нажатию inline кнопки."""
    webapp_url = f"{WEBAPP_BASE_URL}/webapp/tenders"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="📊 Открыть мои тендеры",
            web_app=WebAppInfo(url=webapp_url)
        )
    ]])
    await callback.message.answer(
        "Нажмите кнопку, чтобы открыть список ваших тендеров:",
        reply_markup=kb
    )
    await callback.answer()
