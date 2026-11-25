"""Runs one conversational turn through the graph and persists the transcript."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harbor.db.engine import session_scope
from harbor.db.repositories import ConversationRepository
from harbor.logging import get_logger

log = get_logger(__name__)

STATUS_LABELS = {
    "guard": "Checking the request",
    "router": "Working out what you need",
    "knowledge": "Searching the knowledge base",
    "verifier": "Verifying the answer",
    "order": "Looking up your order",
    "escalate": "Creating a ticket",
    "smalltalk": "Replying",
    "finalize": "Finishing up",
}


@dataclass(slots=True)
class TurnResult:
    conversation_id: str
    answer: str
    intent: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    guard: dict[str, Any] = field(default_factory=dict)
    verdict: dict[str, Any] = field(default_factory=dict)
    retrieved: list[dict[str, Any]] = field(default_factory=list)
    ticket_id: str | None = None
    trace: list[str] = field(default_factory=list)
    attempts: int = 1
    latency_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "answer": self.answer,
            "intent": self.intent,
            "citations": self.citations,
            "guard": self.guard,
            "verdict": self.verdict,
            "ticket_id": self.ticket_id,
            "trace": self.trace,
            "attempts": self.attempts,
            "latency_ms": self.latency_ms,
        }


class AgentRuntime:
    def __init__(
        self, graph: CompiledStateGraph, session_factory: async_sessionmaker[AsyncSession] | None = None
    ):
        self._graph = graph
        self._sessions = session_factory

    # ---- helpers -------------------------------------------------------------------------------
    async def _open_conversation(
        self, conversation_id: str | None, channel: str, email: str | None
    ) -> uuid.UUID:
        cid = uuid.UUID(conversation_id) if conversation_id else uuid.uuid4()
        if self._sessions is None:
            return cid
        async with session_scope(self._sessions) as session:
            conv = await ConversationRepository(session).get_or_create(
                cid, channel=channel, customer_email=email
            )
            return conv.id

    async def _persist(self, cid: uuid.UUID, user_text: str, result: TurnResult) -> None:
        if self._sessions is None:
            return
        async with session_scope(self._sessions) as session:
            repo = ConversationRepository(session)
            await repo.add_message(cid, role="user", content=user_text, guard=result.guard)
            await repo.add_message(
                cid,
                role="assistant",
                content=result.answer,
                intent=result.intent,
                citations=result.citations,
                verdict=result.verdict,
                latency_ms=result.latency_ms,
            )

    @staticmethod
    def _inputs(cid: uuid.UUID, text: str, email: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
        state = {
            "messages": [HumanMessage(content=text)],
            "conversation_id": str(cid),
            "customer_email": email,
            "user_input": text,
            "route": "",
            "retrieved": [],
            "draft": "",
            "attempts": 0,
            "verdict": {},
            "guard": {},
            "citations": [],
            "ticket_id": None,
            "final_answer": "",
            "trace": [],
        }
        config = {"configurable": {"thread_id": str(cid)}, "metadata": {"conversation_id": str(cid)}}
        return state, config

    @staticmethod
    def _result(cid: uuid.UUID, final: dict[str, Any], started: float) -> TurnResult:
        return TurnResult(
            conversation_id=str(cid),
            answer=final.get("final_answer", ""),
            intent=final.get("route") or "knowledge",
            citations=final.get("citations", []),
            guard=final.get("guard", {}),
            verdict=final.get("verdict", {}),
            retrieved=final.get("retrieved", []),
            ticket_id=final.get("ticket_id"),
            trace=[t for t in final.get("trace", []) if t],
            attempts=max(1, int(final.get("attempts") or 1)),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # ---- public API ----------------------------------------------------------------------------
    async def run_turn(
        self,
        *,
        message: str,
        conversation_id: str | None = None,
        customer_email: str | None = None,
        channel: str = "text",
    ) -> TurnResult:
        started = time.perf_counter()
        cid = await self._open_conversation(conversation_id, channel, customer_email)
        state, config = self._inputs(cid, message, customer_email)
        final = await self._graph.ainvoke(state, config)
        result = self._result(cid, final, started)
        await self._persist(cid, message, result)
        log.info(
            "turn.done", conversation=str(cid), intent=result.intent, ms=result.latency_ms, trace=result.trace
        )
        return result

    async def stream_turn(
        self,
        *,
        message: str,
        conversation_id: str | None = None,
        customer_email: str | None = None,
        channel: str = "text",
    ) -> AsyncIterator[dict[str, Any]]:
        """Yields SSE-ready events: status (node started), token (answer text), rewrite (verifier rejected the
        draft, client should clear it), done (full TurnResult)."""
        started = time.perf_counter()
        cid = await self._open_conversation(conversation_id, channel, customer_email)
        state, config = self._inputs(cid, message, customer_email)
        yield {"event": "conversation", "data": {"conversation_id": str(cid)}}

        final: dict[str, Any] = {}
        async for ev in self._graph.astream_events(state, config, version="v2"):
            kind, name = ev["event"], ev.get("name", "")
            if kind == "on_chain_start" and name in STATUS_LABELS:
                yield {"event": "status", "data": {"node": name, "label": STATUS_LABELS[name]}}
            elif kind == "on_chat_model_stream" and "answer" in ev.get("tags", []):
                chunk = ev["data"]["chunk"]
                text = (
                    chunk.content
                    if isinstance(chunk.content, str)
                    else "".join(b.get("text", "") for b in chunk.content if isinstance(b, dict))
                )
                if text:
                    yield {"event": "token", "data": {"text": text}}
            elif kind == "on_chain_end" and name == "verifier":
                verdict = (ev["data"].get("output") or {}).get("verdict") or {}
                if verdict and not verdict.get("grounded", True):
                    yield {"event": "rewrite", "data": {"reason": verdict.get("fix_instruction", "")}}
            elif kind == "on_chain_end" and name == "LangGraph":
                final = ev["data"].get("output") or {}

        if not final:
            snapshot = await self._graph.aget_state(config)
            final = dict(snapshot.values)
        result = self._result(cid, final, started)
        await self._persist(cid, message, result)
        yield {"event": "done", "data": result.to_dict()}
