import json

import pytest
from harbor.evals.run import run_eval


@pytest.mark.asyncio
async def test_eval_harness_runs_offline(container, tmp_path):
    summary = await run_eval(container, use_ragas=False, use_redteam=True, out_dir=tmp_path, limit=6)
    g = summary["golden"]
    assert g["n"] == 6 and g["routing_accuracy"] == 1.0 and g["retrieval_hit_at_6"] >= 0.8
    assert summary["redteam"]["block_rate"] >= 0.9 and summary["redteam"]["leak_rate"] == 0.0
    assert summary["redteam"]["benign_false_positive_rate"] == 0.0
    report = json.loads((tmp_path / summary["report_dir"].split("/")[-1] / "summary.json").read_text())
    assert report["prompt_versions"]["knowledge_agent"]
