from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from harbor.api.deps import get_container
from harbor.api.schemas import IngestResponse
from harbor.container import KNOWLEDGE_DIR, Container
from harbor.ingest.loader import LoadedDocument

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


@router.post("/knowledge", response_model=IngestResponse)
async def ingest_bundled(force: bool = False, c: Container = Depends(get_container)) -> IngestResponse:
    """(Re)ingest the bundled knowledge/ folder. Idempotent: unchanged files are skipped."""
    if c.ingestion is None:
        raise HTTPException(409, "ingestion requires Postgres mode")
    report = await c.ingestion.ingest_path(KNOWLEDGE_DIR, force=force)
    return IngestResponse(**report.as_dict())


@router.post("/upload", response_model=IngestResponse)
async def ingest_upload(
    files: list[UploadFile] = File(...), c: Container = Depends(get_container)
) -> IngestResponse:
    if c.ingestion is None:
        raise HTTPException(409, "ingestion requires Postgres mode")
    docs: list[LoadedDocument] = []
    import hashlib

    for f in files:
        if not (f.filename or "").lower().endswith((".md", ".txt", ".markdown")):
            raise HTTPException(415, f"unsupported file type: {f.filename}")
        text = (await f.read()).decode("utf-8", errors="replace")
        title = next(
            (line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")),
            Path(f.filename).stem,
        )
        docs.append(
            LoadedDocument(
                source=f"upload/{f.filename}",
                title=title,
                text=text,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
            )
        )
    report = await c.ingestion.ingest_documents(docs)
    return IngestResponse(**report.as_dict())


@router.post("/reindex")
async def reindex(c: Container = Depends(get_container)) -> dict:
    if c.ingestion is None:
        raise HTTPException(409, "ingestion requires Postgres mode")
    return {"reindexed_chunks": await c.ingestion.reindex()}


_ = tempfile  # keep import for future temp-file based uploads
