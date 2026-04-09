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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

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
    - Реактивационные сообщения для неактивных пользователей
    """

    # Время отправки дневного дайджеста (МСК)
    DIGEST_HOUR = 9
    DIGEST_MINUTE = 0

    # Время отправки реактивационных сообщений (МСК)
    REACTIVATION_HOUR = 10
    REACTIVATION_MINUTE = 0

    # Параметры реактивации
    REACTIVATION_INACTIVITY_DAYS = 3  # Через сколько дней неактивности отправлять
    REACTIVATION_FREQUENCY_DAYS = 3   # Как часто отправлять (раз в N дней)
    REACTIVATION_MAX_MESSAGES = 10    # Максимум сообщений (~1 месяц)

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

        # Задержка 60 сек после старта — ждём пока старый инстанс остановится
        # Предотвращает дубли follow-up при деплое
        await asyncio.sleep(60)

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

            # 4. Реактивационные сообщения в 10:00 МСК
            if current_hour == self.REACTIVATION_HOUR:
                await self._send_reactivation_messages(bot)

            # 5. Просроченные сделки Битрикс24 → LOSE (8:00 и 14:00 МСК)
            if current_hour in (8, 14):
                await self._move_expired_bitrix24_deals()

            # 6. AI анализ для сделок в стадии "Новые процедуры с AI" без результата
            await self._auto_analyze_bitrix24_ai_deals()

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
                    SniperUser.subscription_tier.in_(['trial', 'starter', 'pro', 'premium', 'basic'])
                )
            )
            users = result.scalars().all()

        followups_sent = 0

        for user in users:
            try:
                # Пропускаем группы — follow-up только для личных пользователей
                if getattr(user, 'is_group', False):
                    continue

                # Перечитываем user.data из БД (защита от дублей при двух инстансах)
                async with DatabaseSession() as _sess:
                    _fresh = await _sess.get(SniperUser, user.id)
                    user_data = (_fresh.data if _fresh and isinstance(_fresh.data, dict) else {}) if _fresh else {}

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
                day2_sent = user_data.get('followup_day2_sent', False)
                day3_sent = user_data.get('followup_day3_sent', False)
                day7_sent = user_data.get('followup_day7_sent', False)
                day12_sent = user_data.get('followup_day12_sent', False)
                day14_sent = user_data.get('followup_day14_sent', False)

                # Только для trial пользователей — напоминания об истечении
                is_trial = getattr(user, 'subscription_tier', '') == 'trial'

                # Хелпер: пометить → отправить → если ошибка, откатить
                async def _send_followup(day_key: str, send_coro):
                    """Mark as sent BEFORE sending to prevent duplicates on restart."""
                    nonlocal followups_sent
                    await self._update_user_data(user.id, {f'followup_{day_key}_sent': True})
                    try:
                        msg = await send_coro
                        logger.info(f"📧 {day_key} follow-up sent to {user.telegram_id}")
                        # Save message_id for possible deletion
                        if msg and hasattr(msg, 'message_id'):
                            await self._update_user_data(user.id, {f'followup_{day_key}_msg_id': msg.message_id})
                        followups_sent += 1
                    except Exception as e:
                        logger.error(f"Failed to send {day_key} follow-up to {user.telegram_id}: {e}")

                # День 1
                if days_since_filter >= 1 and not day1_sent:
                    stats = await get_user_stats(user.telegram_id)
                    await _send_followup('day1', send_day1_followup(bot, user.telegram_id, stats))

                # День 2 — персональное сообщение от основателя
                elif days_since_filter >= 2 and not day2_sent:
                    await _send_followup('day2', bot.send_message(
                        user.telegram_id,
                        "👋 Привет! Я Николай, основатель Tender Sniper.\n\n"
                        "Хотел лично спросить — как вам бот? "
                        "Всё понятно? Нашлись подходящие тендеры?\n\n"
                        "Если есть вопросы или пожелания — напишите мне лично: "
                        "@nikolai_chizhik\n\n"
                        "Хорошего дня! 🙌"
                    ))

                # День 3
                elif days_since_filter >= 3 and not day3_sent:
                    stats = await get_user_stats(user.telegram_id)
                    await _send_followup('day3', send_day3_followup(bot, user.telegram_id, stats))

                # День 7 — половина триала (только trial)
                elif days_since_filter >= 7 and not day7_sent and is_trial:
                    stats = await get_user_stats(user.telegram_id)
                    total = stats.get('total_notifications', 0)
                    hours_saved = max(1, total * 0.5)
                    await _send_followup('day7', bot.send_message(
                        user.telegram_id,
                        f"📊 <b>Неделя с Tender Sniper!</b>\n\n"
                        f"За 7 дней бот нашёл для вас <b>{total}</b> подходящих тендеров.\n"
                        f"Вы сэкономили примерно <b>{hours_saved:.0f} часов</b> на ручном поиске.\n\n"
                        f"⏳ Осталось <b>7 дней</b> пробного периода.\n\n"
                        f"Оформите подписку, чтобы не потерять мониторинг:",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="⭐ Тарифы и подписка", callback_data="subscription_tiers")],
                            [InlineKeyboardButton(text="🎯 Мои фильтры", callback_data="sniper_my_filters")],
                        ]),
                        parse_mode="HTML"
                    ))

                # День 12 — осталось 2 дня (только trial)
                elif days_since_filter >= 12 and not day12_sent and is_trial:
                    stats = await get_user_stats(user.telegram_id)
                    total = stats.get('total_notifications', 0)
                    await _send_followup('day12', bot.send_message(
                        user.telegram_id,
                        f"⚠️ <b>Пробный период заканчивается через 2 дня</b>\n\n"
                        f"За это время бот нашёл <b>{total}</b> тендеров по вашим фильтрам.\n\n"
                        f"После окончания триала мониторинг будет приостановлен.\n"
                        f"Оформите подписку, чтобы продолжить получать уведомления.\n\n"
                        f"💡 Starter — от <b>499 ₽/мес</b> (5 фильтров)",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="⭐ Оформить подписку", callback_data="subscription_tiers")],
                        ]),
                        parse_mode="HTML"
                    ))

                # День 14 — последний день (только trial)
                elif days_since_filter >= 14 and not day14_sent and is_trial:
                    await _send_followup('day14', bot.send_message(
                        user.telegram_id,
                        "🔔 <b>Последний день пробного периода</b>\n\n"
                        "Завтра мониторинг тендеров будет приостановлен.\n\n"
                        "Оформите подписку сейчас, чтобы не пропустить "
                        "ни одного подходящего тендера:\n\n"
                        "📋 <b>Basic</b> — 1 490 ₽/мес (5 фильтров)\n"
                        "🏆 <b>Premium</b> — 2 990 ₽/мес (20 фильтров + AI без лимитов)",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="⭐ Оформить подписку", callback_data="subscription_tiers")],
                            [InlineKeyboardButton(text="💬 Связаться с поддержкой", url="https://t.me/nikolai_chizhik")],
                        ]),
                        parse_mode="HTML"
                    ))

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
                        SniperUser.subscription_tier.in_(['trial', 'starter', 'pro', 'premium', 'basic']),
                        SniperUser.trial_expires_at > datetime.utcnow()  # Активная подписка
                    )
                )
            )
            users = result.scalars().all()

        digests_sent = 0

        for user in users:
            try:
                # Пропускаем группы — уведомления привязаны к user_id владельца фильтра, не группы
                if getattr(user, 'is_group', False):
                    continue

                # Проверяем, включён ли дайджест у пользователя
                user_data = user.data if isinstance(user.data, dict) else {}
                if user_data.get('digest_disabled', False):
                    continue

                # Проверяем тихие часы (даже для дайджеста)
                if user_data.get('quiet_hours_enabled', False):
                    current_hour = (datetime.utcnow() + timedelta(hours=MOSCOW_TZ_OFFSET)).hour
                    quiet_start = user_data.get('quiet_hours_start', 22)
                    quiet_end = user_data.get('quiet_hours_end', 8)

                    # Проверяем, находимся ли в тихих часах
                    if quiet_start > quiet_end:
                        is_quiet = current_hour >= quiet_start or current_hour < quiet_end
                    else:
                        is_quiet = quiet_start <= current_hour < quiet_end

                    if is_quiet:
                        logger.debug(f"Пропускаем дайджест для {user.telegram_id} (тихие часы)")
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

                    # ТОП-3 тендера за вчера (по score)
                    top_tenders_result = await session.execute(
                        select(SniperNotification).where(
                            and_(
                                SniperNotification.user_id == user.id,
                                SniperNotification.sent_at >= yesterday
                            )
                        ).order_by(SniperNotification.score.desc()).limit(3)
                    )
                    top_tenders = top_tenders_result.scalars().all()

                # Не отправляем дайджест если тендеров нет
                if notifications_count == 0:
                    logger.debug(f"Дайджест для {user.telegram_id}: 0 тендеров — пропускаем")
                    continue

                # Формируем список ТОП-3 тендеров
                top_lines = []
                for t in top_tenders:
                    name = (t.tender_name or '')[:60]
                    if len(t.tender_name or '') > 60:
                        name += '…'
                    price_str = ''
                    if t.tender_price:
                        try:
                            price_str = f" · {float(t.tender_price):,.0f} ₽".replace(',', ' ')
                        except Exception:
                            pass
                    link = t.tender_url or f"https://zakupki.gov.ru/epz/order/notice/ea44/view/common-info.html?regNumber={t.tender_number}"
                    top_lines.append(f'• <a href="{link}">{name}</a>{price_str}')

                top_block = '\n'.join(top_lines)

                text = (
                    f"☀️ <b>Доброе утро!</b>\n\n"
                    f"📊 <b>Дайджест за вчера:</b>\n"
                    f"📬 Найдено тендеров: <b>{notifications_count}</b> · "
                    f"🎯 Фильтров: <b>{active_filters}</b>\n\n"
                    f"<b>ТОП-{len(top_tenders)}:</b>\n{top_block}"
                )

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"📋 Все {notifications_count} тендеров", callback_data="alltenders_last_24h")],
                    [InlineKeyboardButton(text="🔕 Отключить дайджест", callback_data="disable_digest")],
                ])

                await bot.send_message(
                    user.telegram_id, text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
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
                        SniperUser.subscription_tier.in_(['trial', 'starter', 'pro', 'premium', 'basic'])
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

                # Проверяем, отключены ли напоминания о дедлайнах
                if user_data.get('deadline_reminders_disabled', False):
                    continue

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

    async def _send_reactivation_messages(self, bot: Bot):
        """
        Сегментированная серия реактивационных сообщений (3 / 7 / 14 дней).

        Три сегмента:
          no_filters  — зарегистрирован, но нет активных фильтров
          no_notifs   — есть фильтры, но 30+ дней без уведомлений
          inactive    — есть фильтры + уведомления, просто не открывает бот

        Для каждого сегмента серия из 3 сообщений: день 3 → 7 → 14.
        Деdупликация через таблицу reactivation_events (event_type уникален на пользователя).
        """
        from database import DatabaseSession, SniperUser, SniperFilter, SniperNotification, ReactivationEvent
        from sqlalchemy import select, func, and_
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        now = datetime.utcnow()
        threshold_3d = now - timedelta(days=3)

        # Все активные личные пользователи, неактивные 3+ дней
        async with DatabaseSession() as session:
            result = await session.execute(
                select(SniperUser).where(
                    and_(
                        SniperUser.status == 'active',
                        SniperUser.is_group == False,
                        SniperUser.last_activity < threshold_3d,
                    )
                )
            )
            users = result.scalars().all()

        sent_count = 0

        for user in users:
            try:
                days_inactive = max(3, (now - user.last_activity).days) if user.last_activity else 3

                # Определяем целевой day-bucket (ближайший достигнутый)
                if days_inactive >= 14:
                    target_bucket = 14
                elif days_inactive >= 7:
                    target_bucket = 7
                else:
                    target_bucket = 3

                # Собираем статистику пользователя
                async with DatabaseSession() as session:
                    filter_count = await session.scalar(
                        select(func.count(SniperFilter.id)).where(
                            and_(
                                SniperFilter.user_id == user.id,
                                SniperFilter.is_active == True,
                                SniperFilter.deleted_at.is_(None),
                            )
                        )
                    ) or 0

                    notif_count = await session.scalar(
                        select(func.count(SniperNotification.id)).where(
                            and_(
                                SniperNotification.user_id == user.id,
                                SniperNotification.sent_at >= now - timedelta(days=30),
                            )
                        )
                    ) or 0

                    # Какие event_type уже отправлены этому пользователю
                    sent_rows = await session.execute(
                        select(ReactivationEvent.event_type).where(
                            ReactivationEvent.user_id == user.id
                        )
                    )
                    sent_types = {row[0] for row in sent_rows}

                # Определяем сегмент
                if filter_count == 0:
                    segment = 'no_filters'
                elif notif_count == 0:
                    segment = 'no_notifs'
                else:
                    segment = 'inactive'

                # Находим следующее неотправленное сообщение в серии
                event_type = None
                for bucket in [3, 7, 14]:
                    et = f'seg_{segment}_{bucket}d'
                    if et not in sent_types and bucket <= target_bucket:
                        event_type = et
                        break

                if not event_type:
                    continue  # Серия закончена для этого пользователя

                # Формируем текст и кнопки
                text, keyboard = self._build_reactivation_message(
                    segment=segment,
                    bucket=int(event_type.split('_')[-1].rstrip('d')),
                    filter_count=filter_count,
                    notif_count=notif_count,
                )

                await bot.send_message(
                    user.telegram_id,
                    text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )

                # Логируем событие
                async with DatabaseSession() as session:
                    session.add(ReactivationEvent(
                        user_id=user.id,
                        event_type=event_type,
                        message_variant=segment,
                    ))

                sent_count += 1
                await asyncio.sleep(0.15)

            except Exception as e:
                logger.warning(f"Реактивация для {user.telegram_id}: {e}")

        if sent_count > 0:
            logger.info(f"🔄 Реактивация: отправлено {sent_count} сообщений")

    def _build_reactivation_message(
        self,
        segment: str,
        bucket: int,
        filter_count: int,
        notif_count: int,
    ):
        """Формирует текст и клавиатуру реактивационного сообщения."""
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        # ── Сегмент A: нет фильтров ──────────────────────────────────────
        if segment == 'no_filters':
            if bucket == 3:
                text = (
                    "👋 <b>Мониторинг ещё не настроен</b>\n\n"
                    "Вы зарегистрировались, но фильтров пока нет — тендеры проходят мимо.\n\n"
                    "Это займёт 2 минуты: укажите ключевые слова (например, <i>«ноутбуки»</i>, "
                    "<i>«ремонт кровли»</i>, <i>«охрана»</i>) и бот начнёт присылать подходящие тендеры."
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🎯 Создать первый фильтр", callback_data="sniper_create_filter")],
                    [InlineKeyboardButton(text="📋 Готовые шаблоны", callback_data="filter_templates")],
                ])
            elif bucket == 7:
                text = (
                    "📈 <b>За эту неделю наши пользователи нашли десятки тендеров</b>\n\n"
                    "А ваш мониторинг ещё не запущен.\n\n"
                    "Настройте первый фильтр — первый результат вы увидите уже через несколько часов."
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🎯 Создать фильтр", callback_data="sniper_create_filter")],
                    [InlineKeyboardButton(text="🔍 Попробовать разовый поиск", callback_data="sniper_new_search")],
                ])
            else:  # 14
                text = (
                    "⏰ <b>Пробный период заканчивается</b>\n\n"
                    "У вас ещё есть время попробовать бота бесплатно.\n\n"
                    "Создайте первый фильтр прямо сейчас — без настроек мы не можем показать,"
                    " насколько это полезно для вашего бизнеса."
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🎯 Создать фильтр", callback_data="sniper_create_filter")],
                    [InlineKeyboardButton(text="💬 Нужна помощь", callback_data="contact_support")],
                ])

        # ── Сегмент B: есть фильтры, нет уведомлений ────────────────────
        elif segment == 'no_notifs':
            if bucket == 3:
                text = (
                    "📭 <b>Фильтры работают, но тендеров нет</b>\n\n"
                    "Возможно, критерии слишком жёсткие. Попробуйте:\n"
                    "• Добавить больше ключевых слов-синонимов\n"
                    "• Расширить диапазон цен\n"
                    "• Убрать ограничения по регионам\n\n"
                    "Или запустите разовый поиск прямо сейчас."
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🎯 Редактировать фильтры", callback_data="sniper_my_filters")],
                    [InlineKeyboardButton(text="🔍 Разовый поиск", callback_data="sniper_new_search")],
                ])
            elif bucket == 7:
                text = (
                    "💡 <b>7 дней без тендеров — давайте разберёмся</b>\n\n"
                    "По похожим запросам другие пользователи получают уведомления.\n\n"
                    "Скорее всего, проблема в ключевых словах или ценовом диапазоне. "
                    "Откорректировать фильтр можно в один клик."
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🎯 Мои фильтры", callback_data="sniper_my_filters")],
                    [InlineKeyboardButton(text="📋 Новый фильтр по шаблону", callback_data="filter_templates")],
                ])
            else:  # 14
                text = (
                    "🔔 <b>Уже 2 недели без уведомлений</b>\n\n"
                    "Разовый поиск поможет сразу понять, есть ли тендеры по вашей теме.\n\n"
                    "Если результаты есть — настроим автоматический мониторинг вместе."
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔍 Разовый поиск сейчас", callback_data="sniper_new_search")],
                    [InlineKeyboardButton(text="🎯 Настроить фильтр", callback_data="sniper_my_filters")],
                ])

        # ── Сегмент C: всё настроено, просто не заходит ─────────────────
        else:  # inactive
            if bucket == 3:
                text = (
                    "👋 <b>Вы давно не заходили — мониторинг работает!</b>\n\n"
                    "Ваши фильтры продолжают искать тендеры в фоне.\n\n"
                    "Загляните — там могут быть интересные предложения с близкими дедлайнами."
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📋 Мои тендеры", callback_data="sniper_all_tenders")],
                    [InlineKeyboardButton(text="🎯 Мои фильтры", callback_data="sniper_my_filters")],
                ])
            elif bucket == 7:
                text = (
                    "📊 <b>Тендеры ждут вашего внимания</b>\n\n"
                    "За эту неделю по вашим фильтрам прошло несколько подходящих тендеров.\n\n"
                    "Некоторые из них скоро закроют приём заявок — успейте проверить!"
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📊 Смотреть тендеры", callback_data="sniper_all_tenders")],
                    [InlineKeyboardButton(text="⏰ Тендеры с дедлайном", callback_data="alltenders_deadline_soon")],
                ])
            else:  # 14
                text = (
                    "🏆 <b>Не упустите выгодные контракты</b>\n\n"
                    "Tender Sniper работает 24/7 и продолжает мониторить рынок.\n\n"
                    "Вернитесь и посмотрите, что нашлось за это время!"
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📊 Смотреть тендеры", callback_data="sniper_all_tenders")],
                    [InlineKeyboardButton(text="🎯 Мои фильтры", callback_data="sniper_my_filters")],
                ])

        return text, keyboard

    async def _move_expired_bitrix24_deals(self):
        """
        Ежедневный джоб (8:00 и 14:00 МСК): перемещает сделки с истёкшим сроком
        подачи из «Новые процедуры» / «Новые процедуры с AI» в «Не берем в работу».

        Для определения истечения используется submission_deadline из БД, а если
        он NULL — CLOSEDATE, полученный из Битрикс24 в том же запросе, которым
        читается текущий этап сделки.

        webhook_url ищется сначала в user.data, потом в env BITRIX24_WEBHOOK
        (нужно чтобы работал для юзеров без явной per-user настройки).
        """
        try:
            import os
            from datetime import datetime
            from tender_sniper.database import get_sniper_db
            from bot.handlers.bitrix24 import update_bitrix24_deal_stage, STAGE_LOSE
            import aiohttp

            default_webhook = os.getenv('BITRIX24_WEBHOOK', '').strip()

            db = await get_sniper_db()
            expired = await db.get_expired_bitrix24_notifications()
            if not expired:
                return

            logger.info(f"🔄 Bitrix24 expired deals check: {len(expired)} candidates")
            moved = 0
            skipped_no_webhook = 0
            skipped_wrong_stage = 0
            skipped_not_expired = 0
            now = datetime.utcnow()

            for notif in expired:
                try:
                    user = await db.get_user_by_id(notif['user_id'])
                    if not user:
                        continue
                    webhook_url = (user.get('data') or {}).get('bitrix24_webhook_url', '') or default_webhook
                    if not webhook_url:
                        skipped_no_webhook += 1
                        continue

                    deal_id = notif['bitrix24_deal_id']
                    db_deadline = notif.get('submission_deadline_raw')  # may be None

                    # Fetch current deal state from Bitrix (stage + CLOSEDATE)
                    webhook_url_slash = webhook_url if webhook_url.endswith('/') else webhook_url + '/'
                    endpoint = webhook_url_slash + 'crm.deal.get.json'
                    current_stage = None
                    bitrix_closedate = None
                    try:
                        async with aiohttp.ClientSession(
                            timeout=aiohttp.ClientTimeout(total=6)
                        ) as session:
                            async with session.post(
                                endpoint, json={'id': deal_id}
                            ) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    result = data.get('result') or {}
                                    current_stage = result.get('STAGE_ID')
                                    closedate_str = result.get('CLOSEDATE') or ''
                                    if closedate_str:
                                        try:
                                            from datetime import timezone
                                            dt = datetime.fromisoformat(closedate_str)
                                            if dt.tzinfo is not None:
                                                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                                            bitrix_closedate = dt
                                        except ValueError:
                                            pass
                    except Exception:
                        pass

                    # Перемещаем только если сделка в начальных этапах
                    initial_stages = {'NEW', 'UC_OZCYR2'}
                    if current_stage and current_stage not in initial_stages:
                        skipped_wrong_stage += 1
                        continue  # Уже продвинута — не трогаем

                    # Если в БД дедлайн NULL — используем CLOSEDATE из Битрикс
                    if db_deadline is None and bitrix_closedate is not None:
                        if bitrix_closedate > now:
                            skipped_not_expired += 1
                            continue
                    elif db_deadline is None and bitrix_closedate is None:
                        # Нет данных ни с одной стороны — пропускаем
                        skipped_not_expired += 1
                        continue

                    ok = await update_bitrix24_deal_stage(webhook_url, deal_id, STAGE_LOSE)
                    if ok:
                        moved += 1

                    await asyncio.sleep(0.2)

                except Exception as e:
                    logger.warning(f"Expired deal {notif.get('bitrix24_deal_id')}: {e}")

            logger.info(
                f"✅ Bitrix24: {moved} moved to LOSE "
                f"(skipped: no_webhook={skipped_no_webhook}, "
                f"wrong_stage={skipped_wrong_stage}, not_expired={skipped_not_expired})"
            )

        except Exception as e:
            logger.error(f"_move_expired_bitrix24_deals error: {e}", exc_info=True)


    async def _auto_analyze_bitrix24_ai_deals(self):
        """
        Каждый час: ищет сделки в стадии «Новые процедуры с AI» (UC_OZCYR2)
        без AI резюме и запускает AI анализ документации.
        """
        try:
            from tender_sniper.database import get_sniper_db
            from bot.handlers.bitrix24 import update_bitrix24_deal_ai_results, STAGE_AI
            from bot.handlers.webapp import _run_ai_analysis
            import aiohttp

            db = await get_sniper_db()
            users = await db.get_users_with_bitrix24()
            if not users:
                return

            logger.info(f"🤖 Bitrix24 AI polling: checking {len(users)} users")
            total_analyzed = 0

            for user in users:
                webhook_url = user['data'].get('bitrix24_webhook_url', '')
                if not webhook_url:
                    continue
                if not webhook_url.endswith('/'):
                    webhook_url += '/'

                # Получаем сделки в UC_OZCYR2 без AI резюме
                try:
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as session:
                        async with session.post(
                            webhook_url + 'crm.deal.list.json',
                            json={
                                'filter': {
                                    'STAGE_ID': STAGE_AI,
                                    'UF_CRM_AI_SUMMARY': '',
                                },
                                'select': ['ID', 'UF_CRM_TENDER_NUMBER', 'UF_CRM_AI_SUMMARY'],
                                'order': {'ID': 'DESC'},
                                'start': 0,
                            }
                        ) as resp:
                            if resp.status != 200:
                                continue
                            data = await resp.json()
                            deals = data.get('result', []) or []
                except Exception as e:
                    logger.debug(f"Bitrix24 deal.list error for user {user['id']}: {e}")
                    continue

                if not deals:
                    continue

                logger.info(f"  User {user['id']}: {len(deals)} deals need AI analysis")

                for deal in deals[:5]:  # Не более 5 за цикл, чтобы не перегружать
                    deal_id = str(deal.get('ID', ''))
                    tender_number = deal.get('UF_CRM_TENDER_NUMBER', '')
                    if not deal_id or not tender_number:
                        continue

                    # Проверяем повторно — AI резюме уже не пустое?
                    if deal.get('UF_CRM_AI_SUMMARY'):
                        continue

                    try:
                        subscription_tier = user.get('subscription_tier', 'trial')
                        formatted, is_ai, extraction = await _run_ai_analysis(
                            tender_number, subscription_tier
                        )
                        await update_bitrix24_deal_ai_results(
                            webhook_url.rstrip('/'), deal_id, extraction, formatted
                        )
                        total_analyzed += 1
                        logger.info(f"  ✅ AI analyzed: deal {deal_id} / tender {tender_number}")
                    except Exception as e:
                        logger.debug(f"  AI analysis failed for deal {deal_id}: {e}")

                    await asyncio.sleep(1)  # Пауза между анализами

            if total_analyzed:
                logger.info(f"✅ Bitrix24 AI polling: {total_analyzed} deals analyzed")

        except Exception as e:
            logger.error(f"_auto_analyze_bitrix24_ai_deals error: {e}", exc_info=True)


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
