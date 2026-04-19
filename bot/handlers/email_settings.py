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


@router.message(Command("test_email"))
async def cmd_test_email(message: Message):
    """Send a test email to verify SMTP works."""
    async with DatabaseSession() as session:
        user = await session.scalar(
            select(SniperUser).where(SniperUser.telegram_id == message.from_user.id)
        )
    if not user or not user.email:
        await message.answer("❌ Email не установлен. Используйте /email your@mail.com")
        return
    if not user.email_notifications_enabled:
        await message.answer("❌ Email уведомления отключены. Используйте /email your@mail.com")
        return

    try:
        from bot.integrations import get_integration_manager
        mgr = get_integration_manager()
        test_tender = {
            'name': 'Тестовый тендер — проверка email уведомлений',
            'number': '0000000000000000001',
            'price': 500000,
            'customer': 'Тестовый заказчик',
            'region': 'Москва',
            'deadline': '2026-05-01',
            'url': 'https://zakupki.gov.ru',
            'filter_name': 'Тестовый фильтр',
        }
        success = await mgr.send_email_notification(user.email, test_tender)
        if success:
            await message.answer(f"✅ Тестовое письмо отправлено на <code>{user.email}</code>", parse_mode='HTML')
        else:
            await message.answer("❌ Ошибка отправки. Проверьте SMTP настройки.")
    except Exception as e:
        logger.error(f"Test email error: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {e}")


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
