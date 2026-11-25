"""Rerankers. After fusion we have maybe 30 candidates; a cross-encoder style reranker reads query and
passage together and produces a far better top-6 than either bi-encoder similarity or BM25 alone.

voyage : rerank-2.5 (hosted cross-encoder)
llm    : Claude Haiku scores each candidate 0-10 in one listwise call (no extra vendor, slower, pricier)
none   : keep the fused order (useful as a baseline in evals)
"""

from __future__ import annotations

import asyncio
import re
from typing import Protocol

from pydantic import BaseModel, Field

from harbor.retrieval.types import RetrievedChunk


class Reranker(Protocol):
    name: str

    async def rerank(
        self, query: str, candidates: list[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]: ...


class NoopReranker:
    name = "none"

    async def rerank(self, query: str, candidates: list[RetrievedChunk], top_n: int) -> list[RetrievedChunk]:
        out = []
        for c in candidates[:top_n]:
            c.score = c.fused_score * 10  # keep scores roughly in the 0-1 range for the relevance gate
            out.append(c)
        return out


class VoyageReranker:
    name = "voyage"

    def __init__(self, api_key: str, model: str):
        import voyageai

        self._client = voyageai.AsyncClient(api_key=api_key)
        self._model = model

    async def rerank(self, query: str, candidates: list[RetrievedChunk], top_n: int) -> list[RetrievedChunk]:
        if not candidates:
            return []
        res = await self._client.rerank(
            query=query, documents=[c.content for c in candidates], model=self._model, top_k=top_n
        )
        out: list[RetrievedChunk] = []
        for r in res.results:
            chunk = candidates[r.index]
            chunk.score = float(r.relevance_score)
            out.append(chunk)
        return out


class _Scores(BaseModel):
    scores: list[int] = Field(description="One integer 0-10 per candidate, in the given order")


class LLMReranker:
    """Listwise pointwise scoring with a small model. `model` is any LangChain chat model."""

    name = "llm"

    def __init__(self, model, prompt: str):
        self._structured = model.with_structured_output(_Scores)
        self._prompt = prompt

    async def rerank(self, query: str, candidates: list[RetrievedChunk], top_n: int) -> list[RetrievedChunk]:
        if not candidates:
            return []
        listing = "\n\n".join(f"[{i}] {c.content[:1200]}" for i, c in enumerate(candidates))
        result: _Scores = await self._structured.ainvoke(
            [("system", self._prompt), ("human", f"Query: {query}\n\nCandidates:\n{listing}")]
        )
        scores = list(result.scores) + [0] * (len(candidates) - len(result.scores))
        for c, s in zip(candidates, scores, strict=False):
            c.score = max(0, min(10, int(s))) / 10.0
        ranked = sorted(candidates, key=lambda c: -c.score)
        return ranked[:top_n]


class FakeReranker:
    """Token overlap, for offline mode."""

    name = "fake"
    _tok = re.compile(r"[a-z0-9]+")

    async def rerank(self, query: str, candidates: list[RetrievedChunk], top_n: int) -> list[RetrievedChunk]:
        q = {t for t in self._tok.findall(query.lower()) if len(t) > 2}
        for c in candidates:
            words = {t for t in self._tok.findall(c.content.lower()) if len(t) > 2}
            c.score = len(q & words) / (len(q) or 1)
        await asyncio.sleep(0)
        return sorted(candidates, key=lambda c: (-c.score, -c.fused_score))[:top_n]
