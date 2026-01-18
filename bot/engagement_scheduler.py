"""
Engagement Scheduler - планировщик для вовлечения пользователей.

Включает:
- Follow-up сообщения (День 1, День 3)
- Дневной дайджест (9:00 МСК)
- Напоминания о дедлайнах тендеров
"""

import asyncio
import logging
from datetime import datetime, timedelta, time
from typing import Optional, List, Dict, Any

from aiogram import Bot

logger = logging.getLogger(__name__)

# Московское время (UTC+3)
MOSCOW_TZ_OFFSET = 3


class EngagementScheduler:
    """
    Планировщик для вовлечения пользователей.

    Запускает периодические задачи:
    - Follow-up сообщения новым пользователям
    - Дневной дайджест
    - Напоминания о дедлайнах
    """

    # Время отправки дневного дайджеста (МСК)
    DIGEST_HOUR = 9
    DIGEST_MINUTE = 0

    # Интервал проверки (в секундах)
    CHECK_INTERVAL = 3600  # каждый час

    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self._running = False
        self._task = None

    async def start(self):
        """Запуск планировщика."""
        if self._running:
            return

        self._running = True
        logger.info("📅 Engagement Scheduler запущен")

        while self._running:
            try:
                await self._run_scheduled_tasks()
            except Exception as e:
                logger.error(f"❌ Ошибка в планировщике: {e}", exc_info=True)

            # Ждём до следующей проверки
            await asyncio.sleep(self.CHECK_INTERVAL)

    async def stop(self):
        """Остановка планировщика."""
        self._running = False
        logger.info("🛑 Engagement Scheduler остановлен")

    async def _run_scheduled_tasks(self):
        """Выполнить запланированные задачи."""
        now = datetime.utcnow() + timedelta(hours=MOSCOW_TZ_OFFSET)
        current_hour = now.hour

        logger.info(f"🔄 Проверка задач в {now.strftime('%H:%M')} МСК")

        bot = Bot(token=self.bot_token)

        try:
            # 1. Follow-up сообщения
            await self._send_followup_messages(bot)

            # 2. Дневной дайджест в 9:00 МСК
            if current_hour == self.DIGEST_HOUR:
                await self._send_daily_digests(bot)

            # 3. Напоминания о дедлайнах
            await self._send_deadline_reminders(bot)

        finally:
            await bot.session.close()

    async def _send_followup_messages(self, bot: Bot):
        """Отправить follow-up сообщения новым пользователям."""
        from database import DatabaseSession, SniperUser
        from sqlalchemy import select, and_
        from bot.handlers.onboarding import send_day1_followup, send_day3_followup, get_user_stats

        now = datetime.utcnow()

        async with DatabaseSession() as session:
            # Получаем всех пользователей с активными подписками
            result = await session.execute(
                select(SniperUser).where(
                    SniperUser.subscription_tier.in_(['trial', 'basic', 'premium'])
                )
            )
            users = result.scalars().all()

        followups_sent = 0

        for user in users:
            try:
                # Проверяем user.data на наличие first_filter_created_at
                user_data = {}
                if hasattr(user, 'data') and user.data:
                    user_data = user.data if isinstance(user.data, dict) else {}

                first_filter_at = user_data.get('first_filter_created_at')
                if not first_filter_at:
                    continue

                # Парсим дату
                if isinstance(first_filter_at, str):
                    first_filter_dt = datetime.fromisoformat(first_filter_at.replace('Z', ''))
                else:
                    first_filter_dt = first_filter_at

                days_since_filter = (now - first_filter_dt).days

                # Проверяем, были ли уже отправлены follow-up
                day1_sent = user_data.get('followup_day1_sent', False)
                day3_sent = user_data.get('followup_day3_sent', False)

                # День 1 - отправляем через 24 часа
                if days_since_filter >= 1 and not day1_sent:
                    stats = await get_user_stats(user.telegram_id)
                    await send_day1_followup(bot, user.telegram_id, stats)

                    # Отмечаем как отправленное
                    await self._update_user_data(user.id, {'followup_day1_sent': True})
                    followups_sent += 1

                # День 3 - отправляем через 72 часа
                elif days_since_filter >= 3 and not day3_sent:
                    stats = await get_user_stats(user.telegram_id)
                    await send_day3_followup(bot, user.telegram_id, stats)

                    # Отмечаем как отправленное
                    await self._update_user_data(user.id, {'followup_day3_sent': True})
                    followups_sent += 1

            except Exception as e:
                logger.error(f"Ошибка отправки follow-up для {user.telegram_id}: {e}")

        if followups_sent > 0:
            logger.info(f"📧 Отправлено {followups_sent} follow-up сообщений")

    async def _update_user_data(self, user_id: int, data: Dict[str, Any]):
        """Обновить данные пользователя."""
        from database import DatabaseSession, SniperUser
        from sqlalchemy import update

        async with DatabaseSession() as session:
            user = await session.get(SniperUser, user_id)
            if user:
                current_data = user.data if isinstance(user.data, dict) else {}
                current_data.update(data)
                user.data = current_data
                await session.commit()

    async def _send_daily_digests(self, bot: Bot):
        """Отправить дневной дайджест пользователям."""
        from database import DatabaseSession, SniperUser, SniperFilter, SniperNotification
        from sqlalchemy import select, func, and_
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        yesterday = datetime.utcnow() - timedelta(days=1)

        async with DatabaseSession() as session:
            # Получаем активных пользователей
            result = await session.execute(
                select(SniperUser).where(
                    and_(
                        SniperUser.subscription_tier.in_(['trial', 'basic', 'premium']),
                        SniperUser.trial_expires_at > datetime.utcnow()  # Активная подписка
                    )
                )
            )
            users = result.scalars().all()

        digests_sent = 0

        for user in users:
            try:
                # Проверяем, включён ли дайджест у пользователя
                user_data = user.data if isinstance(user.data, dict) else {}
                if user_data.get('digest_disabled', False):
                    continue

                # Получаем статистику за вчера
                async with DatabaseSession() as session:
                    # Количество уведомлений за вчера
                    notifications_count = await session.scalar(
                        select(func.count(SniperNotification.id)).where(
                            and_(
                                SniperNotification.user_id == user.id,
                                SniperNotification.sent_at >= yesterday
                            )
                        )
                    ) or 0

                    # Количество активных фильтров
                    active_filters = await session.scalar(
                        select(func.count(SniperFilter.id)).where(
                            and_(
                                SniperFilter.user_id == user.id,
                                SniperFilter.is_active == True
                            )
                        )
                    ) or 0

                # Формируем дайджест
                if notifications_count > 0:
                    text = f"""
☀️ <b>Доброе утро!</b>

📊 <b>Ваш дневной дайджест:</b>

• 📬 Новых тендеров найдено: <b>{notifications_count}</b>
• 🎯 Активных фильтров: <b>{active_filters}</b>
• ⏱ Сэкономлено времени: <b>~{notifications_count * 0.5:.0f} ч</b>

<i>Нажмите кнопку ниже, чтобы посмотреть все тендеры.</i>
"""
                else:
                    text = f"""
☀️ <b>Доброе утро!</b>

📊 <b>Ваш дневной дайджест:</b>

Вчера не было новых тендеров по вашим фильтрам.

💡 <b>Совет:</b> Попробуйте расширить критерии поиска или добавить новые ключевые слова.

• 🎯 Активных фильтров: <b>{active_filters}</b>
"""

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📋 Все тендеры", callback_data="sniper_all_tenders")],
                    [InlineKeyboardButton(text="🎯 Мои фильтры", callback_data="sniper_my_filters")],
                    [InlineKeyboardButton(text="🔕 Отключить дайджест", callback_data="disable_digest")],
                ])

                await bot.send_message(user.telegram_id, text, reply_markup=keyboard, parse_mode="HTML")
                digests_sent += 1

                # Небольшая задержка между сообщениями
                await asyncio.sleep(0.1)

            except Exception as e:
                logger.warning(f"Не удалось отправить дайджест пользователю {user.telegram_id}: {e}")

        if digests_sent > 0:
            logger.info(f"📧 Отправлено {digests_sent} дневных дайджестов")

    async def _send_deadline_reminders(self, bot: Bot):
        """Отправить напоминания о дедлайнах тендеров."""
        from database import DatabaseSession, SniperUser, SniperNotification
        from sqlalchemy import select, and_, func
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        # Напоминаем за 3 дня до дедлайна
        reminder_days = 3
        target_date = (datetime.utcnow() + timedelta(days=reminder_days)).date()

        async with DatabaseSession() as session:
            # Получаем уведомления с дедлайном через 3 дня
            result = await session.execute(
                select(SniperNotification, SniperUser).join(
                    SniperUser, SniperNotification.user_id == SniperUser.id
                ).where(
                    and_(
                        SniperNotification.submission_deadline.isnot(None),
                        func.date(SniperNotification.submission_deadline) == target_date,
                        SniperUser.subscription_tier.in_(['trial', 'basic', 'premium'])
                    )
                )
            )
            notifications = result.all()

        reminders_sent = 0
        # Храним ID уведомлений, для которых уже отправили напоминание (в памяти на время сессии)
        sent_reminders_key = f"deadline_reminders_{target_date}"

        for notification, user in notifications:
            try:
                # Проверяем user.data, не отправляли ли уже напоминание
                user_data = user.data if isinstance(user.data, dict) else {}
                sent_reminders = user_data.get(sent_reminders_key, [])

                if notification.id in sent_reminders:
                    continue

                tender_name = notification.tender_name or "Тендер"
                tender_number = notification.tender_number or "N/A"
                tender_price = notification.tender_price or 0
                price_formatted = f"{tender_price:,.0f}".replace(",", " ") if tender_price else "Не указана"

                text = f"""
⏰ <b>Напоминание о дедлайне!</b>

📋 <b>{tender_name[:100]}{'...' if len(tender_name) > 100 else ''}</b>

🔢 Номер: <code>{tender_number}</code>
💰 Цена: <b>{price_formatted} ₽</b>
📅 Подача заявок до: <b>{notification.submission_deadline.strftime('%d.%m.%Y')}</b>

⚠️ <b>Осталось {reminder_days} дня до окончания приёма заявок!</b>
"""

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="📄 Открыть тендер",
                        url=notification.tender_url or f"https://zakupki.gov.ru/epz/order/notice/notice223/view/common-info.html?regNumber={tender_number}"
                    )],
                    [InlineKeyboardButton(text="✅ Участвую", callback_data=f"deadline_yes_{notification.id}")],
                    [InlineKeyboardButton(text="❌ Пропустить", callback_data=f"deadline_no_{notification.id}")],
                ])

                await bot.send_message(user.telegram_id, text, reply_markup=keyboard, parse_mode="HTML")

                # Отмечаем, что напоминание отправлено
                sent_reminders.append(notification.id)
                await self._update_user_data(user.id, {sent_reminders_key: sent_reminders})

                reminders_sent += 1
                await asyncio.sleep(0.1)

            except Exception as e:
                logger.warning(f"Не удалось отправить напоминание о дедлайне: {e}")

        if reminders_sent > 0:
            logger.info(f"⏰ Отправлено {reminders_sent} напоминаний о дедлайнах")


