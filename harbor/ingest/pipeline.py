"""Idempotent ingestion: load -> hash check -> chunk -> injection scan -> embed -> store -> index."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harbor.db.models import Chunk
from harbor.db.repositories import DocumentRepository
from harbor.guard.injection import scan_text
from harbor.ingest.chunker import chunk_markdown
from harbor.ingest.loader import LoadedDocument, load_path
from harbor.logging import get_logger
from harbor.retrieval.embeddings import EmbeddingsProvider
from harbor.retrieval.vector_index import VectorIndex

log = get_logger(__name__)
QUARANTINE_THRESHOLD = 0.5


@dataclass(slots=True)
class IngestReport:
    documents_seen: int = 0
    documents_updated: int = 0
    documents_unchanged: int = 0
    chunks_written: int = 0
    chunks_quarantined: int = 0
    quarantined_sources: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "documents_seen": self.documents_seen,
            "documents_updated": self.documents_updated,
            "documents_unchanged": self.documents_unchanged,
            "chunks_written": self.chunks_written,
            "chunks_quarantined": self.chunks_quarantined,
            "quarantined_sources": self.quarantined_sources,
        }


class IngestionPipeline:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        embeddings: EmbeddingsProvider,
        index: VectorIndex,
        batch_size: int = 32,
    ):
        self._sessions = session_factory
        self._embeddings = embeddings
        self._index = index
        self._batch = batch_size

    async def ingest_path(self, path: Path, *, force: bool = False) -> IngestReport:
        return await self.ingest_documents(load_path(path), force=force)

    async def ingest_documents(self, docs: list[LoadedDocument], *, force: bool = False) -> IngestReport:
        report = IngestReport()
        for doc in docs:
            report.documents_seen += 1
            async with self._sessions() as session:
                repo = DocumentRepository(session)
                existing = await repo.get_by_source(doc.source)
                if existing is not None and existing.content_hash == doc.content_hash and not force:
                    report.documents_unchanged += 1
                    continue

                record = await repo.upsert_document(
                    source=doc.source, title=doc.title, content_hash=doc.content_hash, metadata={}
                )
                old_ids = await repo.delete_chunks(record.id)
                await self._index.delete(old_ids)

                drafts = chunk_markdown(doc.text, title=doc.title)
                rows: list[Chunk] = []
                for d in drafts:
                    verdict = scan_text(d.content)
                    quarantined = verdict.score >= QUARANTINE_THRESHOLD
                    if quarantined:
                        report.chunks_quarantined += 1
                        report.quarantined_sources.append(f"{doc.source}#{d.ordinal}")
                        log.warning(
                            "ingest.quarantined",
                            source=doc.source,
                            ordinal=d.ordinal,
                            patterns=verdict.matched,
                        )
                    rows.append(
                        Chunk(
                            document_id=record.id,
                            ordinal=d.ordinal,
                            content=d.content,
                            embed_text=d.embed_text,
                            token_estimate=d.token_estimate,
                            metadata_={**d.metadata, "injection_score": verdict.score},
                            quarantined=quarantined,
                        )
                    )

                live = [r for r in rows if not r.quarantined]
                for start in range(0, len(live), self._batch):
                    batch = live[start : start + self._batch]
                    vectors = await self._embeddings.embed_documents([r.embed_text for r in batch])
                    for row, vec in zip(batch, vectors, strict=True):
                        row.embedding = vec
                await repo.add_chunks(rows)
                await self._index.upsert(
                    [
                        (r.id, r.embedding, {"source": doc.source, "title": doc.title})
                        for r in live
                        if r.embedding
                    ]
                )
                await session.commit()
                report.documents_updated += 1
                report.chunks_written += len(rows)
                log.info(
                    "ingest.document", source=doc.source, chunks=len(rows), quarantined=len(rows) - len(live)
                )
        return report

    async def reindex(self) -> int:
        """Push every live chunk vector to the external index again (after switching to Pinecone)."""
        async with self._sessions() as session:
            repo = DocumentRepository(session)
            chunks = await repo.all_chunks_for_reindex()
            await self._index.upsert(
                [(c.id, c.embedding, {"document_id": str(c.document_id)}) for c in chunks if c.embedding]
            )
            return len(chunks)
