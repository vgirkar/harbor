"""Reciprocal Rank Fusion (Cormack et al. 2009).

score(d) = sum over result lists of 1 / (k + rank(d)). Rank based, so dense cosine scores and ts_rank values
never need to be put on the same scale. k=60 is the value from the paper and works well in practice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from harbor.db.repositories import ScoredId


@dataclass(slots=True)
class FusedHit:
    chunk_id: uuid.UUID
    fused_score: float
    dense_rank: int | None
    lexical_rank: int | None


def reciprocal_rank_fusion(dense: list[ScoredId], lexical: list[ScoredId], k: int = 60) -> list[FusedHit]:
    scores: dict[uuid.UUID, float] = {}
    dense_rank: dict[uuid.UUID, int] = {}
    lexical_rank: dict[uuid.UUID, int] = {}

    for rank, hit in enumerate(dense, start=1):
        dense_rank[hit.chunk_id] = rank
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
    for rank, hit in enumerate(lexical, start=1):
        lexical_rank[hit.chunk_id] = rank
        scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)

    fused = [
        FusedHit(
            chunk_id=cid,
            fused_score=score,
            dense_rank=dense_rank.get(cid),
            lexical_rank=lexical_rank.get(cid),
        )
        for cid, score in scores.items()
    ]
    fused.sort(key=lambda h: (-h.fused_score, str(h.chunk_id)))
    return fused
