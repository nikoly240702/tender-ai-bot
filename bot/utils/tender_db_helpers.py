"""
Вспомогательные функции для работы с тендерами в БД.

Обертки над database.py для удобной работы с избранным, скрытыми, напоминаниями.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import logging

from database import DatabaseSession, TenderFavorite, HiddenTender, TenderReminder, UserProfile, SniperUser, SniperNotification
from sqlalchemy import select, delete, and_, or_, func

logger = logging.getLogger(__name__)


# ============================================
# ИЗБРАННОЕ
# ============================================

async def add_to_favorites(
    user_id: int,
    tender_number: str,
    tender_name: str = None,
    tender_price: float = None,
    tender_url: str = None,
    notes: str = None
) -> bool:
    """
    Добавляет тендер в избранное.

    Returns:
        bool: True если успешно добавлено
    """
    try:
        async with DatabaseSession() as session:
            # Проверяем, не добавлен ли уже
            result = await session.execute(
                select(TenderFavorite).where(
                    and_(
                        TenderFavorite.user_id == user_id,
                        TenderFavorite.tender_number == tender_number
                    )
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                logger.info(f"Тендер {tender_number} уже в избранном у user {user_id}")
                return False

            # Добавляем в избранное
            favorite = TenderFavorite(
                user_id=user_id,
                tender_number=tender_number,
                tender_name=tender_name,
                tender_price=tender_price,
                tender_url=tender_url,
                notes=notes
            )
            session.add(favorite)

            logger.info(f"✅ Тендер {tender_number} добавлен в избранное user {user_id}")
            return True

    except Exception as e:
        logger.error(f"Ошибка добавления в избранное: {e}", exc_info=True)
        return False


async def remove_from_favorites(user_id: int, tender_number: str) -> bool:
    """Удаляет тендер из избранного."""
    try:
        async with DatabaseSession() as session:
            await session.execute(
                delete(TenderFavorite).where(
                    and_(
                        TenderFavorite.user_id == user_id,
                        TenderFavorite.tender_number == tender_number
                    )
                )
            )
            logger.info(f"✅ Тендер {tender_number} удален из избранного user {user_id}")
            return True

    except Exception as e:
        logger.error(f"Ошибка удаления из избранного: {e}", exc_info=True)
        return False


async def get_user_favorites(user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    """Получает список избранных тендеров пользователя."""
    try:
        async with DatabaseSession() as session:
            result = await session.execute(
                select(TenderFavorite)
                .where(TenderFavorite.user_id == user_id)
                .order_by(TenderFavorite.added_at.desc())
                .limit(limit)
            )
            favorites = result.scalars().all()

            return [{
                'tender_number': f.tender_number,
                'tender_name': f.tender_name,
                'tender_price': f.tender_price,
                'tender_url': f.tender_url,
                'added_at': f.added_at.isoformat() if f.added_at else None,
                'notes': f.notes
            } for f in favorites]

    except Exception as e:
        logger.error(f"Ошибка получения избранного: {e}", exc_info=True)
        return []


# ============================================
# СКРЫТЫЕ ТЕНДЕРЫ
# ============================================

async def hide_tender(user_id: int, tender_number: str, reason: str = None) -> bool:
    """Скрывает тендер."""
    try:
        async with DatabaseSession() as session:
            # Проверяем, не скрыт ли уже
            result = await session.execute(
                select(HiddenTender).where(
                    and_(
                        HiddenTender.user_id == user_id,
                        HiddenTender.tender_number == tender_number
                    )
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                logger.info(f"Тендер {tender_number} уже скрыт у user {user_id}")
                return False

            # Скрываем
            hidden = HiddenTender(
                user_id=user_id,
                tender_number=tender_number,
                reason=reason
            )
            session.add(hidden)

            logger.info(f"✅ Тендер {tender_number} скрыт для user {user_id}")
            return True

    except Exception as e:
        logger.error(f"Ошибка скрытия тендера: {e}", exc_info=True)
        return False


async def unhide_tender(user_id: int, tender_number: str) -> bool:
    """Возвращает тендер из скрытых."""
    try:
        async with DatabaseSession() as session:
            await session.execute(
                delete(HiddenTender).where(
                    and_(
                        HiddenTender.user_id == user_id,
                        HiddenTender.tender_number == tender_number
                    )
                )
            )
            logger.info(f"✅ Тендер {tender_number} возвращен из скрытых для user {user_id}")
            return True

    except Exception as e:
        logger.error(f"Ошибка возврата из скрытых: {e}", exc_info=True)
        return False


async def get_user_hidden_tenders(user_id: int) -> List[Dict[str, Any]]:
    """Получает список скрытых тендеров пользователя."""
    try:
        async with DatabaseSession() as session:
            result = await session.execute(
                select(HiddenTender)
                .where(HiddenTender.user_id == user_id)
                .order_by(HiddenTender.hidden_at.desc())
            )
            hidden = result.scalars().all()

            return [{
                'tender_number': h.tender_number,
                'hidden_at': h.hidden_at.isoformat() if h.hidden_at else None,
                'reason': h.reason
            } for h in hidden]

    except Exception as e:
        logger.error(f"Ошибка получения скрытых: {e}", exc_info=True)
        return []


async def is_tender_hidden(user_id: int, tender_number: str) -> bool:
    """Проверяет, скрыт ли тендер."""
    try:
        async with DatabaseSession() as session:
            result = await session.execute(
                select(HiddenTender).where(
                    and_(
                        HiddenTender.user_id == user_id,
                        HiddenTender.tender_number == tender_number
                    )
                )
            )
            return result.scalar_one_or_none() is not None

    except Exception as e:
        logger.error(f"Ошибка проверки скрытого: {e}", exc_info=True)
        return False


# ============================================
# НАПОМИНАНИЯ
# ============================================

async def create_reminder(
    user_id: int,
    tender_number: str,
    days_before: int,
    tender_name: str = None,
    tender_url: str = None
) -> bool:
    """
    Создает напоминание о тендере.

    Args:
        user_id: ID пользователя
        tender_number: Номер тендера
        days_before: За сколько дней до дедлайна напомнить
        tender_name: Название тендера
        tender_url: URL тендера

    Returns:
        bool: True если успешно создано
    """
    try:
        # Вычисляем время напоминания (предполагаем дедлайн через 14 дней от сегодня)
        # TODO: В будущем использовать реальный deadline из тендера
        deadline = datetime.now() + timedelta(days=14)
        reminder_time = deadline - timedelta(days=days_before)

        async with DatabaseSession() as session:
            reminder = TenderReminder(
                user_id=user_id,
                tender_number=tender_number,
                tender_name=tender_name,
                tender_url=tender_url,
                reminder_time=reminder_time,
                days_before_deadline=days_before
            )
            session.add(reminder)

            logger.info(f"✅ Напоминание создано для user {user_id}, тендер {tender_number}, время {reminder_time}")
            return True

    except Exception as e:
        logger.error(f"Ошибка создания напоминания: {e}", exc_info=True)
        return False


async def get_user_reminders(user_id: int, active_only: bool = True) -> List[Dict[str, Any]]:
    """Получает список напоминаний пользователя."""
    try:
        async with DatabaseSession() as session:
            query = select(TenderReminder).where(TenderReminder.user_id == user_id)

            if active_only:
                query = query.where(TenderReminder.sent == False)

            query = query.order_by(TenderReminder.reminder_time.asc())

            result = await session.execute(query)
            reminders = result.scalars().all()

            return [{
                'id': r.id,
                'tender_number': r.tender_number,
                'tender_name': r.tender_name,
                'tender_url': r.tender_url,
                'reminder_time': r.reminder_time.isoformat() if r.reminder_time else None,
                'days_before_deadline': r.days_before_deadline,
                'sent': r.sent
            } for r in reminders]

    except Exception as e:
        logger.error(f"Ошибка получения напоминаний: {e}", exc_info=True)
        return []


# ============================================
# ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ
# ============================================

async def get_user_profile(user_id: int) -> Optional[Dict[str, Any]]:
    """Получает профиль пользователя."""
    try:
        async with DatabaseSession() as session:
            result = await session.execute(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
            profile = result.scalar_one_or_none()

            if not profile:
                return None

            return {
                'specialization': profile.specialization,
                'regions': profile.regions,
                'amount_min': profile.amount_min,
                'amount_max': profile.amount_max,
                'licenses': profile.licenses,
                'experience_years': profile.experience_years,
                'preferred_law_types': profile.preferred_law_types
            }

    except Exception as e:
        logger.error(f"Ошибка получения профиля: {e}", exc_info=True)
        return None


async def create_or_update_profile(
    user_id: int,
    specialization: str = None,
    regions: list = None,
    amount_min: float = None,
    amount_max: float = None,
    **kwargs
) -> bool:
    """Создает или обновляет профиль пользователя."""
    try:
        async with DatabaseSession() as session:
            result = await session.execute(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
            profile = result.scalar_one_or_none()

            if profile:
                # Обновляем существующий
                if specialization is not None:
                    profile.specialization = specialization
                if regions is not None:
                    profile.regions = regions
                if amount_min is not None:
                    profile.amount_min = amount_min
                if amount_max is not None:
                    profile.amount_max = amount_max

                profile.updated_at = datetime.utcnow()
            else:
                # Создаем новый
                profile = UserProfile(
                    user_id=user_id,
                    specialization=specialization,
                    regions=regions or [],
                    amount_min=amount_min,
                    amount_max=amount_max
                )
                session.add(profile)

            logger.info(f"✅ Профиль {'обновлен' if profile else 'создан'} для user {user_id}")
            return True

    except Exception as e:
        logger.error(f"Ошибка сохранения профиля: {e}", exc_info=True)
        return False


# ============================================
# СТАТИСТИКА
# ============================================

async def get_user_stats(user_id: int) -> Dict[str, Any]:
    """Получает статистику пользователя."""
    try:
        async with DatabaseSession() as session:
            # Получаем sniper_user
            result = await session.execute(
                select(SniperUser).where(SniperUser.id == user_id)
            )
            sniper_user = result.scalar_one_or_none()

            if not sniper_user:
                return {}

            # Считаем избранные
            favorites_count = await session.scalar(
                select(func.count()).select_from(TenderFavorite).where(TenderFavorite.user_id == user_id)
            )

            # Считаем скрытые
            hidden_count = await session.scalar(
                select(func.count()).select_from(HiddenTender).where(HiddenTender.user_id == user_id)
            )

            # Считаем активные напоминания
            reminders_count = await session.scalar(
                select(func.count()).select_from(TenderReminder).where(
                    and_(
                        TenderReminder.user_id == user_id,
                        TenderReminder.sent == False
                    )
                )
            )

            # Считаем уведомления за последний месяц
            month_ago = datetime.now() - timedelta(days=30)
            notifications_count = await session.scalar(
                select(func.count()).select_from(SniperNotification).where(
                    and_(
                        SniperNotification.user_id == user_id,
                        SniperNotification.sent_at >= month_ago
                    )
                )
            )

            return {
                'favorites_count': favorites_count or 0,
                'hidden_count': hidden_count or 0,
                'reminders_count': reminders_count or 0,
                'notifications_count': notifications_count or 0,
                'subscription_tier': sniper_user.subscription_tier,
                'notifications_sent_today': sniper_user.notifications_sent_today
            }

    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}", exc_info=True)
        return {}


__all__ = [
    'add_to_favorites',
    'remove_from_favorites',
    'get_user_favorites',
    'hide_tender',
    'unhide_tender',
    'get_user_hidden_tenders',
    'is_tender_hidden',
    'create_reminder',
    'get_user_reminders',
    'get_user_profile',
    'create_or_update_profile',
    'get_user_stats'
]
