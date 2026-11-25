import uuid

from harbor.db.repositories import ScoredId
from harbor.retrieval.fusion import reciprocal_rank_fusion

A, B, C, D = (uuid.uuid4() for _ in range(4))


def test_rrf_prefers_items_present_in_both_lists():
    dense = [ScoredId(A, 0.9), ScoredId(B, 0.8), ScoredId(C, 0.7)]
    lexical = [ScoredId(C, 5.0), ScoredId(D, 4.0), ScoredId(A, 1.0)]
    fused = reciprocal_rank_fusion(dense, lexical, k=60)
    order = [h.chunk_id for h in fused]
    assert order[0] in (A, C)
    assert set(order[:2]) == {A, C}
    assert order[-1] in (B, D)
    a = next(h for h in fused if h.chunk_id == A)
    assert a.dense_rank == 1 and a.lexical_rank == 3
    assert abs(a.fused_score - (1 / 61 + 1 / 63)) < 1e-9


def test_rrf_is_deterministic_on_ties():
    dense = [ScoredId(A, 1.0)]
    lexical = [ScoredId(B, 1.0)]
    first = [h.chunk_id for h in reciprocal_rank_fusion(dense, lexical)]
    second = [h.chunk_id for h in reciprocal_rank_fusion(dense, lexical)]
    assert first == second
