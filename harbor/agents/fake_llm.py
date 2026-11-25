"""A deterministic stand-in for Claude so the whole graph, API and eval harness run with no network.

Each role gets simple rules that mimic what the real prompt asks the model to do. This is not a mock that
returns fixed strings: it reads the documents block, follows the retry feedback, emits tool calls and
streams tokens, so the surrounding code paths are the same ones production runs.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import PrivateAttr

from harbor.guard.injection import scan_text

_ORDER_NO = re.compile(r"\b(HO-?\d{5,}|\d{6,})\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_DOC = re.compile(r'<document id="(c\d+)"[^>]*title="([^"]*)"[^>]*>\n(.*?)\n</document>', re.S)


def _last_human(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


def _system(messages: list[BaseMessage]) -> str:
    return "\n".join(
        m.content for m in messages if isinstance(m, SystemMessage) and isinstance(m.content, str)
    )


class ScriptedChatModel(BaseChatModel):
    role: str = "answer"
    _tool_names: list[str] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    # ---- LangChain plumbing -----------------------------------------------------------------------
    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable:
        bound = ScriptedChatModel(role=self.role)
        bound._tool_names = [getattr(t, "name", getattr(t, "__name__", str(t))) for t in tools]
        return bound

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable:
        def _parse(input_: Any) -> Any:
            raw = self.invoke(input_).content
            return (
                schema.model_validate_json(raw) if hasattr(schema, "model_validate_json") else json.loads(raw)
            )

        return RunnableLambda(_parse)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._respond(messages))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        msg = self._respond(messages)
        if msg.tool_calls:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_calls=msg.tool_calls))
            return
        words = msg.content.split(" ")
        for i, w in enumerate(words):
            piece = w if i == len(words) - 1 else w + " "
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=piece))
            if run_manager:
                run_manager.on_llm_new_token(piece, chunk=chunk)
            yield chunk

    # ---- role behaviours ---------------------------------------------------------------------------
    def _respond(self, messages: list[BaseMessage]) -> AIMessage:
        handler = getattr(self, f"_role_{self.role}", self._role_answer)
        return handler(messages)

    def _role_router(self, messages: list[BaseMessage]) -> AIMessage:
        text = _last_human(messages).split("Latest customer message:")[-1].strip().lower()
        if re.search(
            r"\b(speak|talk|chat) (to|with) (a |an |someone|somebody)?\s?(human|agent|person|manager|colleague)|\b(hacked|lawyer|legal action|sue)\b",
            text,
        ):
            intent = "escalate"
        elif _ORDER_NO.search(text) or re.search(
            r"\b(where is my|track(ing)?|status of my|has my (order|parcel) (shipped|arrived)|my recent orders)\b",
            text,
        ):
            intent = "order"
        elif (
            re.match(r"^(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening))\b", text)
            and len(text) < 60
            or re.search(r"\b(poem|python|weather|joke|recipe)\b", text)
        ):
            intent = "smalltalk"
        else:
            intent = "knowledge"
        return AIMessage(content=json.dumps({"intent": intent, "confidence": 0.9, "reason": "keyword rule"}))

    def _role_guard(self, messages: list[BaseMessage]) -> AIMessage:
        text = _last_human(messages)
        h = scan_text(text)
        asked_about = (
            bool(re.search(r"\b(what does|what do|explain|meaning of|is it true that)\b", text, re.I))
            and h.score < 0.9
        )
        cat = {
            "override_instructions": "override",
            "persona_switch": "persona",
            "jailbreak": "persona",
            "prompt_leak": "prompt_leak",
            "template_tokens": "override",
            "markdown_exfiltration": "data_exfiltration",
            "exfiltration": "data_exfiltration",
            "secret_request": "prompt_leak",
            "cross_customer_data": "cross_customer",
        }.get(h.category or "", "none")
        is_inj = h.score >= 0.4 and not asked_about
        return AIMessage(
            content=json.dumps(
                {
                    "is_injection": is_inj,
                    "category": cat if is_inj else "none",
                    "confidence": 0.95 if is_inj else 0.1,
                    "rationale": f"pattern score {h.score:.2f}",
                }
            )
        )

    def _role_answer(self, messages: list[BaseMessage]) -> AIMessage:
        system = _system(messages)
        human = _last_human(messages)
        docs = _DOC.findall(human)
        if "small talk" in system:
            return AIMessage(
                content="Hi there! I can help with orders, shipping, returns, warranty, payments or product care."
            )
        question = human.split("Customer question:")[-1].strip()
        retry = "verifier found problems" in system
        if not docs:
            return AIMessage(
                content="I couldn't find that in our documentation, so I'd rather not guess. "
                "I can create a support ticket so a colleague answers you directly."
            )
        q_terms = {t for t in re.findall(r"[a-z0-9]+", question.lower()) if len(t) > 3}
        sentences = []
        for cid, _title, body in docs[:2]:
            candidates = re.split(r"(?<=[.!?])\s+", body.strip())
            best = max(
                candidates, key=lambda sent: len(q_terms & set(re.findall(r"[a-z0-9]+", sent.lower())))
            )
            sentences.append(f"{best} [{cid}]")
        text = " ".join(sentences)
        if "UNGROUNDED_TEST" in question and not retry:
            text += " Deliveries to the moon take two days [c9]."
        return AIMessage(content=text)

    def _role_verifier(self, messages: list[BaseMessage]) -> AIMessage:
        human = _last_human(messages)
        draft = human.split("Draft answer:")[-1]
        bad = "moon" in draft.lower()
        return AIMessage(
            content=json.dumps(
                {
                    "grounded": not bad,
                    "score": 0.2 if bad else 0.95,
                    "unsupported_claims": ["Deliveries to the moon take two days"] if bad else [],
                    "fix_instruction": "Remove the claim about moon deliveries." if bad else "",
                }
            )
        )

    def _role_order(self, messages: list[BaseMessage]) -> AIMessage:
        last = messages[-1]
        if isinstance(last, ToolMessage):
            raw = last.content if isinstance(last.content, str) else str(last.content)
            try:
                data = (
                    json.loads(raw.split("\n", 1)[1].rsplit("\n", 1)[0])
                    if raw.startswith("<tool_result")
                    else json.loads(raw)
                )
            except json.JSONDecodeError:
                data = {}
            if isinstance(data, dict) and data.get("status"):
                parts = [f"Your order {data['order_number']} is {data['status']}."]
                if data.get("carrier"):
                    parts.append(
                        f"It ships with {data['carrier']}, tracking number {data.get('tracking_number')}."
                    )
                return AIMessage(content=" ".join(parts))
            if isinstance(data, list) and data:
                return AIMessage(
                    content="Your recent orders: "
                    + ", ".join(f"{o['order_number']} ({o['status']})" for o in data)
                    + "."
                )
            return AIMessage(
                content="I couldn't find an order with those details for your email. Please check your confirmation email or contact support."
            )
        human = _last_human(messages)
        system = _system(messages)
        email_match = re.search(r"Verified customer email: (\S+)", system) or _EMAIL.search(human)
        order = _ORDER_NO.search(human)
        if order and email_match and "lookup_order" in self._tool_names:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "lookup_order",
                        "args": {
                            "order_number": order.group(1).upper(),
                            "email": email_match.group(1).rstrip("."),
                        },
                        "id": "call_lookup_1",
                        "type": "tool_call",
                    }
                ],
            )
        if email_match and "list_recent_orders" in self._tool_names and not order:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_recent_orders",
                        "args": {"email": email_match.group(1).rstrip(".")},
                        "id": "call_list_1",
                        "type": "tool_call",
                    }
                ],
            )
        return AIMessage(content="Could you share the email address used for the order and the order number?")

    def _role_summary(self, messages: list[BaseMessage]) -> AIMessage:
        text = _last_human(messages)
        high = bool(re.search(r"twice|hacked|lawyer|legal|injur", text, re.I))
        return AIMessage(
            content=json.dumps({"summary": text[-300:].strip(), "priority": "high" if high else "normal"})
        )

    def _role_judge(self, messages: list[BaseMessage]) -> AIMessage:
        human = _last_human(messages)
        answer = human.split("Assistant answer:")[-1].lower()
        ok = "couldn't find" not in answer and "[c" in answer
        s = 5 if ok else 2
        return AIMessage(
            content=json.dumps(
                {
                    "correctness": s,
                    "groundedness": s,
                    "completeness": s,
                    "tone": 4,
                    "pass": ok,
                    "rationale": "fake judge",
                }
            )
        )

    def _role_rerank(self, messages: list[BaseMessage]) -> AIMessage:
        human = _last_human(messages)
        query = human.split("Query:")[-1].split("Candidates:")[0].lower()
        q = {t for t in re.findall(r"[a-z0-9]+", query) if len(t) > 2}
        scores = []
        for _, body in re.findall(r"\[(\d+)\] (.*?)(?=\n\n\[\d+\] |\Z)", human, re.S):
            words = {t for t in re.findall(r"[a-z0-9]+", body.lower()) if len(t) > 2}
            scores.append(min(10, int(10 * len(q & words) / (len(q) or 1))))
        return AIMessage(content=json.dumps({"scores": scores}))
