"""
LangGraph agent graph for Tender-GPT.

Simple ReAct-style agent: LLM decides whether to call tools or respond.
"""

import os
import logging
from typing import Annotated, TypedDict, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from tender_sniper.tender_gpt.prompts import SYSTEM_PROMPT
from tender_sniper.tender_gpt.tools import search_tenders, get_tender_details, analyze_risks, analyze_documentation

logger = logging.getLogger(__name__)


# State definition
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]


# All available tools
TOOLS = [search_tenders, get_tender_details, analyze_risks, analyze_documentation]


def create_agent_graph():
    """
    Create and compile the LangGraph agent.

    Returns compiled StateGraph.
    """
    # LLM with tool binding
    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.3,
        api_key=os.getenv("OPENAI_API_KEY"),
        max_tokens=2000,
    )
    llm_with_tools = llm.bind_tools(TOOLS)

    # --- Nodes ---

    async def call_model(state: AgentState) -> dict:
        """Call the LLM with current messages."""
        messages = state["messages"]

        # Ensure system prompt is first
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)

        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}

    def should_continue(state: AgentState) -> str:
        """Decide: call tools or end."""
        last_message = state["messages"][-1]
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        return END

    # --- Build graph ---
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("agent", call_model)
    graph.add_node("tools", ToolNode(TOOLS))

    # Set entry point
    graph.set_entry_point("agent")

    # Add edges
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile()


# Singleton compiled graph
_compiled_graph = None


def get_agent_graph():
    """Get or create the compiled agent graph (singleton)."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = create_agent_graph()
        logger.info("Tender-GPT LangGraph agent compiled")
    return _compiled_graph
