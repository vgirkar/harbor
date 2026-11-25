import json
from pathlib import Path

import pytest
from harbor.container import InMemoryRetriever

GOLDEN = Path("harbor/evals/data/golden.jsonl")


@pytest.mark.asyncio
async def test_in_memory_hybrid_retrieval_hits_expected_documents(settings):
    r = InMemoryRetriever(Path("knowledge"), settings)
    assert len(r.chunks) > 20
    items = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    hits = 0
    knowledge = [i for i in items if i["expected_source"]]
    for item in knowledge:
        got = await r.retrieve(item["question"])
        sources = [c.source for c in got[:3]]
        hits += item["expected_source"] in sources
    assert hits / len(knowledge) >= 0.8, f"hit@3 too low: {hits}/{len(knowledge)}"


@pytest.mark.asyncio
async def test_quarantine_excludes_poisoned_chunks(settings, tmp_path):
    (tmp_path / "poisoned.md").write_text(Path("tests/fixtures/poisoned.md").read_text())
    r = InMemoryRetriever(tmp_path, settings)
    assert r.quarantined and any("poisoned.md" in q for q in r.quarantined)
    assert all("Ignore all previous" not in c.content for c in r.chunks)
    assert any("heavy items" in c.content for c in r.chunks)
