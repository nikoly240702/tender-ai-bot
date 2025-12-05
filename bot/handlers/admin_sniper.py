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
import aiosqlite

from bot.config import BotConfig
# SniperDatabase не используется, работаем напрямую с aiosqlite

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
        db_path = Path(__file__).parent.parent.parent / 'tender_sniper' / 'database' / 'sniper.db'

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            # Общая статистика
            async with db.execute("SELECT COUNT(*) as total FROM users") as cursor:
                total_users = (await cursor.fetchone())['total']

            async with db.execute("SELECT COUNT(*) as total FROM filters WHERE active = 1") as cursor:
                active_filters = (await cursor.fetchone())['total']

            async with db.execute("SELECT COUNT(*) as total FROM filters") as cursor:
                total_filters = (await cursor.fetchone())['total']

            # Статистика уведомлений
            async with db.execute("SELECT COUNT(*) as total FROM notifications") as cursor:
                total_notifications = (await cursor.fetchone())['total']

            # Уведомления за сегодня
            today = datetime.now().date().isoformat()
            async with db.execute(
                "SELECT COUNT(*) as total FROM notifications WHERE date(created_at) = ?",
                (today,)
            ) as cursor:
                today_notifications = (await cursor.fetchone())['total']

            # Уведомления за последние 7 дней
            week_ago = (datetime.now() - timedelta(days=7)).date().isoformat()
            async with db.execute(
                "SELECT COUNT(*) as total FROM notifications WHERE date(created_at) >= ?",
                (week_ago,)
            ) as cursor:
                week_notifications = (await cursor.fetchone())['total']

            # Топ-3 пользователя по уведомлениям
            async with db.execute("""
                SELECT u.telegram_id, u.subscription_tier, COUNT(n.id) as notif_count
                FROM notifications n
                JOIN users u ON n.user_id = u.id
                GROUP BY u.id
                ORDER BY notif_count DESC
                LIMIT 3
            """) as cursor:
                top_users = await cursor.fetchall()

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
                text += f"  {i}. ID {user['telegram_id']} ({user['subscription_tier']}): {user['notif_count']} уведомлений\n"

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
        db_path = Path(__file__).parent.parent.parent / 'tender_sniper' / 'database' / 'sniper.db'

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute("""
                SELECT f.id, f.name, f.keywords, f.price_min, f.price_max,
                       u.telegram_id, u.subscription_tier,
                       COUNT(n.id) as notifications_count
                FROM filters f
                JOIN users u ON f.user_id = u.id
                LEFT JOIN notifications n ON f.id = n.filter_id
                WHERE f.active = 1
                GROUP BY f.id
                ORDER BY notifications_count DESC
                LIMIT 10
            """) as cursor:
                filters = await cursor.fetchall()

        if not filters:
            await callback.message.answer("ℹ️ Нет активных фильтров")
            return

        text = "🎯 <b>Активные фильтры (топ-10):</b>\n\n"

        for f in filters:
            import json
            keywords = json.loads(f['keywords']) if f['keywords'] else []
            keywords_str = ', '.join(keywords[:3])
            if len(keywords) > 3:
                keywords_str += f" (+{len(keywords)-3})"

            price = f"{f['price_min']:,} - {f['price_max']:,}" if f['price_min'] and f['price_max'] else "Не указана"

            text += (
                f"<b>{f['name']}</b>\n"
                f"  ID: {f['id']} | User: {f['telegram_id']} ({f['subscription_tier']})\n"
                f"  Ключевые слова: {keywords_str}\n"
                f"  Цена: {price}\n"
                f"  Уведомлений: {f['notifications_count']}\n\n"
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
        db_path = Path(__file__).parent.parent.parent / 'tender_sniper' / 'database' / 'sniper.db'

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute("""
                SELECT
                    u.telegram_id,
                    u.subscription_tier,
                    COUNT(DISTINCT f.id) as filters_count,
                    COUNT(DISTINCT CASE WHEN f.active = 1 THEN f.id END) as active_filters,
                    COUNT(n.id) as total_notifications,
                    COUNT(CASE WHEN date(n.created_at) = date('now') THEN 1 END) as today_notifications
                FROM users u
                LEFT JOIN filters f ON u.id = f.user_id
                LEFT JOIN notifications n ON u.id = n.user_id
                GROUP BY u.id
                ORDER BY total_notifications DESC
                LIMIT 15
            """) as cursor:
                users = await cursor.fetchall()

        if not users:
            await callback.message.answer("ℹ️ Нет пользователей")
            return

        text = "👥 <b>Пользователи и тарифы:</b>\n\n"

        for user in users:
            tier_emoji = {
                'free': '🆓',
                'basic': '💼',
                'premium': '👑'
            }.get(user['subscription_tier'], '❓')

            text += (
                f"{tier_emoji} <b>User {user['telegram_id']}</b> ({user['subscription_tier']})\n"
                f"  Фильтры: {user['active_filters']}/{user['filters_count']}\n"
                f"  Уведомления: {user['today_notifications']} сегодня / {user['total_notifications']} всего\n\n"
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
        db_path = Path(__file__).parent.parent.parent / 'tender_sniper' / 'database' / 'sniper.db'

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            # Последние уведомления
            async with db.execute("""
                SELECT
                    n.created_at,
                    u.telegram_id,
                    f.name as filter_name
                FROM notifications n
                JOIN users u ON n.user_id = u.id
                JOIN filters f ON n.filter_id = f.id
                ORDER BY n.created_at DESC
                LIMIT 5
            """) as cursor:
                recent_notifications = await cursor.fetchall()

            # Статистика по часам (последние 24 часа)
            async with db.execute("""
                SELECT
                    strftime('%H:00', created_at) as hour,
                    COUNT(*) as count
                FROM notifications
                WHERE created_at >= datetime('now', '-24 hours')
                GROUP BY hour
                ORDER BY hour DESC
                LIMIT 6
            """) as cursor:
                hourly_stats = await cursor.fetchall()

        text = "📈 <b>Мониторинг системы</b>\n\n"

        if hourly_stats:
            text += "<b>Активность по часам (последние 6 часов):</b>\n"
            for stat in hourly_stats:
                text += f"  {stat['hour']}: {stat['count']} уведомлений\n"
            text += "\n"

        if recent_notifications:
            text += "<b>Последние 5 уведомлений:</b>\n"
            for notif in recent_notifications:
                time = notif['created_at'].split('.')[0]  # Убираем миллисекунды
                text += f"  • {time} - User {notif['telegram_id']} ({notif['filter_name']})\n"

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
        db_path = Path(__file__).parent.parent.parent / 'tender_sniper' / 'database' / 'sniper.db'

        async with aiosqlite.connect(db_path) as db:
            # Сбрасываем квоты
            await db.execute("""
                UPDATE users
                SET notifications_today = 0, last_notification_date = NULL
            """)
            await db.commit()

            async with db.execute("SELECT COUNT(*) as total FROM users") as cursor:
                total = (await cursor.fetchone())['total']

        await callback.message.answer(
            f"✅ <b>Квоты сброшены</b>\n\n"
            f"Дневные квоты {total} пользователей сброшены до нуля.",
            parse_mode="HTML"
        )

        logger.info(f"Админ {callback.from_user.id} сбросил квоты для {total} пользователей")

    except Exception as e:
        logger.error(f"Ошибка сброса квот: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка сброса квот")
