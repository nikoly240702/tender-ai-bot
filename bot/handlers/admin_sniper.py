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
from sqlalchemy import select, func, and_, distinct, update, delete, text

from bot.config import BotConfig
from database import (
    SniperUser,
    SniperFilter,
    SniperNotification,
    ReactivationEvent,
    UserFeedback,
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
        [InlineKeyboardButton(text="⚙️ Управление тарифами", callback_data="sniper_admin_manage_tiers")],
        [InlineKeyboardButton(text="🚫 Управление блокировками", callback_data="sniper_admin_manage_blocks")],
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

            # Реактивация за последние 7 дней
            try:
                reactivation_sent = await session.scalar(
                    select(func.count(ReactivationEvent.id)).where(
                        ReactivationEvent.event_type == 'sent',
                        ReactivationEvent.created_at >= week_ago
                    )
                ) or 0
            except Exception:
                reactivation_sent = 0

            # Feedback за последние 7 дней
            try:
                feedback_interesting = await session.scalar(
                    select(func.count(UserFeedback.id)).where(
                        UserFeedback.feedback_type == 'interesting',
                        UserFeedback.created_at >= week_ago
                    )
                ) or 0
                feedback_hidden = await session.scalar(
                    select(func.count(UserFeedback.id)).where(
                        UserFeedback.feedback_type == 'hidden',
                        UserFeedback.created_at >= week_ago
                    )
                ) or 0
            except Exception:
                feedback_interesting = 0
                feedback_hidden = 0

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
            f"🔄 <b>Engagement (неделя):</b>\n"
            f"  • Реактивации: {reactivation_sent}\n"
            f"  • Feedback +: {feedback_interesting} | -: {feedback_hidden}\n\n"
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
                    SniperUser.data,
                    SniperUser.is_group,
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
                'trial': '🎁',
                'starter': '⭐',
                'pro': '🚀',
                'premium': '👑'
            }.get(user.subscription_tier, '❓')

            # Статус: группа, заблокировал бота, или обычный
            user_data = user.data if isinstance(user.data, dict) else {}
            status_flags = []
            if getattr(user, 'is_group', False):
                status_flags.append("👥 группа")
            if user_data.get('bot_blocked'):
                status_flags.append("⛔ бот заблокирован")
            status_str = f" [{', '.join(status_flags)}]" if status_flags else ""

            text += (
                f"{tier_emoji} <b>User {user.telegram_id}</b> ({user.subscription_tier}){status_str}\n"
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
            # Упрощенный подход: группируем по часам в Python
            twenty_four_hours_ago = datetime.now() - timedelta(hours=24)
            recent_24h_query = (
                select(SniperNotification.sent_at)
                .where(SniperNotification.sent_at >= twenty_four_hours_ago)
                .order_by(SniperNotification.sent_at.desc())
            )
            result_24h = await session.execute(recent_24h_query)
            notifications_24h = result_24h.scalars().all()

            # Группируем по часам в Python
            from collections import defaultdict
            hourly_counts = defaultdict(int)
            for sent_at in notifications_24h:
                hour_key = sent_at.strftime('%H:00')
                hourly_counts[hour_key] += 1

            # Берем последние 6 часов
            hourly_stats = [
                {'hour': hour, 'count': count}
                for hour, count in sorted(hourly_counts.items(), reverse=True)[:6]
            ]

        # Получаем общую статистику для статуса
        total_users = await session.scalar(select(func.count(SniperUser.id)))
        active_filters = await session.scalar(
            select(func.count(SniperFilter.id)).where(SniperFilter.is_active == True)
        )

        text = (
            "📈 <b>Мониторинг системы</b>\n\n"
            f"<b>Статус:</b> ✅ Работает\n"
            f"<b>Пользователей:</b> {total_users}\n"
            f"<b>Активных фильтров:</b> {active_filters}\n\n"
        )

        if hourly_stats:
            text += "<b>Активность по часам (последние 6 часов):</b>\n"
            for stat in hourly_stats:
                text += f"  {stat['hour']}: {stat['count']} уведомлений\n"
            text += "\n"
        else:
            text += "ℹ️ Нет уведомлений за последние 24 часа\n\n"

        if recent_notifications:
            text += "<b>Последние 5 уведомлений:</b>\n"
            for notif in recent_notifications:
                time = notif.sent_at.strftime('%Y-%m-%d %H:%M:%S')
                text += f"  • {time} - User {notif.telegram_id} ({notif.filter_name})\n"
        else:
            text += "ℹ️ Уведомлений пока нет\n"

        await callback.message.answer(text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка мониторинга: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка мониторинга")


@router.callback_query(F.data == "sniper_admin_manage_tiers")
async def manage_user_tiers(callback: CallbackQuery):
    """Управление тарифами пользователей."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    await callback.answer()

    try:
        async with DatabaseSession() as session:
            # Получаем всех пользователей
            query = (
                select(
                    SniperUser.id,
                    SniperUser.telegram_id,
                    SniperUser.username,
                    SniperUser.subscription_tier
                )
                .order_by(SniperUser.created_at.desc())
            )
            result = await session.execute(query)
            users = result.all()

        if not users:
            await callback.message.answer("ℹ️ Нет пользователей в системе")
            return

        text = (
            "⚙️ <b>Управление тарифами</b>\n\n"
            "Для изменения тарифа пользователя отправьте команду:\n"
            "<code>/set_tier USER_ID TIER</code>\n\n"
            "<b>Доступные тарифы:</b>\n"
            "• <code>trial</code> - Пробный (3 фильтра, 20 уведомлений/день)\n"
            "• <code>starter</code> - Starter (5 фильтров, 50 уведомлений/день)\n"
            "• <code>pro</code> - Pro (15 фильтров, безлимит, 50 GPT)\n"
            "• <code>premium</code> - Business (30 фильтров, безлимит, 200 GPT)\n\n"
            "<b>Пользователи:</b>\n\n"
        )

        tier_emoji = {
            'trial': '🎁',
            'starter': '⭐',
            'pro': '🚀',
            'premium': '👑'
        }

        for user in users[:20]:  # Показываем первых 20
            emoji = tier_emoji.get(user.subscription_tier, '❓')
            username = f"@{user.username}" if user.username else "нет username"
            text += f"{emoji} ID: <code>{user.telegram_id}</code> ({username}) - {user.subscription_tier}\n"

        text += (
            "\n<b>Пример:</b>\n"
            f"<code>/set_tier {users[0].telegram_id if users else '123456789'} premium</code>"
        )

        await callback.message.answer(text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка управления тарифами: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка получения пользователей")


@router.message(Command("set_tier"))
async def set_user_tier(message: Message):
    """Установка тарифа пользователю."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return

    try:
        # Парсим команду: /set_tier USER_ID TIER
        parts = message.text.split()
        if len(parts) != 3:
            await message.answer(
                "❌ Неверный формат команды\n\n"
                "Используйте: <code>/set_tier USER_ID TIER</code>\n"
                "Пример: <code>/set_tier 123456789 premium</code>",
                parse_mode="HTML"
            )
            return

        try:
            target_telegram_id = int(parts[1])
        except ValueError:
            await message.answer("❌ USER_ID должен быть числом")
            return

        new_tier = parts[2].lower()
        valid_tiers = ['trial', 'starter', 'pro', 'premium']

        if new_tier not in valid_tiers:
            await message.answer(
                f"❌ Неверный тариф. Доступны: {', '.join(valid_tiers)}"
            )
            return

        # Обновляем тариф
        async with DatabaseSession() as session:
            # Проверяем существование пользователя
            user_query = select(SniperUser).where(SniperUser.telegram_id == target_telegram_id)
            result = await session.execute(user_query)
            user = result.scalar_one_or_none()

            if not user:
                await message.answer(
                    f"❌ Пользователь с ID <code>{target_telegram_id}</code> не найден",
                    parse_mode="HTML"
                )
                return

            old_tier = user.subscription_tier

            # Обновляем тариф и лимиты
            limits_map = {
                'trial': {'filters': 3, 'notifications': 20, 'days': 7},
                'starter': {'filters': 5, 'notifications': 50, 'days': 30},
                'pro': {'filters': 15, 'notifications': 9999, 'days': 30},
                'premium': {'filters': 30, 'notifications': 9999, 'days': 30}
            }

            new_limits = limits_map[new_tier]

            # Вычисляем дату окончания подписки
            if user.trial_expires_at and user.trial_expires_at > datetime.now():
                # Если есть активная подписка - добавляем к ней
                new_expires = user.trial_expires_at + timedelta(days=new_limits['days'])
            else:
                # Иначе от сегодня
                new_expires = datetime.now() + timedelta(days=new_limits['days'])

            await session.execute(
                update(SniperUser)
                .where(SniperUser.telegram_id == target_telegram_id)
                .values(
                    subscription_tier=new_tier,
                    filters_limit=new_limits['filters'],
                    notifications_limit=new_limits['notifications'],
                    trial_expires_at=new_expires
                )
            )

        tier_emoji = {
            'trial': '🎁',
            'starter': '⭐',
            'pro': '🚀',
            'premium': '👑'
        }

        expires_text = ""
        if new_expires:
            expires_text = f"\n• Действует до: {new_expires.strftime('%d.%m.%Y')}"

        await message.answer(
            f"✅ <b>Тариф изменен</b>\n\n"
            f"Пользователь: <code>{target_telegram_id}</code>\n"
            f"Было: {tier_emoji.get(old_tier, '❓')} {old_tier}\n"
            f"Стало: {tier_emoji.get(new_tier, '❓')} {new_tier}\n\n"
            f"Новые лимиты:\n"
            f"• Фильтры: {new_limits['filters']}\n"
            f"• Уведомления/день: {new_limits['notifications']}"
            f"{expires_text}",
            parse_mode="HTML"
        )

        logger.info(
            f"Админ {message.from_user.id} изменил тариф пользователя {target_telegram_id}: "
            f"{old_tier} → {new_tier}"
        )

    except Exception as e:
        logger.error(f"Ошибка изменения тарифа: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка при изменении тарифа")


@router.message(Command("cleanup_duplicates"))
async def cleanup_duplicate_filters(message: Message):
    """Удаление дубликатов фильтров (одинаковые имя + user_id)."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return

    try:
        async with DatabaseSession() as session:
            # Находим дубликаты: группируем по user_id + name и оставляем только ID старейшего
            duplicates_query = text("""
                WITH duplicates AS (
                    SELECT
                        id,
                        user_id,
                        name,
                        ROW_NUMBER() OVER (
                            PARTITION BY user_id, name
                            ORDER BY created_at ASC
                        ) as row_num
                    FROM sniper_filters
                )
                SELECT id FROM duplicates WHERE row_num > 1
            """)

            result = await session.execute(duplicates_query)
            duplicate_ids = [row[0] for row in result.all()]

            if not duplicate_ids:
                await message.answer("✅ Дубликатов не найдено")
                return

            # Удаляем дубликаты
            await session.execute(
                delete(SniperFilter).where(SniperFilter.id.in_(duplicate_ids))
            )

            await message.answer(
                f"✅ <b>Дубликаты удалены</b>\n\n"
                f"Удалено фильтров: {len(duplicate_ids)}",
                parse_mode="HTML"
            )

            logger.info(f"Админ {message.from_user.id} удалил {len(duplicate_ids)} дубликатов фильтров")

    except Exception as e:
        logger.error(f"Ошибка удаления дубликатов: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка при удалении дубликатов")


# ============================================
# УПРАВЛЕНИЕ БЛОКИРОВКАМИ
# ============================================

@router.callback_query(F.data == "sniper_admin_manage_blocks")
async def manage_user_blocks(callback: CallbackQuery):
    """Управление блокировками пользователей."""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return

    await callback.answer()

    try:
        async with DatabaseSession() as session:
            # Получаем заблокированных пользователей
            blocked_query = (
                select(SniperUser)
                .where(SniperUser.status == 'blocked')
                .order_by(SniperUser.blocked_at.desc())
            )
            result = await session.execute(blocked_query)
            blocked_users = result.scalars().all()

        text = (
            "🚫 <b>Управление блокировками</b>\n\n"
            "<b>Команды:</b>\n"
            "• <code>/block USER_ID причина</code> - заблокировать пользователя\n"
            "• <code>/unblock USER_ID</code> - разблокировать пользователя\n\n"
        )

        if blocked_users:
            text += f"<b>Заблокированные пользователи ({len(blocked_users)}):</b>\n\n"
            for user in blocked_users[:15]:  # Показываем первых 15
                username = f"@{user.username}" if user.username else "нет username"
                full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Без имени"
                blocked_at = user.blocked_at.strftime('%Y-%m-%d %H:%M') if user.blocked_at else "?"
                reason = user.blocked_reason or "Не указана"

                text += (
                    f"🔒 <b>{full_name}</b> ({username})\n"
                    f"   ID: <code>{user.telegram_id}</code>\n"
                    f"   Заблокирован: {blocked_at}\n"
                    f"   Причина: {reason}\n\n"
                )
        else:
            text += "✅ Нет заблокированных пользователей\n"

        text += (
            "\n<b>Примеры:</b>\n"
            "<code>/block 123456789 Спам</code>\n"
            "<code>/unblock 123456789</code>"
        )

        await callback.message.answer(text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка получения заблокированных: {e}", exc_info=True)
        await callback.message.answer("❌ Ошибка получения данных")


@router.message(Command("block"))
async def block_user(message: Message):
    """Заблокировать пользователя."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return

    try:
        # Парсим команду: /block USER_ID причина
        parts = message.text.split(maxsplit=2)
        if len(parts) < 2:
            await message.answer(
                "❌ Неверный формат команды\n\n"
                "Используйте: <code>/block USER_ID причина</code>\n"
                "Пример: <code>/block 123456789 Спам</code>",
                parse_mode="HTML"
            )
            return

        try:
            target_telegram_id = int(parts[1])
        except ValueError:
            await message.answer("❌ USER_ID должен быть числом")
            return

        reason = parts[2] if len(parts) > 2 else "Не указана"

        # Блокируем пользователя
        async with DatabaseSession() as session:
            # Проверяем существование пользователя
            user_query = select(SniperUser).where(SniperUser.telegram_id == target_telegram_id)
            result = await session.execute(user_query)
            user = result.scalar_one_or_none()

            if not user:
                await message.answer(
                    f"❌ Пользователь с ID <code>{target_telegram_id}</code> не найден",
                    parse_mode="HTML"
                )
                return

            if user.status == 'blocked':
                await message.answer(
                    f"⚠️ Пользователь <code>{target_telegram_id}</code> уже заблокирован",
                    parse_mode="HTML"
                )
                return

            # Блокируем
            await session.execute(
                update(SniperUser)
                .where(SniperUser.telegram_id == target_telegram_id)
                .values(
                    status='blocked',
                    blocked_reason=reason,
                    blocked_at=datetime.now(),
                    blocked_by=message.from_user.id
                )
            )

        username = f"@{user.username}" if user.username else "нет username"
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Без имени"

        await message.answer(
            f"🔒 <b>Пользователь заблокирован</b>\n\n"
            f"Имя: {full_name}\n"
            f"Username: {username}\n"
            f"ID: <code>{target_telegram_id}</code>\n"
            f"Причина: {reason}",
            parse_mode="HTML"
        )

        logger.info(f"Админ {message.from_user.id} заблокировал пользователя {target_telegram_id}: {reason}")

    except Exception as e:
        logger.error(f"Ошибка блокировки: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка при блокировке")


@router.message(Command("unblock"))
async def unblock_user(message: Message):
    """Разблокировать пользователя."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return

    try:
        # Парсим команду: /unblock USER_ID
        parts = message.text.split()
        if len(parts) != 2:
            await message.answer(
                "❌ Неверный формат команды\n\n"
                "Используйте: <code>/unblock USER_ID</code>\n"
                "Пример: <code>/unblock 123456789</code>",
                parse_mode="HTML"
            )
            return

        try:
            target_telegram_id = int(parts[1])
        except ValueError:
            await message.answer("❌ USER_ID должен быть числом")
            return

        # Разблокируем пользователя
        async with DatabaseSession() as session:
            # Проверяем существование пользователя
            user_query = select(SniperUser).where(SniperUser.telegram_id == target_telegram_id)
            result = await session.execute(user_query)
            user = result.scalar_one_or_none()

            if not user:
                await message.answer(
                    f"❌ Пользователь с ID <code>{target_telegram_id}</code> не найден",
                    parse_mode="HTML"
                )
                return

            if user.status != 'blocked':
                await message.answer(
                    f"⚠️ Пользователь <code>{target_telegram_id}</code> не заблокирован",
                    parse_mode="HTML"
                )
                return

            # Разблокируем
            await session.execute(
                update(SniperUser)
                .where(SniperUser.telegram_id == target_telegram_id)
                .values(
                    status='active',
                    blocked_reason=None,
                    blocked_at=None,
                    blocked_by=None
                )
            )

        username = f"@{user.username}" if user.username else "нет username"
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Без имени"

        await message.answer(
            f"🔓 <b>Пользователь разблокирован</b>\n\n"
            f"Имя: {full_name}\n"
            f"Username: {username}\n"
            f"ID: <code>{target_telegram_id}</code>",
            parse_mode="HTML"
        )

        logger.info(f"Админ {message.from_user.id} разблокировал пользователя {target_telegram_id}")

    except Exception as e:
        logger.error(f"Ошибка разблокировки: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка при разблокировке")


@router.message(Command("test_sheets"))
async def test_sheets_command(message: Message):
    """Тест Google Sheets: берёт 4 последних тендера и отправляет в таблицу с AI обогащением."""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет доступа")
        return

    from database import GoogleSheetsConfig

    status_msg = await message.answer("🔄 Запускаю тест Google Sheets...")

    try:
        async with DatabaseSession() as session:
            user = await session.scalar(
                select(SniperUser).where(SniperUser.telegram_id == message.from_user.id)
            )
            if not user:
                await status_msg.edit_text("❌ Пользователь не найден в БД")
                return

            gs_config = await session.scalar(
                select(GoogleSheetsConfig).where(GoogleSheetsConfig.user_id == user.id)
            )
            if not gs_config:
                await status_msg.edit_text("❌ Google Sheets не настроен.\nПодключите через: Настройки → Google Sheets")
                return

            if not gs_config.enabled:
                await status_msg.edit_text("⚠️ Google Sheets отключён. Включите в настройках.")
                return

            subscription_tier = user.subscription_tier or 'trial'

            result = await session.execute(
                select(SniperNotification)
                .where(SniperNotification.user_id == user.id)
                .order_by(SniperNotification.sent_at.desc())
                .limit(4)
            )
            notifications = result.scalars().all()

            if not notifications:
                await status_msg.edit_text("❌ Нет уведомлений для отправки")
                return

        from tender_sniper.google_sheets_sync import (
            get_sheets_sync, AI_COLUMNS, DEFAULT_COLUMNS, enrich_tender_with_ai
        )

        sheets_sync = get_sheets_sync()
        if not sheets_sync:
            await status_msg.edit_text("❌ Google Sheets sync не инициализирован (проверьте GOOGLE_SERVICE_ACCOUNT_JSON)")
            return

        columns = gs_config.columns if isinstance(gs_config.columns, list) else []
        if not columns:
            columns = DEFAULT_COLUMNS

        # Проверяем нужно ли AI обогащение
        has_ai_columns = bool(set(columns) & AI_COLUMNS)
        is_premium = subscription_tier == 'premium'
        do_ai = has_ai_columns and is_premium

        if do_ai:
            await status_msg.edit_text(
                f"🔄 Отправляю {len(notifications)} тендеров в Google Sheets...\n"
                f"🤖 AI-обогащение включено (это может занять 1-2 мин на тендер)"
            )
        else:
            await status_msg.edit_text(f"🔄 Отправляю {len(notifications)} тендеров в Google Sheets...")

        results = []
        for i, n in enumerate(notifications, 1):
            tender_data = {
                'number': n.tender_number or '',
                'name': n.tender_name or '',
                'price': n.tender_price,
                'url': n.tender_url or '',
                'region': n.tender_region or '',
                'customer_name': n.tender_customer or '',
                'published_date': n.published_date.strftime('%d.%m.%Y') if n.published_date else '',
                'submission_deadline': n.submission_deadline.strftime('%d.%m.%Y %H:%M') if n.submission_deadline else '',
            }

            # AI обогащение
            ai_data = {}
            if do_ai and n.tender_number:
                try:
                    ai_data = await enrich_tender_with_ai(
                        tender_number=n.tender_number,
                        tender_price=n.tender_price,
                        customer_name=n.tender_customer or '',
                        subscription_tier='premium'
                    )
                except Exception as ai_err:
                    logger.warning(f"AI enrichment ошибка для {n.tender_number}: {ai_err}")

            match_data = {
                'score': n.score or 0,
                'red_flags': [],
                'filter_name': n.filter_name or '',
                'ai_data': ai_data,
            }

            try:
                from tender_sniper.google_sheets_sync import get_weekly_sheet_name
                success = await sheets_sync.append_tender(
                    spreadsheet_id=gs_config.spreadsheet_id,
                    tender_data=tender_data,
                    match_data=match_data,
                    columns=columns,
                    sheet_name=get_weekly_sheet_name()
                )
                name_short = (n.tender_name or '')[:35]
                ai_tag = f" (AI: {len(ai_data)} полей)" if ai_data else ""
                if success:
                    results.append(f"✅ {name_short}{ai_tag}")
                else:
                    results.append(f"❌ {name_short}")
            except Exception as e:
                results.append(f"❌ Ошибка: {str(e)[:50]}")

        success_count = sum(1 for r in results if r.startswith("✅"))
        text = (
            f"📊 <b>Тест Google Sheets</b>\n\n"
            f"Отправлено: {success_count}/{len(notifications)}\n"
            f"AI обогащение: {'✅ Да' if do_ai else '❌ Нет'}\n\n"
            + "\n".join(results)
        )
        await status_msg.edit_text(text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка test_sheets: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {str(e)[:200]}")
