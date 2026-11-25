"""Heading aware chunking with contextual breadcrumbs.

1. Split on markdown headings (H1-H3) so a chunk never straddles two policies.
2. Split long sections with a recursive character splitter (target ~450 tokens, 60 token overlap).
3. Prefix every chunk with "Title > Section" for embedding and indexing (the breadcrumb is what makes a
   sentence like "within 30 days" retrievable for "return window"), while the raw content is what the model
   sees inside the <document> block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

CHARS_PER_TOKEN = 4  # rough English average, good enough for sizing


@dataclass(slots=True)
class ChunkDraft:
    ordinal: int
    content: str
    embed_text: str
    token_estimate: int
    metadata: dict[str, Any] = field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def chunk_markdown(
    text: str, *, title: str, target_tokens: int = 450, overlap_tokens: int = 60
) -> list[ChunkDraft]:
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")], strip_headers=True
    )
    sections = header_splitter.split_text(text)
    body_splitter = RecursiveCharacterTextSplitter(
        chunk_size=target_tokens * CHARS_PER_TOKEN,
        chunk_overlap=overlap_tokens * CHARS_PER_TOKEN,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    drafts: list[ChunkDraft] = []
    ordinal = 0
    for section in sections:
        crumbs = [title] + [section.metadata[k] for k in ("h1", "h2", "h3") if k in section.metadata]
        crumbs = [c for i, c in enumerate(crumbs) if i == 0 or c != crumbs[i - 1]]
        breadcrumb = " > ".join(crumbs)
        for piece in body_splitter.split_text(section.page_content):
            piece = piece.strip()
            if not piece:
                continue
            drafts.append(
                ChunkDraft(
                    ordinal=ordinal,
                    content=piece,
                    embed_text=f"{breadcrumb}\n\n{piece}",
                    token_estimate=estimate_tokens(piece),
                    metadata={"breadcrumb": breadcrumb, "section": crumbs[-1]},
                )
            )
            ordinal += 1
    return drafts