# ============================================
# CALLBACK HANDLERS для дайджеста и дедлайнов
# ============================================

from aiogram import Router

engagement_router = Router(name="engagement")


@engagement_router.callback_query(lambda c: c.data == "disable_digest")
async def handle_disable_digest(callback_query, state=None):
    """Отключить дневной дайджест."""
    from database import DatabaseSession, SniperUser
    from sqlalchemy import select, update

    user_id = callback_query.from_user.id

    async with DatabaseSession() as session:
        user = await session.scalar(
            select(SniperUser).where(SniperUser.telegram_id == user_id)
        )

        if user:
            current_data = user.data if isinstance(user.data, dict) else {}
            current_data['digest_disabled'] = True
            user.data = current_data
            await session.commit()

    await callback_query.answer("🔕 Дневной дайджест отключён")
    await callback_query.message.edit_text(
        "🔕 <b>Дневной дайджест отключён</b>\n\n"
        "Вы больше не будете получать утренние сводки.\n"
        "Включить обратно можно в настройках (/settings).",
        parse_mode="HTML"
    )


@engagement_router.callback_query(lambda c: c.data == "enable_digest")
async def handle_enable_digest(callback_query, state=None):
    """Включить дневной дайджест."""
    from database import DatabaseSession, SniperUser
    from sqlalchemy import select

    user_id = callback_query.from_user.id

    async with DatabaseSession() as session:
        user = await session.scalar(
            select(SniperUser).where(SniperUser.telegram_id == user_id)
        )

        if user:
            current_data = user.data if isinstance(user.data, dict) else {}
            current_data['digest_disabled'] = False
            user.data = current_data
            await session.commit()

    await callback_query.answer("🔔 Дневной дайджест включён")
    await callback_query.message.answer(
        "🔔 <b>Дневной дайджест включён</b>\n\n"
        "Каждое утро в 9:00 МСК вы будете получать сводку по тендерам.",
        parse_mode="HTML"
    )


@engagement_router.callback_query(lambda c: c.data and c.data.startswith("deadline_yes_"))
async def handle_deadline_participating(callback_query, state=None):
    """Пользователь участвует в тендере."""
    await callback_query.answer("✅ Отмечено! Удачи в тендере!")
    await callback_query.message.edit_reply_markup(reply_markup=None)


@engagement_router.callback_query(lambda c: c.data and c.data.startswith("deadline_no_"))
async def handle_deadline_skip(callback_query, state=None):
    """Пользователь не участвует в тендере."""
    await callback_query.answer("Понял, пропускаем этот тендер")
    await callback_query.message.edit_reply_markup(reply_markup=None)


# ============================================
# ЭКСПОРТ
# ============================================

def get_engagement_scheduler(bot_token: str) -> EngagementScheduler:
    """Получить экземпляр планировщика."""
    return EngagementScheduler(bot_token)
