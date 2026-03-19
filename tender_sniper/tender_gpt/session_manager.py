"""
Session manager for Tender-GPT.

Manages chat sessions: create, get/continue, timeout, history.
"""

import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from sqlalchemy import select, update, and_
from database import (
    GptSession as GptSessionModel,
    GptMessage as GptMessageModel,
    DatabaseSession,
)

logger = logging.getLogger(__name__)

# Session timeout: 30 minutes of inactivity
SESSION_TIMEOUT_MINUTES = 30
# Max messages in context window
MAX_CONTEXT_MESSAGES = 15


class SessionManager:
    """Manages Tender-GPT chat sessions."""

    async def get_or_create_session(
        self,
        user_id: int,
        tender_number: Optional[str] = None,
    ) -> str:
        """
        Get active session or create a new one.

        Returns session_id (UUID string).
        """
        async with DatabaseSession() as session:
            # Look for active session
            cutoff = datetime.utcnow() - timedelta(minutes=SESSION_TIMEOUT_MINUTES)
            result = await session.execute(
                select(GptSessionModel).where(
                    and_(
                        GptSessionModel.user_id == user_id,
                        GptSessionModel.is_active == True,
                        GptSessionModel.last_message_at >= cutoff,
                    )
                ).order_by(GptSessionModel.last_message_at.desc())
            )
            active_session = result.scalar_one_or_none()

            if active_session:
                # Update last_message_at
                active_session.last_message_at = datetime.utcnow()
                logger.debug(f"Continuing session {active_session.id} for user {user_id}")
                return active_session.id

            # Deactivate any old sessions
            await session.execute(
                update(GptSessionModel).where(
                    and_(
                        GptSessionModel.user_id == user_id,
                        GptSessionModel.is_active == True,
                    )
                ).values(is_active=False)
            )

            # Create new session
            session_id = str(uuid.uuid4())
            new_session = GptSessionModel(
                id=session_id,
                user_id=user_id,
                tender_number=tender_number,
                started_at=datetime.utcnow(),
                last_message_at=datetime.utcnow(),
                is_active=True,
            )
            session.add(new_session)
            logger.info(f"Created new GPT session {session_id} for user {user_id}")
            return session_id

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict] = None,
        tool_result: Optional[str] = None,
    ):
        """Add a message to session history."""
        async with DatabaseSession() as session:
            msg = GptMessageModel(
                session_id=session_id,
                role=role,
                content=content,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=tool_result,
                created_at=datetime.utcnow(),
            )
            session.add(msg)

            # Update session last_message_at
            await session.execute(
                update(GptSessionModel).where(
                    GptSessionModel.id == session_id
                ).values(last_message_at=datetime.utcnow())
            )

    async def get_history(
        self,
        session_id: str,
        limit: int = MAX_CONTEXT_MESSAGES,
    ) -> List[Dict[str, Any]]:
        """
        Get recent messages from session for LLM context.

        Returns list of dicts: [{"role": "user", "content": "..."}, ...]
        """
        async with DatabaseSession() as session:
            result = await session.execute(
                select(GptMessageModel).where(
                    GptMessageModel.session_id == session_id
                ).order_by(GptMessageModel.created_at.desc()).limit(limit)
            )
            rows = result.scalars().all()

        # Reverse to chronological order
        messages = []
        for row in reversed(rows):
            if row.role == "tool":
                # Tool results are included as tool messages in LangGraph format
                messages.append({
                    "role": "tool",
                    "content": row.tool_result or row.content,
                    "tool_name": row.tool_name,
                })
            else:
                messages.append({
                    "role": row.role,
                    "content": row.content,
                })
        return messages

    async def close_session(self, session_id: str):
        """Mark session as inactive."""
        async with DatabaseSession() as session:
            await session.execute(
                update(GptSessionModel).where(
                    GptSessionModel.id == session_id
                ).values(is_active=False)
            )
            logger.info(f"Closed GPT session {session_id}")
