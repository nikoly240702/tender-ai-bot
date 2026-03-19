"""
TenderGPTService — main facade for Tender-GPT.

Orchestrates: quota check -> session management -> LangGraph agent -> save history.
Platform-agnostic (works with Telegram, VK, web).
"""

import logging
from typing import Optional, Dict, Any

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from tender_sniper.tender_gpt.graph import get_agent_graph
from tender_sniper.tender_gpt.session_manager import SessionManager
from tender_sniper.tender_gpt.quota_manager import QuotaManager, QUOTA_EXCEEDED_MESSAGE
from tender_sniper.tender_gpt.prompts import SYSTEM_PROMPT, GREETING_MESSAGE

logger = logging.getLogger(__name__)


class TenderGPTService:
    """
    Main entry point for Tender-GPT conversations.

    Usage:
        service = TenderGPTService()
        response = await service.chat(telegram_id=123, user_message="Найди тендеры на ПО")
    """

    def __init__(self):
        self.session_manager = SessionManager()
        self.quota_manager = QuotaManager()

    async def chat(
        self,
        telegram_id: int,
        user_id: int,
        user_message: str,
        tender_number: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process a user message and return AI response.

        Args:
            telegram_id: Telegram user ID
            user_id: Internal DB user ID (sniper_users.id)
            user_message: User's text message
            tender_number: Optional tender number if chat started from tender card

        Returns:
            {
                'response': str,           # AI response text
                'session_id': str,          # Session UUID
                'quota': {...},             # Quota info
                'tool_calls': int,          # Number of tools called
            }
        """
        # 1. Check quota
        quota = await self.quota_manager.check_quota(telegram_id)
        if not quota['allowed']:
            return {
                'response': QUOTA_EXCEEDED_MESSAGE.format(
                    tier=quota['tier'],
                    limit=quota['limit'],
                    used=quota['used'],
                ),
                'session_id': None,
                'quota': quota,
                'tool_calls': 0,
            }

        # 2. Get or create session
        session_id = await self.session_manager.get_or_create_session(
            user_id=user_id,
            tender_number=tender_number,
        )

        # 3. Save user message to history
        await self.session_manager.add_message(
            session_id=session_id,
            role="user",
            content=user_message,
        )

        # 4. Load history for context
        history = await self.session_manager.get_history(session_id)

        # 5. Build messages for LangGraph
        messages = [SystemMessage(content=SYSTEM_PROMPT)]
        for msg in history:
            if msg["role"] == "user":
                messages.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                messages.append(AIMessage(content=msg["content"]))
            # Tool messages are handled internally by LangGraph

        # 6. Run LangGraph agent
        try:
            graph = get_agent_graph()
            result = await graph.ainvoke({"messages": messages})

            # Extract final AI response
            final_messages = result.get("messages", [])
            ai_response = ""
            tool_calls_count = 0

            for msg in final_messages:
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    tool_calls_count += len(msg.tool_calls)

            # Get the last AI message (the final response)
            for msg in reversed(final_messages):
                if isinstance(msg, AIMessage) and msg.content and not getattr(msg, 'tool_calls', None):
                    ai_response = msg.content
                    break

            if not ai_response:
                # Fallback — get any AI message content
                for msg in reversed(final_messages):
                    if isinstance(msg, AIMessage) and msg.content:
                        ai_response = msg.content
                        break

            if not ai_response:
                ai_response = "Извините, не удалось сформировать ответ. Попробуйте переформулировать вопрос."

        except Exception as e:
            logger.error(f"LangGraph agent error for user {telegram_id}: {e}", exc_info=True)
            ai_response = (
                "Произошла ошибка при обработке запроса. "
                "Попробуйте ещё раз или переформулируйте вопрос."
            )
            tool_calls_count = 0

        # 7. Truncate response for Telegram (4096 chars max)
        if len(ai_response) > 4000:
            ai_response = ai_response[:3950] + "\n\n<i>...ответ сокращён</i>"

        # 8. Save AI response to history
        await self.session_manager.add_message(
            session_id=session_id,
            role="assistant",
            content=ai_response,
        )

        # 9. Increment quota
        await self.quota_manager.increment(telegram_id)

        # 10. Update quota info
        quota = await self.quota_manager.check_quota(telegram_id)

        return {
            'response': ai_response,
            'session_id': session_id,
            'quota': quota,
            'tool_calls': tool_calls_count,
        }

    async def get_greeting(self) -> str:
        """Return the greeting message for new conversations."""
        return GREETING_MESSAGE
