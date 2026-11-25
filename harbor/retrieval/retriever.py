"""Hybrid retriever: dense (vector index) + lexical (Postgres FTS) -> RRF -> rerank -> relevance gate."""

from __future__ import annotations

import time
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harbor.config import Settings
from harbor.db.repositories import DocumentRepository
from harbor.logging import get_logger
from harbor.retrieval.embeddings import EmbeddingsProvider
from harbor.retrieval.fusion import reciprocal_rank_fusion
from harbor.retrieval.rerank import Reranker
from harbor.retrieval.types import RetrievedChunk
from harbor.retrieval.vector_index import VectorIndex

log = get_logger(__name__)


class Retriever(Protocol):
    async def retrieve(self, query: str, *, top_n: int | None = None) -> list[RetrievedChunk]: ...


class HybridRetriever:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        embeddings: EmbeddingsProvider,
        index: VectorIndex,
        reranker: Reranker,
        settings: Settings,
    ):
        self._sessions = session_factory
        self._embeddings = embeddings
        self._index = index
        self._reranker = reranker
        self._s = settings

    async def retrieve(self, query: str, *, top_n: int | None = None) -> list[RetrievedChunk]:
        started = time.perf_counter()
        top_n = top_n or self._s.rerank_top_n
        query_vector = await self._embeddings.embed_query(query)

        async with self._sessions() as session:
            repo = DocumentRepository(session)
            dense = await self._index.search(session, query_vector, self._s.dense_top_k)
            lexical = await repo.lexical_search(query, self._s.lexical_top_k)
            fused = reciprocal_rank_fusion(dense, lexical, k=self._s.rrf_k)
            candidate_ids = [h.chunk_id for h in fused[: max(top_n * 4, 12)]]
            chunks = await repo.get_chunks(candidate_ids)

        candidates: list[RetrievedChunk] = []
        for hit in fused:
            chunk = chunks.get(hit.chunk_id)
            if chunk is None:
                continue
            candidates.append(
                RetrievedChunk(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    source=chunk.document.source,
                    title=chunk.document.title,
                    content=chunk.content,
                    score=hit.fused_score,
                    dense_rank=hit.dense_rank,
                    lexical_rank=hit.lexical_rank,
                    fused_score=hit.fused_score,
                    metadata=dict(chunk.metadata_ or {}),
                )
            )

        reranked = await self._reranker.rerank(query, candidates, top_n)
        log.info(
            "retrieval.done",
            dense=len(dense),
            lexical=len(lexical),
            fused=len(fused),
            returned=len(reranked),
            top_score=round(reranked[0].score, 3) if reranked else None,
            reranker=self._reranker.name,
            index=self._index.name,
            ms=int((time.perf_counter() - started) * 1000),
        )
        return reranked
