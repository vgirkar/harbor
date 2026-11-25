"""Graph nodes. Each node is a small async function over AgentState; the services it needs are bound by
`build_nodes` so the functions stay testable with in-memory implementations."""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from harbor.agents.models import ModelFactory, StructuredOutputError
from harbor.agents.schemas import GroundingVerdict, InjectionVerdict, RouteDecision, TicketSummary
from harbor.agents.services import OrderService, Retriever, TicketService
from harbor.agents.state import AgentState
from harbor.agents.tools import make_order_tools
from harbor.config import Settings
from harbor.guard.injection import scan_text
from harbor.guard.output import guard_output
from harbor.guard.spotlight import render_documents, render_tool_result
from harbor.logging import get_logger
from harbor.prompts import load_prompt, prompt_version, render
from harbor.retrieval.types import RetrievedChunk

log = get_logger(__name__)

BLOCKED_REPLY = (
    "I can't help with that request. I'm happy to help with orders, shipping, returns, warranty, payments "
    "or product care."
)
HISTORY_WINDOW = 8
MAX_TOOL_ROUNDS = 3

Node = Callable[[AgentState, RunnableConfig], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class Services:
    settings: Settings
    models: ModelFactory
    retriever: Retriever
    orders: OrderService
    tickets: TicketService


def _history(state: AgentState) -> list[BaseMessage]:
    """Previous turns only (the current human message is re-added by each node with its own context)."""
    msgs = [m for m in state.get("messages", []) if isinstance(m, (HumanMessage, AIMessage))]
    return msgs[:-1][-HISTORY_WINDOW:]


def _chunks_from_state(state: AgentState, min_relevance: float) -> list[RetrievedChunk]:
    out = []
    for d in state.get("retrieved", []) or []:
        if d["score"] < min_relevance:
            continue
        out.append(
            RetrievedChunk(
                chunk_id=uuid.UUID(d["chunk_id"]),
                document_id=uuid.UUID(d["document_id"]),
                source=d["source"],
                title=d["title"],
                content=d["content"],
                score=d["score"],
                dense_rank=d.get("dense_rank"),
                lexical_rank=d.get("lexical_rank"),
                fused_score=d.get("fused_score", 0.0),
                metadata=d.get("metadata", {}),
            )
        )
    return out


def _tagged(config: RunnableConfig, *tags: str) -> RunnableConfig:
    merged = dict(config or {})
    merged["tags"] = list(merged.get("tags", [])) + list(tags)
    return merged


def build_nodes(svc: Services) -> dict[str, Node]:
    s = svc.settings

    # ---- 1. guard ------------------------------------------------------------------------------
    async def guard(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        text = state["user_input"]
        h = scan_text(text)
        decision: dict[str, Any] = {
            "heuristic_score": h.score,
            "patterns": h.matched,
            "llm": None,
            "blocked": False,
            "category": None,
            "decided_by": "heuristic",
        }
        threshold = s.guard_block_threshold
        if h.score >= s.guard_hard_block_score and s.guard_llm_mode != "always":
            decision.update(blocked=True, category=h.category)
        else:
            ask_llm = s.guard_llm_mode == "always" or (s.guard_llm_mode == "uncertain" and h.score >= 0.3)
            if ask_llm:
                try:
                    verdict: InjectionVerdict = await svc.models.ainvoke_structured(
                        "guard",
                        InjectionVerdict,
                        [SystemMessage(load_prompt("injection_classifier")), HumanMessage(text)],
                        config,
                    )
                    decision["llm"] = verdict.model_dump()
                    decision["decided_by"] = "llm"
                    if verdict.is_injection and verdict.confidence >= threshold:
                        decision.update(blocked=True, category=verdict.category)
                except StructuredOutputError as exc:
                    # fail closed on the uncertain band: the heuristics alone decide, at the LLM threshold
                    decision["llm"] = {"error": str(exc)[:200]}
                    decision["decided_by"] = "heuristic_fallback"
                    if h.score >= threshold:
                        decision.update(blocked=True, category=h.category)
            elif s.guard_llm_mode == "never" and h.score >= threshold:
                decision.update(blocked=True, category=h.category)
        if decision["blocked"]:
            log.warning(
                "guard.blocked", category=decision["category"], score=h.score, by=decision["decided_by"]
            )
        return {"guard": decision, "route": "blocked" if decision["blocked"] else "", "trace": ["guard"]}

    # ---- 2. router -----------------------------------------------------------------------------
    async def router(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        convo = _history(state)
        transcript = "\n".join(
            f"{'Customer' if isinstance(m, HumanMessage) else 'Assistant'}: {m.content}" for m in convo[-4:]
        )
        prompt = (
            f"Previous turns:\n{transcript or '(none)'}\n\nLatest customer message:\n{state['user_input']}"
        )
        try:
            decision: RouteDecision = await svc.models.ainvoke_structured(
                "router", RouteDecision, [SystemMessage(load_prompt("router")), HumanMessage(prompt)], config
            )
        except StructuredOutputError as exc:
            log.warning("router.fallback", error=str(exc)[:200])
            decision = RouteDecision(
                intent="knowledge", confidence=0.0, reason="router unavailable, defaulted"
            )
        intent = decision.intent if decision.confidence >= 0.4 else "knowledge"
        log.info("router.decided", intent=intent, confidence=decision.confidence, reason=decision.reason)
        return {"route": intent, "route_confidence": decision.confidence, "trace": ["router"]}

    # ---- 3a. knowledge agent (RAG) --------------------------------------------------------------
    async def knowledge(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        attempts = state.get("attempts", 0)
        retrieved = state.get("retrieved") or []
        if attempts == 0:
            chunks = await svc.retriever.retrieve(state["user_input"])
            retrieved = [c.to_dict() for c in chunks]
        relevant = _chunks_from_state({"retrieved": retrieved}, s.min_relevance)

        feedback = ""
        if attempts > 0 and state.get("verdict"):
            v = state["verdict"]
            claims = "; ".join(v.get("unsupported_claims", [])) or "(none listed)"
            feedback = (
                "A verifier found problems with your previous draft. Unsupported claims: "
                f"{claims}. Fix: {v.get('fix_instruction') or 'remove every claim not supported by the documents.'} "
                "Rewrite the full answer."
            )
        system = render("knowledge_agent", feedback=feedback)
        docs_block = (
            render_documents(relevant)
            if relevant
            else "<documents>\n(no relevant documents found)\n</documents>"
        )
        human = f"{docs_block}\n\nCustomer question: {state['user_input']}"
        ai = await svc.models.get("answer").ainvoke(
            [SystemMessage(system), *_history(state), HumanMessage(human)], _tagged(config, "answer")
        )
        log.info(
            "knowledge.drafted",
            attempt=attempts + 1,
            docs=len(relevant),
            prompt=prompt_version("knowledge_agent"),
        )
        return {"draft": ai.content, "retrieved": retrieved, "attempts": attempts + 1, "trace": ["knowledge"]}

    # ---- 3b. verifier (LLM-as-judge inside the loop) ---------------------------------------------
    async def verifier(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        relevant = _chunks_from_state(state, s.min_relevance)
        if not relevant:
            return {
                "verdict": {"grounded": True, "score": 1.0, "skipped": "no documents"},
                "trace": ["verifier"],
            }
        human = (
            f"Customer question: {state['user_input']}\n\n{render_documents(relevant)}\n\n"
            f"Draft answer:\n{state['draft']}"
        )
        try:
            verdict: GroundingVerdict = await svc.models.ainvoke_structured(
                "verifier",
                GroundingVerdict,
                [SystemMessage(load_prompt("verifier")), HumanMessage(human)],
                config,
            )
        except StructuredOutputError as exc:
            # ship the draft rather than fail the turn; the output guard still validates citations
            log.warning("verifier.fallback", error=str(exc)[:200])
            return {
                "verdict": {"grounded": True, "score": 0.0, "skipped": "verifier unavailable"},
                "trace": ["verifier"],
            }
        log.info(
            "verifier.verdict", grounded=verdict.grounded, score=verdict.score, attempts=state.get("attempts")
        )
        return {"verdict": verdict.model_dump(), "trace": ["verifier"]}

    # ---- 3c. order agent (bounded tool loop) -----------------------------------------------------
    async def order(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        email = state.get("customer_email")
        tools = make_order_tools(svc.orders, email)
        tool_map = {t.name: t for t in tools}
        model = svc.models.get("order").bind_tools(tools)
        context = (
            f"Verified customer email: {email}" if email else "No verified customer email in this session."
        )
        msgs: list[BaseMessage] = [
            SystemMessage(load_prompt("order_agent") + "\n\n" + context),
            *_history(state),
            HumanMessage(state["user_input"]),
        ]
        ai = AIMessage(content="")
        for _ in range(MAX_TOOL_ROUNDS):
            ai = await model.ainvoke(msgs, _tagged(config, "answer"))
            msgs.append(ai)
            if not ai.tool_calls:
                break
            for call in ai.tool_calls:
                t = tool_map.get(call["name"])
                result = await t.ainvoke(call["args"]) if t else json.dumps({"error": "unknown tool"})
                log.info("order.tool", tool=call["name"], args=call["args"])
                msgs.append(
                    ToolMessage(
                        content=render_tool_result(call["name"], str(result)),
                        tool_call_id=call["id"],
                        name=call["name"],
                    )
                )
        else:
            ai = await model.ainvoke(
                [*msgs, HumanMessage("Reply to the customer now with what you have.")],
                _tagged(config, "answer"),
            )
        text = (
            ai.content
            if isinstance(ai.content, str)
            else "".join(b.get("text", "") for b in ai.content if isinstance(b, dict))
        )
        return {"draft": text, "trace": ["order"]}

    # ---- 3d. escalation --------------------------------------------------------------------------
    async def escalate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        convo = _history(state)
        transcript = "\n".join(
            f"{'Customer' if isinstance(m, HumanMessage) else 'Assistant'}: {m.content}" for m in convo[-6:]
        )
        try:
            summary: TicketSummary = await svc.models.ainvoke_structured(
                "summary",
                TicketSummary,
                [
                    SystemMessage(load_prompt("ticket_summary")),
                    HumanMessage(f"{transcript}\nCustomer: {state['user_input']}"),
                ],
                config,
            )
        except StructuredOutputError as exc:
            log.warning("escalate.fallback", error=str(exc)[:200])
            summary = TicketSummary(summary=state["user_input"][:800], priority="normal")
        conv_id = uuid.UUID(state["conversation_id"]) if state.get("conversation_id") else None
        ticket_id = await svc.tickets.create(
            conversation_id=conv_id,
            customer_email=state.get("customer_email"),
            summary=summary.summary,
            priority=summary.priority,
        )
        short = ticket_id.replace("-", "")[:8].upper()
        draft = (
            f"I've passed this to a colleague. Your ticket reference is {short} and someone will reply "
            f"{'within four hours' if summary.priority == 'high' else 'within one business day'}"
            f"{' at ' + state['customer_email'] if state.get('customer_email') else ''}."
        )
        log.info("escalate.ticket", ticket=ticket_id, priority=summary.priority)
        return {"draft": draft, "ticket_id": ticket_id, "trace": ["escalate"]}

    # ---- 3e. small talk --------------------------------------------------------------------------
    async def smalltalk(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        ai = await svc.models.get("answer").ainvoke(
            [SystemMessage(load_prompt("smalltalk")), *_history(state), HumanMessage(state["user_input"])],
            _tagged(config, "answer"),
        )
        return {"draft": ai.content, "trace": ["smalltalk"]}

    # ---- 3f. blocked -----------------------------------------------------------------------------
    async def blocked(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        return {"draft": BLOCKED_REPLY, "trace": ["blocked"]}

    # ---- 4. finalize (output guard + citation mapping) -----------------------------------------
    async def finalize(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        relevant = _chunks_from_state(state, s.min_relevance) if state.get("route") == "knowledge" else []
        valid = {f"c{i}" for i in range(1, len(relevant) + 1)}
        verdict = guard_output(state.get("draft", ""), valid_citation_ids=valid)
        if verdict.canary_leak or verdict.redactions:
            log.warning("output.guard", canary=verdict.canary_leak, redactions=verdict.redactions)
        citations = []
        for cid in verdict.citations:
            chunk = relevant[int(cid[1:]) - 1]
            citations.append(
                {
                    "id": cid,
                    "source": chunk.source,
                    "title": chunk.title,
                    "section": chunk.metadata.get("section"),
                    "snippet": chunk.content[:240],
                    "score": round(chunk.score, 3),
                }
            )
        return {
            "final_answer": verdict.text,
            "citations": citations,
            "messages": [AIMessage(content=verdict.text)],
            "trace": ["finalize"],
        }

    return {
        "guard": guard,
        "router": router,
        "knowledge": knowledge,
        "verifier": verifier,
        "order": order,
        "escalate": escalate,
        "smalltalk": smalltalk,
        "blocked": blocked,
        "finalize": finalize,
    }
