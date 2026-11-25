"""Chat model factory. One place decides which Claude model plays which role and how structured output is
obtained, so the rest of the code never mentions a vendor."""

from __future__ import annotations

from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable, RunnableConfig

from harbor.config import Settings
from harbor.logging import get_logger

Role = Literal["answer", "router", "guard", "verifier", "rerank", "order", "summary", "judge"]
log = get_logger(__name__)


class StructuredOutputError(RuntimeError):
    """The model did not produce a valid instance of the requested schema after the allowed attempts."""


_NO_FORCED_TOOL_CHOICE = ("claude-opus-5", "claude-sonnet-5", "claude-fable", "claude-mythos")
_SUPPORTS_EFFORT = ("claude-opus-5", "claude-sonnet-5", "claude-opus-4-7", "claude-opus-4-8", "claude-fable")


def _model_for_role(settings: Settings, role: Role) -> tuple[str, int, str | None]:
    """(model id, max_tokens, effort)"""
    if role in ("answer", "order"):
        return settings.answer_model, settings.answer_max_tokens, settings.answer_effort
    if role in ("verifier", "judge"):
        return settings.verifier_model, 1024, "low"
    return settings.fast_model, 512, "low"


def _compat_model_for_role(settings: Settings, role: Role) -> str:
    if role in ("answer", "order"):
        return settings.openai_compat_answer_model
    if role in ("verifier", "judge"):
        return settings.openai_compat_verifier_model
    return settings.openai_compat_fast_model


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
        if self._s.llm_provider == "openai_compatible":
            return model.with_structured_output(schema, method=self._s.openai_compat_structured_method)
        model_id, _, _ = _model_for_role(self._s, role)
        return model.with_structured_output(schema, method=structured_method(model_id))

    async def ainvoke_structured(
        self,
        role: Role,
        schema: type,
        messages: list,
        config: RunnableConfig | None = None,
        attempts: int = 2,
    ):
        """Invoke a structured-output model with retries. Small or flaky models sometimes return an empty tool
        call or malformed JSON; a second attempt with a corrective note fixes most of those. Raises
        StructuredOutputError when every attempt fails so the caller can apply a safe default."""
        runnable = self.structured(role, schema)
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return await runnable.ainvoke(messages, config)
            except Exception as exc:  # pydantic ValidationError, OutputParserException, provider errors
                last = exc
                log.warning("structured.retry", role=role, attempt=attempt, error=str(exc)[:200])
                if attempt < attempts:
                    from langchain_core.messages import HumanMessage

                    messages = [
                        *messages,
                        HumanMessage(
                            "Your previous reply was not valid. Fill in every field of the schema with a value."
                        ),
                    ]
        raise StructuredOutputError(f"{role}: {last}") from last

    def model_id(self, role: Role) -> str:
        if self._s.llm_provider == "fake":
            return "fake"
        if self._s.llm_provider == "openai_compatible":
            return _compat_model_for_role(self._s, role)
        return _model_for_role(self._s, role)[0]

    def _build(self, role: Role) -> BaseChatModel:
        if self._s.llm_provider == "fake":
            from harbor.agents.fake_llm import ScriptedChatModel

            return ScriptedChatModel(role=role)

        if self._s.llm_provider == "openai_compatible":
            from langchain_openai import ChatOpenAI

            _, max_tokens, _ = _model_for_role(self._s, role)
            return ChatOpenAI(
                model=_compat_model_for_role(self._s, role),
                base_url=self._s.openai_compat_base_url,
                api_key=self._s.openai_compat_api_key,
                max_tokens=max_tokens,
                temperature=0,
                max_retries=3,
                timeout=120,
            )

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
