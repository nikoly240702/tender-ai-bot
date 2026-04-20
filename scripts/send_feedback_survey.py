"""
Feedback survey broadcast.

Отправляет персональное сообщение от основателя пользователям,
у которых истёк trial и есть хотя бы один фильтр — с просьбой
написать обратную связь в личку @nikolai_chizhik.

Запуск:
    python -m scripts.send_feedback_survey --dry-run   # только печать аудитории
    python -m scripts.send_feedback_survey --send-now  # отправить немедленно, игнорируя расписание
    (или автоматически из bot/main.py в 11:00 MSK 2026-04-21)
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, update, text

from database import (
    init_database, DatabaseSession,
    BroadcastMessage, BroadcastRecipient,
)

logger = logging.getLogger(__name__)

# ============================================
# CONFIG
# ============================================

BROADCAST_KEY = 'feedback_survey_2026_04_21'
TARGET_DT_UTC = datetime(2026, 4, 21, 8, 0, 0)  # 11:00 MSK
FOUNDER_HANDLE = 'nikolai_chizhik'
FOUNDER_URL = f'https://t.me/{FOUNDER_HANDLE}'
RATE_LIMIT_SLEEP = 0.05  # 20 msg/s, под лимитом Telegram 30/s

MESSAGE_TEXT = (
    "Привет! Это Николай, основатель Tender Sniper.\n\n"
    "Ты один из первых, кто попробовал бот всерьёз — создал фильтр, дождался уведомлений. "
    "Но подписку не оформил, и мне правда важно понять почему.\n\n"
    f"Я читаю каждый ответ лично. Напиши в личку — @{FOUNDER_HANDLE} — что остановило? "
    "Неудобно, не нашлось нужных тендеров, цена, что-то другое. Одной фразы достаточно.\n\n"
    "От этого зависит, в какую сторону мы доделываем продукт. Спасибо, что попробовал."
)

AUDIENCE_SQL = """
    SELECT u.id, u.telegram_id, u.username, u.subscription_tier
    FROM sniper_users u
    WHERE (
            u.subscription_tier = 'expired'
            OR (u.subscription_tier = 'trial' AND u.trial_expires_at < now())
          )
      AND u.telegram_id IS NOT NULL
      AND u.telegram_id > 0
      AND (u.status IS NULL OR u.status != 'banned')
      AND EXISTS (
            SELECT 1 FROM sniper_filters f
            WHERE f.user_id = u.id AND f.deleted_at IS NULL
          )
"""


# ============================================
# AUDIENCE
# ============================================

async def fetch_audience() -> List[Dict[str, Any]]:
    """Возвращает список пользователей-получателей рассылки."""
    async with DatabaseSession() as session:
        result = await session.execute(text(AUDIENCE_SQL))
        return [dict(row) for row in result.mappings().all()]
