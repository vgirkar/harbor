"""Load knowledge files (.md, .txt) into LoadedDocument objects."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

SUPPORTED = {".md", ".markdown", ".txt"}


@dataclass(slots=True)
class LoadedDocument:
    source: str  # stable id, e.g. "knowledge/returns-and-refunds.md"
    title: str
    text: str
    content_hash: str


def _title_from(text: str, path: Path) -> str:
    m = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    return m.group(1).strip() if m else path.stem.replace("-", " ").replace("_", " ").title()


def load_path(path: Path, *, root: Path | None = None) -> list[LoadedDocument]:
    files = sorted(
        p for p in ([path] if path.is_file() else path.rglob("*")) if p.suffix.lower() in SUPPORTED
    )
    root = root or (path if path.is_dir() else path.parent)
    docs: list[LoadedDocument] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        rel = f.relative_to(root.parent) if root.parent in f.parents else f.name
        docs.append(
            LoadedDocument(
                source=str(rel).replace("\\", "/"),
                title=_title_from(text, f),
                text=text,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        )
    return docs
