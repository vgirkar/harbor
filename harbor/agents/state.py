"""Graph state. `messages` is the durable conversation (persisted by the checkpointer per thread); everything
else is scratch for the current turn."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    conversation_id: str
    customer_email: str | None
    user_input: str
    guard: dict[str, Any]
    route: str
    route_confidence: float
    retrieved: list[dict[str, Any]]
    draft: str
    attempts: int
    verdict: dict[str, Any]
    final_answer: str
    citations: list[dict[str, Any]]
    ticket_id: str | None
    trace: list[str]  # node names visited in this turn; reset by the runtime at every turn
