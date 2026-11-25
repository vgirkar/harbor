"""Shared retrieval data types."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    source: str
    title: str
    content: str
    score: float  # final relevance after rerank (or fused score when no reranker)
    dense_rank: int | None = None
    lexical_rank: int | None = None
    fused_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": str(self.chunk_id),
            "document_id": str(self.document_id),
            "source": self.source,
            "title": self.title,
            "content": self.content,
            "score": round(self.score, 4),
            "dense_rank": self.dense_rank,
            "lexical_rank": self.lexical_rank,
            "fused_score": round(self.fused_score, 5),
            "metadata": self.metadata,
        }
