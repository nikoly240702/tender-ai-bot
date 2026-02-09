"""
Subscription Expiration Checker.

Фоновая задача для уведомлений об истечении подписки.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)


class SubscriptionChecker:
    """
    Проверяет подписки и отправляет уведомления об истечении.

    Отправляет напоминания за:
    - 3 дня до истечения
    - 1 день до истечения
    - В день истечения
    """

    # Дни до истечения для отправки напоминаний
    REMINDER_DAYS = [3, 1, 0]

    def __init__(self, bot_token: str, check_interval_hours: int = 6):
        self.bot_token = bot_token
        self.check_interval = check_interval_hours * 3600  # в секундах
        self._running = False
        self._task = None

    async def start(self):
        """Запуск проверки подписок."""
        if self._running:
            return

        self._running = True
        logger.info("🔔 Subscription Checker запущен")

        while self._running:
            try:
                await self._check_expiring_subscriptions()
            except Exception as e:
                logger.error(f"❌ Ошибка проверки подписок: {e}", exc_info=True)

            # Ждём до следующей проверки
            await asyncio.sleep(self.check_interval)

    async def stop(self):
        """Остановка проверки."""
        self._running = False
        logger.info("🛑 Subscription Checker остановлен")

    async def _check_expiring_subscriptions(self):
        """Проверить и уведомить об истекающих подписках."""
        from database import DatabaseSession, SniperUser
        from sqlalchemy import select, and_

        logger.info("🔍 Проверка истекающих подписок...")

        now = datetime.utcnow()
        bot = Bot(token=self.bot_token)

        try:
            async with DatabaseSession() as session:
                # Получаем всех пользователей с активными подписками
                result = await session.execute(
                    select(SniperUser).where(
                        and_(
                            SniperUser.subscription_tier.in_(['trial', 'basic', 'premium']),
                            SniperUser.trial_expires_at.isnot(None)
                        )
                    )
                )
                users = result.scalars().all()

            notified_count = 0

            for user in users:
                if not user.trial_expires_at:
                    continue

                # Вычисляем дни до истечения
                days_left = (user.trial_expires_at - now).days

                # Проверяем, нужно ли отправить напоминание
                if days_left in self.REMINDER_DAYS:
                    # Проверяем что не отправляли сегодня
                    user_data = user.data if isinstance(user.data, dict) else {}
                    last_reminder = user_data.get('last_subscription_reminder', '')
                    today_str = now.strftime('%Y-%m-%d')

                    if last_reminder == today_str:
                        logger.debug(f"⏭️ Напоминание уже отправлено сегодня: user={user.telegram_id}")
                        continue

                    try:
                        await self._send_expiration_reminder(
                            bot=bot,
                            telegram_id=user.telegram_id,
                            tier=user.subscription_tier,
                            expires_at=user.trial_expires_at,
                            days_left=days_left
                        )
                        notified_count += 1

                        # Сохраняем дату отправки
                        async with DatabaseSession() as save_session:
                            from sqlalchemy import select as sel
                            u = await save_session.scalar(
                                sel(SniperUser).where(SniperUser.id == user.id)
                            )
                            if u:
                                d = u.data if isinstance(u.data, dict) else {}
                                d['last_subscription_reminder'] = today_str
                                u.data = d

                        # Небольшая задержка между сообщениями
                        await asyncio.sleep(0.1)

                    except Exception as e:
                        logger.warning(f"Не удалось уведомить пользователя {user.telegram_id}: {e}")

            logger.info(f"✅ Проверка завершена. Отправлено напоминаний: {notified_count}")

        finally:
            await bot.session.close()

    async def _send_expiration_reminder(
        self,
        bot: Bot,
        telegram_id: int,
        tier: str,
        expires_at: datetime,
        days_left: int
    ):
        """Отправить напоминание об истечении подписки."""

        tier_names = {
            'trial': 'Пробный период',
            'basic': 'Basic',
            'premium': 'Premium'
        }
        tier_name = tier_names.get(tier, tier)

        if days_left == 0:
            # Подписка истекает сегодня
            text = (
                f"⚠️ <b>Ваша подписка истекает сегодня!</b>\n\n"
                f"Тариф: <b>{tier_name}</b>\n"
                f"Дата окончания: <b>{expires_at.strftime('%d.%m.%Y')}</b>\n\n"
                f"Продлите подписку, чтобы не потерять доступ к функциям бота."
            )
        elif days_left == 1:
            # Остался 1 день
            text = (
                f"⏰ <b>Подписка истекает завтра!</b>\n\n"
                f"Тариф: <b>{tier_name}</b>\n"
                f"Дата окончания: <b>{expires_at.strftime('%d.%m.%Y')}</b>\n\n"
                f"Не забудьте продлить подписку!"
            )
        else:
            # 3 дня или больше
            text = (
                f"📅 <b>Напоминание о подписке</b>\n\n"
                f"Тариф: <b>{tier_name}</b>\n"
                f"Дата окончания: <b>{expires_at.strftime('%d.%m.%Y')}</b>\n"
                f"Осталось дней: <b>{days_left}</b>\n\n"
                f"Продлите заранее, чтобы не прерывать работу!"
            )

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📦 Продлить подписку", callback_data="subscription_tiers")],
            [InlineKeyboardButton(text="📊 Моя подписка", callback_data="sniper_subscription")]
        ])

        await bot.send_message(
            telegram_id,
            text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )

        logger.info(f"📧 Напоминание отправлено: user={telegram_id}, days_left={days_left}")
