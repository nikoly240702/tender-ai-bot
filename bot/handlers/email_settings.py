"""Telegram handlers for /email and /email_off commands."""
import re
import logging
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import update, select

from database import DatabaseSession, SniperUser

logger = logging.getLogger(__name__)
router = Router()

EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


@router.message(Command("email"))
async def cmd_email(message: Message):
    """Set email and enable notifications. Usage: /email user@example.com"""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        async with DatabaseSession() as session:
            user = await session.scalar(
                select(SniperUser).where(SniperUser.telegram_id == message.from_user.id)
            )
        current = user.email if user else None
        enabled = user.email_notifications_enabled if user else False
        text = (
            f"📧 <b>Email уведомления</b>\n\n"
            f"Текущий email: <code>{current or 'не задан'}</code>\n"
            f"Уведомления: <b>{'включены' if enabled else 'выключены'}</b>\n\n"
            f"Чтобы установить или изменить email и включить уведомления:\n"
            f"<code>/email your@email.com</code>\n\n"
            f"Чтобы отключить уведомления:\n<code>/email_off</code>"
        )
        await message.answer(text, parse_mode='HTML')
        return

    email = parts[1].strip()
    if not EMAIL_REGEX.match(email):
        await message.answer("❌ Неверный формат email. Пример: <code>user@example.com</code>", parse_mode='HTML')
        return

    async with DatabaseSession() as session:
        await session.execute(
            update(SniperUser)
            .where(SniperUser.telegram_id == message.from_user.id)
            .values(email=email, email_notifications_enabled=True)
        )
        await session.commit()

    await message.answer(
        f"✅ Email <code>{email}</code> сохранён. Уведомления о новых тендерах будут также приходить на почту.",
        parse_mode='HTML'
    )


@router.message(Command("email_off"))
async def cmd_email_off(message: Message):
    """Disable email notifications (keeps email in DB)."""
    async with DatabaseSession() as session:
        await session.execute(
            update(SniperUser)
            .where(SniperUser.telegram_id == message.from_user.id)
            .values(email_notifications_enabled=False)
        )
        await session.commit()
    await message.answer("✅ Email уведомления отключены. Чтобы включить снова — /email")
