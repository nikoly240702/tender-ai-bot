"""
Логирование действий пользователей для аналитики.

События:
- filter_created, filter_edited, filter_deleted
- search_executed, tender_viewed, tender_favorited
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def log_user_action(
    user_id: int,
    action_type: str,
    action_data: Optional[Dict[str, Any]] = None
):
    """
    Логирование действия пользователя.

    Args:
        user_id: ID пользователя в БД (не telegram_id!)
        action_type: Тип действия (filter_created, search_executed, etc.)
        action_data: Дополнительные данные в JSON формате

    Examples:
        await log_user_action(
            user_id=123,
            action_type='filter_created',
            action_data={'filter_name': 'IT оборудование', 'keywords_count': 3}
        )
    """
    try:
        from tender_sniper.database.sqlalchemy_adapter import DatabaseSession
        from sqlalchemy.dialects.postgresql import insert
        import json

        async with DatabaseSession() as session:
            stmt = text("""
                INSERT INTO user_actions (user_id, action_type, action_data)
                VALUES (:user_id, :action_type, :action_data)
            """)

            await session.execute(
                stmt,
                {
                    'user_id': user_id,
                    'action_type': action_type,
                    'action_data': json.dumps(action_data) if action_data else None
                }
            )
            await session.commit()

        logger.debug(f"📊 User action logged: {action_type} for user {user_id}")

    except Exception as e:
        # Не критично если не залогировали - не прерываем основной flow
        logger.warning(f"Failed to log user action: {e}")


async def get_user_stats(user_id: int) -> Dict[str, Any]:
    """
    Получение статистики действий пользователя.

    Returns:
        {
            'total_actions': 150,
            'filters_created': 5,
            'searches_executed': 45,
            'tenders_viewed': 100
        }
    """
    try:
        from tender_sniper.database.sqlalchemy_adapter import DatabaseSession
        from sqlalchemy import text

        async with DatabaseSession() as session:
            result = await session.execute(
                text("""
                    SELECT
                        COUNT(*) as total_actions,
                        COUNT(*) FILTER (WHERE action_type = 'filter_created') as filters_created,
                        COUNT(*) FILTER (WHERE action_type = 'search_executed') as searches_executed,
                        COUNT(*) FILTER (WHERE action_type = 'tender_viewed') as tenders_viewed
                    FROM user_actions
                    WHERE user_id = :user_id
                """),
                {'user_id': user_id}
            )

            row = result.first()
            if row:
                return {
                    'total_actions': row[0],
                    'filters_created': row[1],
                    'searches_executed': row[2],
                    'tenders_viewed': row[3]
                }

        return {'total_actions': 0}

    except Exception as e:
        logger.error(f"Failed to get user stats: {e}")
        return {'total_actions': 0}


async def get_popular_actions(days: int = 7) -> list:
    """
    Получение популярных действий за последние N дней.

    Returns:
        [
            {'action_type': 'filter_created', 'count': 45},
            {'action_type': 'search_executed', 'count': 120},
            ...
        ]
    """
    try:
        from tender_sniper.database.sqlalchemy_adapter import DatabaseSession
        from sqlalchemy import text

        async with DatabaseSession() as session:
            result = await session.execute(
                text("""
                    SELECT action_type, COUNT(*) as count
                    FROM user_actions
                    WHERE created_at > NOW() - INTERVAL ':days days'
                    GROUP BY action_type
                    ORDER BY count DESC
                    LIMIT 10
                """),
                {'days': days}
            )

            return [
                {'action_type': row[0], 'count': row[1]}
                for row in result
            ]

    except Exception as e:
        logger.error(f"Failed to get popular actions: {e}")
        return []


__all__ = ['log_user_action', 'get_user_stats', 'get_popular_actions']
