"""
Расширенная админ-панель для Tender Sniper.

Функционал:
- Статистика по уведомлениям
- Просмотр активных фильтров
- Управление тарифами пользователей
- Мониторинг системы
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

# Добавляем путь к корневой директории
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
import logging
from sqlalchemy import select, func, and_, distinct
from sqlalchemy.sql import text

from bot.config import BotConfig
from database import (
    SniperUser,
    SniperFilter,
    SniperNotification,
    DatabaseSession
)

logger = logging.getLogger(__name__)
router = Router()


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    return BotConfig.ADMIN_USER_ID and user_id == BotConfig.ADMIN_USER_ID


def get_sniper_admin_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура админ-панели Tender Sniper."""
    buttons = [
        [InlineKeyboardButton(text="📊 Статистика", callback_data="sniper_admin_stats")],
        [InlineKeyboardButton(text="🎯 Активные фильтры", callback_data="sniper_admin_filters")],
        [InlineKeyboardButton(text="👥 Пользователи и тарифы", callback_data="sniper_admin_users")],
        [InlineKeyboardButton(text="📈 Мониторинг системы", callback_data="sniper_admin_monitoring")],
        [InlineKeyboardButton(text="🔄 Сбросить квоты (сегодня)", callback_data="sniper_admin_reset_quotas")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("sniper_admin"))
async def sniper_admin_panel(message: Message):
    """
    Открывает админ-панель Tender Sniper.
    Доступна только администратору.
    """
    if not is_admin(message.from_user.id):
        await message.answer(
            "❌ У вас нет доступа к админ-панели Tender Sniper.\n\n"
            f"Ваш User ID: `{message.from_user.id}`",
            parse_mode="Markdown"
        )
        return

    await message.answer(
        "👑 <b>Админ-панель Tender Sniper</b>\n\n"
        "Управление системой автоматического мониторинга тендеров.\n\n"
        "Выберите действие:",
        reply_markup=get_sniper_admin_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "sniper_admin_stats")
async def show_statistics(callback: CallbackQuery):
    """Показывает общую статистику системы."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    await callback.answer()

    try:
        async with DatabaseSession() as session:
            # Общая статистика
            total_users = await session.scalar(select(func.count(SniperUser.id)))
            active_filters = await session.scalar(
                select(func.count(SniperFilter.id)).where(SniperFilter.is_active == True)
            )
            total_filters = await session.scalar(select(func.count(SniperFilter.id)))
            total_notifications = await session.scalar(select(func.count(SniperNotification.id)))

            # Уведомления за сегодня
            today = datetime.now().date()
            today_notifications = await session.scalar(
                select(func.count(SniperNotification.id)).where(
                    func.date(SniperNotification.sent_at) == today
                )
            )

            # Уведомления за последние 7 дней
            week_ago = datetime.now() - timedelta(days=7)
            week_notifications = await session.scalar(
                select(func.count(SniperNotification.id)).where(
                    SniperNotification.sent_at >= week_ago
                )
            )

            # Топ-3 пользователя по уведомлениям
            top_users_query = (
                select(
                    SniperUser.telegram_id,
                    SniperUser.subscription_tier,
                    func.count(SniperNotification.id).label('notif_count')
                )
                .join(SniperNotification, SniperNotification.user_id == SniperUser.id)
                .group_by(SniperUser.id)
                .order_by(func.count(SniperNotification.id).desc())
                .limit(3)
            )
            top_users_result = await session.execute(top_users_query)
            top_users = top_users_result.all()

        text = (
            "📊 <b>Статистика Tender Sniper</b>\n\n"
            f"👥 <b>Пользователи:</b> {total_users}\n"
            f"🎯 <b>Активные фильтры:</b> {active_filters} / {total_filters}\n\n"
            f"📬 <b>Уведомления:</b>\n"
            f"  • Всего: {total_notifications}\n"
            f"  • Сегодня: {today_notifications}\n"
            f"  • За неделю: {week_notifications}\n\n"
        )

        if top_users:
            text += "<b>🏆 Топ-3 пользователя:</b>\n"
            for i, user in enumerate(top_users, 1):
                text += f"  {i}. ID {user.telegram_id} ({user.subscription_tier}): {user.notif_count} уведомлений\n"

        await callback.message.answer(text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка получения статистики")


@router.callback_query(F.data == "sniper_admin_filters")
async def show_active_filters(callback: CallbackQuery):
    """Показывает список всех активных фильтров."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    await callback.answer()

    try:
        async with DatabaseSession() as session:
            query = (
                select(
                    SniperFilter.id,
                    SniperFilter.name,
                    SniperFilter.keywords,
                    SniperFilter.price_min,
                    SniperFilter.price_max,
                    SniperUser.telegram_id,
                    SniperUser.subscription_tier,
                    func.count(SniperNotification.id).label('notifications_count')
                )
                .join(SniperUser, SniperFilter.user_id == SniperUser.id)
                .outerjoin(SniperNotification, SniperFilter.id == SniperNotification.filter_id)
                .where(SniperFilter.is_active == True)
                .group_by(SniperFilter.id, SniperUser.telegram_id, SniperUser.subscription_tier)
                .order_by(func.count(SniperNotification.id).desc())
                .limit(10)
            )
            result = await session.execute(query)
            filters = result.all()

        if not filters:
            await callback.message.answer("ℹ️ Нет активных фильтров")
            return

        text = "🎯 <b>Активные фильтры (топ-10):</b>\n\n"

        for f in filters:
            import json
            keywords = f.keywords if isinstance(f.keywords, list) else json.loads(f.keywords) if f.keywords else []
            keywords_str = ', '.join(keywords[:3])
            if len(keywords) > 3:
                keywords_str += f" (+{len(keywords)-3})"

            price = f"{f.price_min:,} - {f.price_max:,}" if f.price_min and f.price_max else "Не указана"

            text += (
                f"<b>{f.name}</b>\n"
                f"  ID: {f.id} | User: {f.telegram_id} ({f.subscription_tier})\n"
                f"  Ключевые слова: {keywords_str}\n"
                f"  Цена: {price}\n"
                f"  Уведомлений: {f.notifications_count}\n\n"
            )

        await callback.message.answer(text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка получения фильтров: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка получения фильтров")


@router.callback_query(F.data == "sniper_admin_users")
async def show_users_and_tiers(callback: CallbackQuery):
    """Показывает пользователей и их тарифы."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    await callback.answer()

    try:
        async with DatabaseSession() as session:
            query = (
                select(
                    SniperUser.telegram_id,
                    SniperUser.subscription_tier,
                    func.count(distinct(SniperFilter.id)).label('filters_count'),
                    func.count(distinct(
                        SniperFilter.id
                    )).filter(SniperFilter.is_active == True).label('active_filters'),
                    func.count(SniperNotification.id).label('total_notifications'),
                    func.count(SniperNotification.id).filter(
                        func.date(SniperNotification.sent_at) == datetime.now().date()
                    ).label('today_notifications')
                )
                .outerjoin(SniperFilter, SniperUser.id == SniperFilter.user_id)
                .outerjoin(SniperNotification, SniperUser.id == SniperNotification.user_id)
                .group_by(SniperUser.id)
                .order_by(func.count(SniperNotification.id).desc())
                .limit(15)
            )
            result = await session.execute(query)
            users = result.all()

        if not users:
            await callback.message.answer("ℹ️ Нет пользователей")
            return

        text = "👥 <b>Пользователи и тарифы:</b>\n\n"

        for user in users:
            tier_emoji = {
                'free': '🆓',
                'basic': '💼',
                'premium': '👑'
            }.get(user.subscription_tier, '❓')

            text += (
                f"{tier_emoji} <b>User {user.telegram_id}</b> ({user.subscription_tier})\n"
                f"  Фильтры: {user.active_filters or 0}/{user.filters_count or 0}\n"
                f"  Уведомления: {user.today_notifications or 0} сегодня / {user.total_notifications or 0} всего\n\n"
            )

        await callback.message.answer(text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка получения пользователей: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка получения пользователей")


@router.callback_query(F.data == "sniper_admin_monitoring")
async def show_system_monitoring(callback: CallbackQuery):
    """Показывает мониторинг системы."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    await callback.answer()

    try:
        async with DatabaseSession() as session:
            # Последние уведомления
            recent_query = (
                select(
                    SniperNotification.sent_at,
                    SniperUser.telegram_id,
                    SniperFilter.name.label('filter_name')
                )
                .join(SniperUser, SniperNotification.user_id == SniperUser.id)
                .join(SniperFilter, SniperNotification.filter_id == SniperFilter.id)
                .order_by(SniperNotification.sent_at.desc())
                .limit(5)
            )
            recent_result = await session.execute(recent_query)
            recent_notifications = recent_result.all()

            # Статистика по часам (последние 24 часа)
            hourly_query = text("""
                SELECT
                    TO_CHAR(sent_at, 'HH24:00') as hour,
                    COUNT(*) as count
                FROM sniper_notifications
                WHERE sent_at >= NOW() - INTERVAL '24 hours'
                GROUP BY TO_CHAR(sent_at, 'HH24:00')
                ORDER BY hour DESC
                LIMIT 6
            """)
            hourly_result = await session.execute(hourly_query)
            hourly_stats = hourly_result.all()

        text = "📈 <b>Мониторинг системы</b>\n\n"

        if hourly_stats:
            text += "<b>Активность по часам (последние 6 часов):</b>\n"
            for stat in hourly_stats:
                text += f"  {stat.hour}: {stat.count} уведомлений\n"
            text += "\n"

        if recent_notifications:
            text += "<b>Последние 5 уведомлений:</b>\n"
            for notif in recent_notifications:
                time = notif.sent_at.strftime('%Y-%m-%d %H:%M:%S')
                text += f"  • {time} - User {notif.telegram_id} ({notif.filter_name})\n"

        await callback.message.answer(text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка мониторинга: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка мониторинга")


@router.callback_query(F.data == "sniper_admin_reset_quotas")
async def reset_daily_quotas(callback: CallbackQuery):
    """Сбрасывает дневные квоты всех пользователей."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    await callback.answer()

    try:
        async with DatabaseSession() as session:
            # Сбрасываем квоты
            from sqlalchemy import update
            await session.execute(
                update(SniperUser).values(
                    notifications_sent_today=0,
                    last_notification_reset=datetime.utcnow()
                )
            )

            total = await session.scalar(select(func.count(SniperUser.id)))

        await callback.message.answer(
            f"✅ <b>Квоты сброшены</b>\n\n"
            f"Дневные квоты {total} пользователей сброшены до нуля.",
            parse_mode="HTML"
        )

        logger.info(f"Админ {callback.from_user.id} сбросил квоты для {total} пользователей")

    except Exception as e:
        logger.error(f"Ошибка сброса квот: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка сброса квот")
