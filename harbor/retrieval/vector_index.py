"""Dense vector index behind one protocol.

pgvector : vectors live in the chunks table; dense search is a SQL query (see DocumentRepository.dense_search)
pinecone : vectors live in a serverless Pinecone index keyed by chunk id; Postgres stays the system of record
           and hydrates the matched ids. Swapping backends never changes the retriever.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from harbor.config import Settings
from harbor.db.repositories import DocumentRepository, ScoredId


class VectorIndex(Protocol):
    name: str

    async def upsert(self, items: list[tuple[uuid.UUID, list[float], dict[str, Any]]]) -> None: ...

    async def delete(self, ids: list[uuid.UUID]) -> None: ...

    async def search(self, session: AsyncSession, query_vector: list[float], k: int) -> list[ScoredId]: ...

    async def ping(self) -> bool: ...


class PgVectorIndex:
    """No-op writes: the ingestion pipeline stores the embedding on the chunk row itself."""

    name = "pgvector"

    async def upsert(self, items: list[tuple[uuid.UUID, list[float], dict[str, Any]]]) -> None:
        return None

    async def delete(self, ids: list[uuid.UUID]) -> None:
        return None

    async def search(self, session: AsyncSession, query_vector: list[float], k: int) -> list[ScoredId]:
        return await DocumentRepository(session).dense_search(query_vector, k)

    async def ping(self) -> bool:
        return True


class PineconeIndex:
    name = "pinecone"

    def __init__(self, api_key: str, index_name: str, namespace: str, dim: int):
        from pinecone import Pinecone, ServerlessSpec

        self._pc = Pinecone(api_key=api_key)
        self._namespace = namespace
        existing = {idx["name"] for idx in self._pc.list_indexes()}
        if index_name not in existing:
            self._pc.create_index(
                name=index_name,
                dimension=dim,
                metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1"),
            )
        self._index = self._pc.Index(index_name)

    async def upsert(self, items: list[tuple[uuid.UUID, list[float], dict[str, Any]]]) -> None:
        vectors = [{"id": str(cid), "values": vec, "metadata": meta} for cid, vec, meta in items]
        for start in range(0, len(vectors), 100):
            batch = vectors[start : start + 100]
            await asyncio.to_thread(self._index.upsert, vectors=batch, namespace=self._namespace)

    async def delete(self, ids: list[uuid.UUID]) -> None:
        if ids:
            await asyncio.to_thread(self._index.delete, ids=[str(i) for i in ids], namespace=self._namespace)

    async def search(self, session: AsyncSession, query_vector: list[float], k: int) -> list[ScoredId]:
        res = await asyncio.to_thread(
            self._index.query, vector=query_vector, top_k=k, namespace=self._namespace, include_metadata=False
        )
        matches = res.get("matches", []) if isinstance(res, dict) else getattr(res, "matches", [])
        out: list[ScoredId] = []
        for m in matches:
            mid = m["id"] if isinstance(m, dict) else m.id
            score = m["score"] if isinstance(m, dict) else m.score
            out.append(ScoredId(chunk_id=uuid.UUID(mid), score=float(score)))
        return out

    async def ping(self) -> bool:
        try:
            await asyncio.to_thread(self._index.describe_index_stats)
            return True
        except Exception:
            return False


def build_vector_index(settings: Settings) -> VectorIndex:
    if settings.vector_backend == "pinecone":
        if not settings.pinecone_api_key:
            raise RuntimeError("VECTOR_BACKEND=pinecone requires PINECONE_API_KEY")
        return PineconeIndex(
            settings.pinecone_api_key,
            settings.pinecone_index,
            settings.pinecone_namespace,
            settings.embedding_dim,
        )
    return PgVectorIndex()
