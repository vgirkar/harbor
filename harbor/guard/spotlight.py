"""Layer 3: spotlighting. Untrusted text (retrieved chunks, tool results) is wrapped in a delimited block the
model is told to treat as data. Delimiters are stripped from the content first so a document cannot close the
block early and smuggle in a fake instruction tier."""

from __future__ import annotations

import re

from harbor.retrieval.types import RetrievedChunk

_TAG = re.compile(r"</?\s*(document|tool_result|system|instructions)\b[^>]*>", re.I)


def sanitize(text: str) -> str:
    return _TAG.sub("", text)


def render_documents(chunks: list[RetrievedChunk]) -> str:
    """Produce the <documents> block with stable short ids (c1, c2, ...) that the model must cite."""
    parts = []
    for i, c in enumerate(chunks, start=1):
        section = c.metadata.get("section") or c.title
        parts.append(
            f'<document id="c{i}" source="{sanitize(c.source)}" title="{sanitize(c.title)}" section="{sanitize(str(section))}">\n'
            f"{sanitize(c.content)}\n</document>"
        )
    return "<documents>\n" + "\n".join(parts) + "\n</documents>"


def render_tool_result(name: str, payload: str) -> str:
    return f'<tool_result name="{sanitize(name)}">\n{sanitize(payload)}\n</tool_result>'
