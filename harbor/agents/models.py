"""Chat model factory. One place decides which Claude model plays which role and how structured output is
obtained, so the rest of the code never mentions a vendor."""

from __future__ import annotations

from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable

from harbor.config import Settings

Role = Literal["answer", "router", "guard", "verifier", "rerank", "order", "summary", "judge"]

_NO_FORCED_TOOL_CHOICE = ("claude-opus-5", "claude-sonnet-5", "claude-fable", "claude-mythos")
_SUPPORTS_EFFORT = ("claude-opus-5", "claude-sonnet-5", "claude-opus-4-7", "claude-opus-4-8", "claude-fable")


def _model_for_role(settings: Settings, role: Role) -> tuple[str, int, str | None]:
    """(model id, max_tokens, effort)"""
    if role in ("answer", "order"):
        return settings.answer_model, settings.answer_max_tokens, settings.answer_effort
    if role in ("verifier", "judge"):
        return settings.verifier_model, 1024, "low"
    return settings.fast_model, 512, "low"


def structured_method(model_id: str) -> Literal["function_calling", "json_schema"]:
    """Opus 5.5 / Sonnet 5.5 reject forced tool choice, so use Claude's native structured outputs there."""
    return "json_schema" if model_id.startswith(_NO_FORCED_TOOL_CHOICE) else "function_calling"


class ModelFactory:
    def __init__(self, settings: Settings):
        self._s = settings
        self._cache: dict[Role, BaseChatModel] = {}

    def get(self, role: Role) -> BaseChatModel:
        if role not in self._cache:
            self._cache[role] = self._build(role)
        return self._cache[role]

    def structured(self, role: Role, schema: type) -> Runnable:
        model = self.get(role)
        if self._s.llm_provider == "fake":
            return model.with_structured_output(schema)
        model_id, _, _ = _model_for_role(self._s, role)
        return model.with_structured_output(schema, method=structured_method(model_id))

    def _build(self, role: Role) -> BaseChatModel:
        if self._s.llm_provider == "fake":
            from harbor.agents.fake_llm import ScriptedChatModel

            return ScriptedChatModel(role=role)

        from langchain_anthropic import ChatAnthropic

        model_id, max_tokens, effort = _model_for_role(self._s, role)
        kwargs: dict = {}
        if effort and model_id.startswith(_SUPPORTS_EFFORT):
            kwargs["output_config"] = {"effort": effort}
        return ChatAnthropic(
            model=model_id,
            api_key=self._s.anthropic_api_key,
            max_tokens=max_tokens,
            max_retries=3,
            timeout=90,
            **kwargs,
        )
