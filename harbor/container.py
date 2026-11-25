"""Composition root. Two builders:

build_offline_container : no Postgres, no keys. In-memory knowledge index + scripted LLM + MemorySaver.
                          Used by unit tests, the API test-suite and `harbor demo`.
build_container         : the real thing. Postgres (pgvector + checkpoints), Claude, Voyage/Pinecone, speech.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from harbor.agents.graph import build_graph
from harbor.agents.models import ModelFactory
from harbor.agents.nodes import Services
from harbor.agents.runtime import AgentRuntime
from harbor.config import Settings
from harbor.db.repositories import OrderRepository, ScoredId, TicketRepository
from harbor.guard.injection import scan_text
from harbor.ingest.chunker import chunk_markdown
from harbor.ingest.loader import load_path
from harbor.ingest.pipeline import QUARANTINE_THRESHOLD
from harbor.retrieval.embeddings import FakeEmbeddings, build_embeddings
from harbor.retrieval.fusion import reciprocal_rank_fusion
from harbor.retrieval.rerank import FakeReranker, LLMReranker, NoopReranker, Reranker, VoyageReranker
from harbor.retrieval.types import RetrievedChunk
from harbor.retrieval.vector_index import build_vector_index
from harbor.voice.speech import FakeSpeech, SpeechProvider, build_speech

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"
SEED_DIR = Path(__file__).resolve().parent.parent / "seeds"


# --------------------------------------------------------------------------------------------------
# In-memory implementations (offline mode)
# --------------------------------------------------------------------------------------------------
class InMemoryRetriever:
    """Same hybrid algorithm as HybridRetriever (dense cosine + lexical overlap -> RRF -> rerank), in RAM."""

    _tok = re.compile(r"[a-z0-9]+")

    def __init__(self, knowledge_dir: Path, settings: Settings, reranker: Reranker | None = None):
        self._s = settings
        self._emb = FakeEmbeddings(settings.embedding_dim)
        self._reranker = reranker or FakeReranker()
        self.chunks: list[RetrievedChunk] = []
        self.vectors: dict[uuid.UUID, list[float]] = {}
        self.quarantined: list[str] = []
        for doc in load_path(knowledge_dir):
            doc_id = uuid.uuid5(uuid.NAMESPACE_URL, doc.source)
            for d in chunk_markdown(doc.text, title=doc.title):
                if scan_text(d.content).score >= QUARANTINE_THRESHOLD:
                    self.quarantined.append(f"{doc.source}#{d.ordinal}")
                    continue
                cid = uuid.uuid5(doc_id, str(d.ordinal))
                self.chunks.append(
                    RetrievedChunk(
                        chunk_id=cid,
                        document_id=doc_id,
                        source=doc.source,
                        title=doc.title,
                        content=d.content,
                        score=0.0,
                        metadata=d.metadata,
                    )
                )
                self.vectors[cid] = self._emb._embed(d.embed_text)
        # BM25 statistics for the lexical leg (mirrors Postgres ts_rank_cd in spirit, not in formula)
        self._doc_terms: dict[uuid.UUID, list[str]] = {
            c.chunk_id: [
                FakeEmbeddings.stem(t)
                for t in self._tok.findall((c.metadata.get("breadcrumb", "") + " " + c.content).lower())
            ]
            for c in self.chunks
        }
        self._avg_len = (
            (sum(len(t) for t in self._doc_terms.values()) / len(self._doc_terms)) if self._doc_terms else 1.0
        )
        self._df: dict[str, int] = {}
        for terms in self._doc_terms.values():
            for term in set(terms):
                self._df[term] = self._df.get(term, 0) + 1

    def _lexical(self, query: str, k: int) -> list[ScoredId]:
        """Okapi BM25 (k1=1.5, b=0.75) over chunk tokens."""
        q = [FakeEmbeddings.stem(t) for t in self._tok.findall(query.lower()) if len(t) > 2]
        n = len(self._doc_terms) or 1
        scored = []
        for cid, terms in self._doc_terms.items():
            if not terms:
                continue
            score = 0.0
            length = len(terms)
            counts: dict[str, int] = {}
            for t in terms:
                counts[t] = counts.get(t, 0) + 1
            for t in q:
                tf = counts.get(t, 0)
                if not tf:
                    continue
                idf = math.log(1 + (n - self._df.get(t, 0) + 0.5) / (self._df.get(t, 0) + 0.5))
                score += idf * (tf * 2.5) / (tf + 1.5 * (1 - 0.75 + 0.75 * length / self._avg_len))
            if score > 0:
                scored.append(ScoredId(chunk_id=cid, score=score))
        scored.sort(key=lambda x: -x.score)
        return scored[:k]

    def _dense(self, query: str, k: int) -> list[ScoredId]:
        qv = self._emb._embed(query)
        scored = []
        for cid, vec in self.vectors.items():
            dot = sum(a * b for a, b in zip(qv, vec, strict=True))
            if dot > 0:
                scored.append(ScoredId(chunk_id=cid, score=dot))
        scored.sort(key=lambda x: -x.score)
        return scored[:k]

    async def retrieve(self, query: str, *, top_n: int | None = None) -> list[RetrievedChunk]:
        top_n = top_n or self._s.rerank_top_n
        fused = reciprocal_rank_fusion(
            self._dense(query, self._s.dense_top_k),
            self._lexical(query, self._s.lexical_top_k),
            k=self._s.rrf_k,
        )
        by_id = {c.chunk_id: c for c in self.chunks}
        candidates = []
        for h in fused[: max(top_n * 4, 12)]:
            base = by_id[h.chunk_id]
            candidates.append(
                RetrievedChunk(
                    chunk_id=base.chunk_id,
                    document_id=base.document_id,
                    source=base.source,
                    title=base.title,
                    content=base.content,
                    score=h.fused_score,
                    dense_rank=h.dense_rank,
                    lexical_rank=h.lexical_rank,
                    fused_score=h.fused_score,
                    metadata=base.metadata,
                )
            )
        return await self._reranker.rerank(query, candidates, top_n)


class InMemoryOrders:
    def __init__(self, customers: list[dict[str, Any]], orders: list[dict[str, Any]]):
        self._orders = [dict(o) for o in orders]
        self._names = {c["email"]: c["name"] for c in customers}

    async def get_order(self, order_number: str, email: str) -> dict[str, Any] | None:
        for o in self._orders:
            if (
                o["order_number"] == order_number.upper().strip()
                and o["customer_email"] == email.lower().strip()
            ):
                return {
                    **{k: v for k, v in o.items() if k != "customer_email"},
                    "customer_name": self._names.get(email, ""),
                }
        return None

    async def list_orders(self, email: str, limit: int = 5) -> list[dict[str, Any]]:
        rows = [o for o in self._orders if o["customer_email"] == email.lower().strip()]
        rows.sort(key=lambda o: o["placed_at"], reverse=True)
        return [{k: v for k, v in o.items() if k != "customer_email"} for o in rows[:limit]]


class InMemoryTickets:
    def __init__(self):
        self.created: list[dict[str, Any]] = []

    async def create(self, *, conversation_id, customer_email, summary, priority) -> str:
        tid = str(uuid.uuid4())
        self.created.append(
            {
                "id": tid,
                "conversation_id": conversation_id,
                "customer_email": customer_email,
                "summary": summary,
                "priority": priority,
            }
        )
        return tid


# --------------------------------------------------------------------------------------------------
# Postgres-backed services
# --------------------------------------------------------------------------------------------------
class DbOrders:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self._sessions = sessions

    async def get_order(self, order_number: str, email: str) -> dict[str, Any] | None:
        async with self._sessions() as session:
            return await OrderRepository(session).get_order(order_number, email)

    async def list_orders(self, email: str, limit: int = 5) -> list[dict[str, Any]]:
        async with self._sessions() as session:
            return await OrderRepository(session).list_orders(email, limit)


class DbTickets:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self._sessions = sessions

    async def create(self, *, conversation_id, customer_email, summary, priority) -> str:
        async with self._sessions() as session:
            ticket = await TicketRepository(session).create(
                conversation_id=conversation_id,
                customer_email=customer_email,
                summary=summary,
                priority=priority,
            )
            await session.commit()
            return str(ticket.id)


# --------------------------------------------------------------------------------------------------
# Container
# --------------------------------------------------------------------------------------------------
@dataclass
class Container:
    settings: Settings
    services: Services
    runtime: AgentRuntime
    speech: SpeechProvider
    engine: AsyncEngine | None = None
    sessions: async_sessionmaker[AsyncSession] | None = None
    ingestion: Any = None
    checkpointer: Any = None
    _pool: Any = None

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
        if self.engine is not None:
            await self.engine.dispose()

    async def ready(self) -> dict[str, bool]:
        checks = {"llm": True, "vector_index": True, "database": True}
        if self.engine is not None:
            try:
                async with self.engine.connect() as conn:
                    from sqlalchemy import text

                    await conn.execute(text("SELECT 1"))
            except Exception:
                checks["database"] = False
        return checks


def _seed_data() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import json

    data = json.loads((SEED_DIR / "orders.json").read_text(encoding="utf-8"))
    return data["customers"], data["orders"]


def build_reranker(settings: Settings, models: ModelFactory) -> Reranker:
    if settings.llm_provider == "fake" and settings.reranker != "none":
        return FakeReranker()
    if settings.reranker == "voyage":
        if not settings.voyage_api_key:
            raise RuntimeError("RERANKER=voyage requires VOYAGE_API_KEY")
        return VoyageReranker(settings.voyage_api_key, settings.rerank_model)
    if settings.reranker == "llm":
        from harbor.prompts import load_prompt

        return LLMReranker(models.get("rerank"), load_prompt("rerank"))
    return NoopReranker()


def build_offline_container(settings: Settings, knowledge_dir: Path | None = None) -> Container:
    settings = settings.model_copy(
        update={
            "llm_provider": "fake",
            "embeddings_provider": "fake",
            "speech_provider": "fake",
            "reranker": "llm" if settings.reranker == "llm" else "none",
        }
    )
    models = ModelFactory(settings)
    customers, orders = _seed_data()
    services = Services(
        settings=settings,
        models=models,
        retriever=InMemoryRetriever(knowledge_dir or KNOWLEDGE_DIR, settings, reranker=FakeReranker()),
        orders=InMemoryOrders(customers, orders),
        tickets=InMemoryTickets(),
    )
    graph = build_graph(services, checkpointer=MemorySaver())
    return Container(
        settings=settings, services=services, runtime=AgentRuntime(graph, None), speech=FakeSpeech()
    )


async def build_container(settings: Settings) -> Container:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    from harbor.db.engine import build_engine, build_session_factory
    from harbor.ingest.pipeline import IngestionPipeline
    from harbor.retrieval.retriever import HybridRetriever

    engine = build_engine(settings.database_url)
    sessions = build_session_factory(engine)
    models = ModelFactory(settings)
    embeddings = build_embeddings(settings)
    index = build_vector_index(settings)
    reranker = build_reranker(settings, models)
    retriever = HybridRetriever(
        session_factory=sessions, embeddings=embeddings, index=index, reranker=reranker, settings=settings
    )

    pool = AsyncConnectionPool(
        conninfo=settings.psycopg_dsn,
        min_size=1,
        max_size=10,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    await pool.open()
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()

    services = Services(
        settings=settings,
        models=models,
        retriever=retriever,
        orders=DbOrders(sessions),
        tickets=DbTickets(sessions),
    )
    graph = build_graph(services, checkpointer=checkpointer)
    speech = FakeSpeech() if settings.speech_provider == "fake" else build_speech(settings)
    return Container(
        settings=settings,
        services=services,
        runtime=AgentRuntime(graph, sessions),
        speech=speech,
        engine=engine,
        sessions=sessions,
        ingestion=IngestionPipeline(session_factory=sessions, embeddings=embeddings, index=index),
        checkpointer=checkpointer,
        _pool=pool,
    )
