"""Deterministic metrics: routing accuracy, retrieval hit rate and MRR, red-team block rate."""

from __future__ import annotations

from statistics import mean
from typing import Any


def hit_at_k(retrieved_sources: list[str], expected: str | None, k: int) -> float | None:
    if not expected:
        return None
    return 1.0 if expected in retrieved_sources[:k] else 0.0


def reciprocal_rank(retrieved_sources: list[str], expected: str | None) -> float | None:
    if not expected:
        return None
    for i, src in enumerate(retrieved_sources, start=1):
        if src == expected:
            return 1.0 / i
    return 0.0


def safe_mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(mean(vals), 4) if vals else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    judged = [r for r in rows if r.get("judge")]
    return {
        "n": len(rows),
        "errors": sum(1 for r in rows if r.get("error") or r.get("judge_error")),
        "routing_accuracy": safe_mean([1.0 if r["intent"] == r["expected_intent"] else 0.0 for r in rows]),
        "retrieval_hit_at_3": safe_mean([r.get("hit_at_3") for r in rows]),
        "retrieval_hit_at_6": safe_mean([r.get("hit_at_6") for r in rows]),
        "retrieval_mrr": safe_mean([r.get("mrr") for r in rows]),
        "judge_pass_rate": safe_mean([1.0 if r["judge"]["pass"] else 0.0 for r in judged])
        if judged
        else None,
        "judge_correctness": safe_mean([r["judge"]["correctness"] for r in judged]) if judged else None,
        "judge_groundedness": safe_mean([r["judge"]["groundedness"] for r in judged]) if judged else None,
        "judge_completeness": safe_mean([r["judge"]["completeness"] for r in judged]) if judged else None,
        "verifier_retries": sum(1 for r in rows if r.get("attempts", 1) > 1),
        "p50_latency_ms": sorted(r["latency_ms"] for r in rows)[len(rows) // 2] if rows else None,
        "mean_latency_ms": int(mean(r["latency_ms"] for r in rows)) if rows else None,
    }
