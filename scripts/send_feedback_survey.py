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


# ============================================
# SEND FLOW
# ============================================

def _build_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✉️ Написать Николаю", url=FOUNDER_URL),
    ]])


async def _already_sent() -> Optional[int]:
    """Возвращает id broadcast, если рассылка уже стартовала, иначе None."""
    async with DatabaseSession() as session:
        row = await session.execute(
            select(BroadcastMessage.id).where(
                BroadcastMessage.target_tier == BROADCAST_KEY
            ).limit(1)
        )
        rec = row.first()
        return rec[0] if rec else None


async def send_feedback_survey(bot: Bot) -> Dict[str, Any]:
    """
    Основная функция рассылки. Возвращает dict со статистикой:
    {broadcast_id, audience, delivered, blocked, errors, elapsed_sec}.

    Идемпотентна: если BroadcastMessage с target_tier=BROADCAST_KEY уже существует,
    возвращает {'skipped': True, ...} без повторной отправки.
    """
    started = datetime.utcnow()

    existing = await _already_sent()
    if existing:
        logger.info(f"Feedback survey already sent (broadcast_id={existing}). Skipping.")
        return {'skipped': True, 'broadcast_id': existing}

    audience = await fetch_audience()
    logger.info(f"Feedback survey audience: {len(audience)} users")

    if not audience:
        logger.info("Feedback survey: empty audience, nothing to send.")
        return {'skipped': True, 'reason': 'empty_audience'}

    # Создаём запись рассылки сразу — это и есть idempotency-флаг.
    async with DatabaseSession() as session:
        bm = BroadcastMessage(
            message_text=MESSAGE_TEXT,
            target_tier=BROADCAST_KEY,
            sent_at=datetime.utcnow(),
            total_recipients=len(audience),
            successful=0,
            failed=0,
            created_by='feedback_survey_scheduler',
        )
        session.add(bm)
        await session.flush()
        broadcast_id = bm.id
        await session.commit()

    logger.info(f"Feedback survey: created broadcast_id={broadcast_id}")

    keyboard = _build_keyboard()
    delivered = 0
    blocked = 0
    errors = 0

    for user in audience:
        async with DatabaseSession() as session:
            recipient = BroadcastRecipient(
                broadcast_id=broadcast_id,
                user_id=user['id'],
                status='pending',
            )
            session.add(recipient)
            await session.flush()
            recipient_id = recipient.id
            await session.commit()

        try:
            await bot.send_message(
                chat_id=user['telegram_id'],
                text=MESSAGE_TEXT,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            new_status = 'delivered'
            delivered += 1
            logger.info(f"  sent to {user['telegram_id']} (@{user.get('username')})")
        except TelegramForbiddenError:
            new_status = 'blocked'
            blocked += 1
            logger.info(f"  blocked by {user['telegram_id']}")
        except Exception as e:
            new_status = 'error'
            errors += 1
            logger.warning(f"  error sending to {user['telegram_id']}: {e}")

        async with DatabaseSession() as session:
            await session.execute(
                update(BroadcastRecipient)
                .where(BroadcastRecipient.id == recipient_id)
                .values(status=new_status, delivered_at=datetime.utcnow())
            )
            await session.commit()

        await asyncio.sleep(RATE_LIMIT_SLEEP)

    elapsed = (datetime.utcnow() - started).total_seconds()
    async with DatabaseSession() as session:
        await session.execute(
            update(BroadcastMessage)
            .where(BroadcastMessage.id == broadcast_id)
            .values(successful=delivered, failed=blocked + errors)
        )
        await session.commit()

    stats = {
        'broadcast_id': broadcast_id,
        'audience': len(audience),
        'delivered': delivered,
        'blocked': blocked,
        'errors': errors,
        'elapsed_sec': round(elapsed, 1),
    }
    logger.info(f"Feedback survey done: {stats}")
    return stats


# ============================================
# ADMIN REPORT
# ============================================

async def send_admin_report(bot: Bot, stats: Dict[str, Any]) -> None:
    """Отправить отчёт админу (ADMIN_TELEGRAM_ID) в личку. Если переменной нет — только лог."""
    admin_id_raw = os.getenv('ADMIN_TELEGRAM_ID', '').strip()
    if not admin_id_raw:
        logger.warning(f"ADMIN_TELEGRAM_ID not set, skipping admin report. Stats: {stats}")
        return

    try:
        admin_id = int(admin_id_raw)
    except ValueError:
        logger.error(f"ADMIN_TELEGRAM_ID is not an integer: {admin_id_raw!r}")
        return

    if stats.get('skipped'):
        text_msg = (
            f"📋 Feedback Survey — пропуск\n\n"
            f"Причина: {stats.get('reason', 'already_sent')}\n"
            f"Broadcast ID: {stats.get('broadcast_id', '—')}"
        )
    else:
        text_msg = (
            f"📋 Feedback Survey — итоги отправки\n\n"
            f"Целевая аудитория: {stats['audience']}\n"
            f"Доставлено: {stats['delivered']}\n"
            f"Заблокировали бота: {stats['blocked']}\n"
            f"Ошибок: {stats['errors']}\n"
            f"Время работы: {stats['elapsed_sec']} сек\n"
            f"Broadcast ID: {stats['broadcast_id']}\n\n"
            f"Конверсию посчитаем через 14 дней."
        )

    try:
        await bot.send_message(chat_id=admin_id, text=text_msg)
        logger.info(f"Admin report sent to {admin_id}")
    except Exception as e:
        logger.error(f"Failed to send admin report to {admin_id}: {e}")


# ============================================
# SCHEDULER
# ============================================

async def scheduled_feedback_survey(bot: Bot) -> None:
    """
    Ждёт до TARGET_DT_UTC, затем запускает рассылку и админ-отчёт.
    Если рассылка уже была отправлена (в т.ч. в прошлый рестарт бота) — ничего не делает.
    Если текущее время уже прошло target и рассылки не было — запускает немедленно.
    """
    try:
        if await _already_sent():
            logger.info("Feedback survey scheduler: already sent previously, exiting.")
            return

        now = datetime.utcnow()
        wait = (TARGET_DT_UTC - now).total_seconds()

        if wait > 0:
            logger.info(
                f"Feedback survey scheduler: sleeping {wait:.0f}s "
                f"until {TARGET_DT_UTC.isoformat()} UTC "
                f"({(TARGET_DT_UTC + timedelta(hours=3)).isoformat()} MSK)"
            )
            await asyncio.sleep(wait)

        # Повторная проверка на случай гонки при рестарте ровно возле T=0.
        if await _already_sent():
            logger.info("Feedback survey scheduler: already sent (race), exiting.")
            return

        stats = await send_feedback_survey(bot)
        await send_admin_report(bot, stats)
    except asyncio.CancelledError:
        logger.info("Feedback survey scheduler cancelled")
        raise
    except Exception as e:
        logger.error(f"Feedback survey scheduler failed: {e}", exc_info=True)


# ============================================
# CLI
# ============================================

async def _cli_main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Только напечатать аудиторию, не слать')
    parser.add_argument('--send-now', action='store_true', help='Отправить немедленно, игнорируя расписание')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    )

    try:
        await init_database()
    except Exception:
        pass

    if args.dry_run:
        audience = await fetch_audience()
        print(f"Audience: {len(audience)} users")
        for u in audience[:50]:
            print(f"  id={u['id']} telegram_id={u['telegram_id']} username=@{u.get('username')} tier={u['subscription_tier']}")
        if len(audience) > 50:
            print(f"  ... and {len(audience) - 50} more")
        return

    if not args.send_now:
        print("Specify --dry-run or --send-now. Automatic scheduling runs from bot/main.py only.")
        return

    bot_token = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('BOT_TOKEN')
    if not bot_token:
        print("ERROR: TELEGRAM_BOT_TOKEN not set")
        return

    bot = Bot(token=bot_token)
    try:
        stats = await send_feedback_survey(bot)
        await send_admin_report(bot, stats)
        print(f"Result: {stats}")
    finally:
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(_cli_main())
