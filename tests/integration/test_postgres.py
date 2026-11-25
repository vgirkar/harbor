"""End-to-end against a real Postgres with pgvector (docker compose up db). Fake LLM and fake embeddings so
no keys are needed; what is under test is the SQL, the hybrid search, the checkpointer and persistence."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
DB = os.environ.get("DATABASE_URL")


@pytest.fixture(scope="module")
async def pg_container():
    if not DB:
        pytest.skip("DATABASE_URL not set")
    import subprocess

    from harbor.config import Settings
    from harbor.container import build_container

    subprocess.run(["alembic", "upgrade", "head"], check=True, capture_output=True)
    settings = Settings(
        _env_file=None,
        database_url=DB,
        llm_provider="fake",
        embeddings_provider="fake",
        speech_provider="fake",
        reranker="none",
    )
    c = await build_container(settings)
    try:
        yield c
    finally:
        await c.close()


async def test_ingest_is_idempotent_and_quarantines(pg_container):
    first = await pg_container.ingestion.ingest_path(Path("knowledge"), force=True)
    assert first.documents_updated == 8 and first.chunks_written > 20 and first.chunks_quarantined == 0
    second = await pg_container.ingestion.ingest_path(Path("knowledge"))
    assert second.documents_unchanged == 8 and second.chunks_written == 0
    poisoned = await pg_container.ingestion.ingest_path(Path("tests/fixtures/poisoned.md"), force=True)
    assert poisoned.chunks_quarantined == 1


async def test_hybrid_search_sql(pg_container):
    chunks = await pg_container.services.retriever.retrieve("How long do I have to return an item?")
    assert chunks and chunks[0].source == "knowledge/returns-and-refunds.md"
    assert any(c.lexical_rank for c in chunks) and any(c.dense_rank for c in chunks)


async def test_turn_persists_transcript_and_checkpoint(pg_container):
    import json
    from datetime import datetime

    from harbor.db.repositories import ConversationRepository, OrderRepository

    data = json.loads(Path("seeds/orders.json").read_text())
    orders = []
    for o in data["orders"]:
        row = dict(o)
        for k in ("placed_at", "shipped_at", "delivered_at"):
            if row.get(k):
                row[k] = datetime.fromisoformat(row[k])
        orders.append(row)
    async with pg_container.sessions() as s:
        await OrderRepository(s).seed(data["customers"], orders)
        await s.commit()

    r1 = await pg_container.runtime.run_turn(message="Is shipping free in the Netherlands?")
    r2 = await pg_container.runtime.run_turn(
        message="Where is my order HO-100231?",
        conversation_id=r1.conversation_id,
        customer_email="maya.lindqvist@example.com",
    )
    assert r1.citations and r2.intent == "order" and "DHL" in r2.answer
    async with pg_container.sessions() as s:
        rows = await ConversationRepository(s).transcript(uuid.UUID(r1.conversation_id))
    assert [m.role for m in rows] == ["user", "assistant", "user", "assistant"]
    snapshot = await pg_container.runtime._graph.aget_state(
        {"configurable": {"thread_id": r1.conversation_id}}
    )
    assert len(snapshot.values["messages"]) == 4
