"""Ragas metrics (faithfulness, answer relevancy, context precision, context recall) on the new collections API.

Ragas needs an LLM that returns structured output (instructor) and an embedding model; both are built from the
same settings as the app so the judge runs on Claude and the embeddings are the production ones.
"""

from __future__ import annotations

from typing import Any

from harbor.config import Settings
from harbor.evals.metrics import safe_mean
from harbor.retrieval.embeddings import EmbeddingsProvider


class _RagasEmbeddings:
    """Adapter from our EmbeddingsProvider to ragas BaseRagasEmbedding."""

    def __init__(self, provider: EmbeddingsProvider):
        from ragas.embeddings.base import BaseRagasEmbedding

        self._provider = provider

        class _Impl(BaseRagasEmbedding):
            def __init__(inner):
                super().__init__()

            def embed_text(inner, text: str, **kwargs: Any) -> list[float]:
                import asyncio

                return asyncio.get_event_loop().run_until_complete(provider.embed_query(text))

            async def aembed_text(inner, text: str, **kwargs: Any) -> list[float]:
                return await provider.embed_query(text)

        self.impl = _Impl()


def build_ragas_llm(settings: Settings):
    import instructor
    from ragas.llms import llm_factory

    if settings.llm_provider == "openai_compatible":
        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url=settings.openai_compat_base_url, api_key=settings.openai_compat_api_key)
        return llm_factory(
            settings.openai_compat_verifier_model,
            provider="openai",
            client=client,
            mode=instructor.Mode.JSON,
            max_tokens=2048,
        )

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return llm_factory(
        settings.verifier_model,
        provider="anthropic",
        client=client,
        mode=instructor.Mode.ANTHROPIC_JSON,
        max_tokens=2048,
    )


async def ragas_scores(
    settings: Settings, embeddings: EmbeddingsProvider, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    from ragas.metrics.collections import (
        AnswerRelevancy,
        ContextPrecisionWithReference,
        ContextRecall,
        Faithfulness,
    )

    llm = build_ragas_llm(settings)
    emb = _RagasEmbeddings(embeddings).impl
    faith, relevancy = Faithfulness(llm=llm), AnswerRelevancy(llm=llm, embeddings=emb)
    precision, recall = ContextPrecisionWithReference(llm=llm), ContextRecall(llm=llm)

    per_row: list[dict[str, float | None]] = []
    for r in rows:
        if not r.get("contexts"):
            continue
        scores: dict[str, float | None] = {}
        for name, coro in (
            (
                "faithfulness",
                faith.ascore(
                    user_input=r["question"], response=r["answer"], retrieved_contexts=r["contexts"]
                ),
            ),
            ("answer_relevancy", relevancy.ascore(user_input=r["question"], response=r["answer"])),
            (
                "context_precision",
                precision.ascore(
                    user_input=r["question"], reference=r["reference"], retrieved_contexts=r["contexts"]
                ),
            ),
            (
                "context_recall",
                recall.ascore(
                    user_input=r["question"], retrieved_contexts=r["contexts"], reference=r["reference"]
                ),
            ),
        ):
            try:
                scores[name] = float((await coro).value)
            except Exception as exc:  # one failing metric must not sink the run
                scores[name] = None
                scores[f"{name}_error"] = str(exc)[:200]  # type: ignore[assignment]
        r["ragas"] = scores
        per_row.append(scores)

    return {
        key: safe_mean([s.get(key) for s in per_row])
        for key in ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
    }
