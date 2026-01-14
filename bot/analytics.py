"""
Analytics module for tracking user events.

Простой интерфейс для отслеживания событий пользователей.
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any

from database import DatabaseSession, UserEvent, SniperUser
from sqlalchemy import select

logger = logging.getLogger(__name__)


class EventType:
    """Типы событий для аналитики."""
    # Регистрация и активность
    REGISTRATION = 'registration'
    BOT_START = 'bot_start'
    BOT_BLOCKED = 'bot_blocked'
    BOT_UNBLOCKED = 'bot_unblocked'

    # Рассылки
    BROADCAST_DELIVERED = 'broadcast_delivered'
    BROADCAST_FAILED = 'broadcast_failed'
    BROADCAST_CLICKED = 'broadcast_clicked'

    # Подписки
    SUBSCRIPTION_VIEWED = 'subscription_viewed'
    SUBSCRIPTION_PURCHASED = 'subscription_purchased'
    SUBSCRIPTION_EXPIRED = 'subscription_expired'
    PROMOCODE_USED = 'promocode_used'

    # Фильтры
    FILTER_CREATED = 'filter_created'
    FILTER_DELETED = 'filter_deleted'
    FILTER_TOGGLED = 'filter_toggled'

    # Поиск
    SEARCH_PERFORMED = 'search_performed'
    TENDER_VIEWED = 'tender_viewed'
    TENDER_FAVORITED = 'tender_favorited'

    # Рефералы
    REFERRAL_LINK_GENERATED = 'referral_link_generated'
    REFERRAL_USED = 'referral_used'
    REFERRAL_BONUS_GIVEN = 'referral_bonus_given'

    # Кнопки и действия
    BUTTON_CLICKED = 'button_clicked'
    MENU_OPENED = 'menu_opened'


async def track_event(
    event_type: str,
    telegram_id: Optional[int] = None,
    user_id: Optional[int] = None,
    broadcast_id: Optional[int] = None,
    data: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Записать событие в базу данных.

    Args:
        event_type: Тип события (см. EventType)
        telegram_id: Telegram ID пользователя
        user_id: ID пользователя в БД (sniper_users.id)
        broadcast_id: ID рассылки (если событие связано с рассылкой)
        data: Дополнительные данные о событии

    Returns:
        True если событие записано успешно
    """
    try:
        async with DatabaseSession() as session:
            # Если передан telegram_id, но не user_id - попробуем найти user_id
            if telegram_id and not user_id:
                result = await session.execute(
                    select(SniperUser.id).where(SniperUser.telegram_id == telegram_id)
                )
                row = result.scalar_one_or_none()
                if row:
                    user_id = row

            event = UserEvent(
                user_id=user_id,
                telegram_id=telegram_id,
                event_type=event_type,
                event_data=data,
                broadcast_id=broadcast_id,
                created_at=datetime.utcnow()
            )
            session.add(event)
            await session.commit()

            logger.debug(f"Event tracked: {event_type} for user {telegram_id or user_id}")
            return True

    except Exception as e:
        logger.error(f"Failed to track event {event_type}: {e}")
        return False


async def track_button_click(
    telegram_id: int,
    button_name: str,
    callback_data: str,
    source: Optional[str] = None
) -> bool:
    """
    Отследить клик по кнопке.

    Args:
        telegram_id: Telegram ID пользователя
        button_name: Название кнопки
        callback_data: callback_data кнопки
        source: Источник (например, 'broadcast', 'menu')
    """
    return await track_event(
        EventType.BUTTON_CLICKED,
        telegram_id=telegram_id,
        data={
            'button_name': button_name,
            'callback_data': callback_data,
            'source': source
        }
    )


async def track_broadcast_delivery(
    telegram_id: int,
    broadcast_id: int,
    success: bool,
    error: Optional[str] = None
) -> bool:
    """
    Отследить доставку рассылки.

    Args:
        telegram_id: Telegram ID пользователя
        broadcast_id: ID рассылки
        success: Успешно ли доставлено
        error: Текст ошибки (если не успешно)
    """
    event_type = EventType.BROADCAST_DELIVERED if success else EventType.BROADCAST_FAILED
    return await track_event(
        event_type,
        telegram_id=telegram_id,
        broadcast_id=broadcast_id,
        data={'error': error} if error else None
    )


async def track_subscription_action(
    telegram_id: int,
    action: str,
    tier: Optional[str] = None,
    amount: Optional[float] = None,
    promocode: Optional[str] = None
) -> bool:
    """
    Отследить действие с подпиской.

    Args:
        telegram_id: Telegram ID пользователя
        action: Тип действия (viewed, purchased, expired)
        tier: Тариф
        amount: Сумма платежа
        promocode: Промокод (если использован)
    """
    event_map = {
        'viewed': EventType.SUBSCRIPTION_VIEWED,
        'purchased': EventType.SUBSCRIPTION_PURCHASED,
        'expired': EventType.SUBSCRIPTION_EXPIRED,
        'promocode': EventType.PROMOCODE_USED,
    }
    event_type = event_map.get(action, EventType.SUBSCRIPTION_VIEWED)

    return await track_event(
        event_type,
        telegram_id=telegram_id,
        data={
            'tier': tier,
            'amount': amount,
            'promocode': promocode
        }
    )


# Shortcut functions for common events
async def track_registration(telegram_id: int, username: Optional[str] = None, referral_code: Optional[str] = None):
    """Отследить регистрацию нового пользователя."""
    return await track_event(
        EventType.REGISTRATION,
        telegram_id=telegram_id,
        data={'username': username, 'referral_code': referral_code}
    )


async def track_filter_action(telegram_id: int, action: str, filter_name: Optional[str] = None, filter_id: Optional[int] = None):
    """Отследить действие с фильтром."""
    event_map = {
        'created': EventType.FILTER_CREATED,
        'deleted': EventType.FILTER_DELETED,
        'toggled': EventType.FILTER_TOGGLED,
    }
    return await track_event(
        event_map.get(action, EventType.FILTER_CREATED),
        telegram_id=telegram_id,
        data={'filter_name': filter_name, 'filter_id': filter_id, 'action': action}
    )


async def track_search(telegram_id: int, keywords: list, results_count: int):
    """Отследить поиск."""
    return await track_event(
        EventType.SEARCH_PERFORMED,
        telegram_id=telegram_id,
        data={'keywords': keywords, 'results_count': results_count}
    )
