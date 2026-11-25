from harbor.ingest.chunker import chunk_markdown

DOC = (
    """# Returns

## Window

You can return within 30 days. """
    + ("More detail. " * 300)
    + """

## Refunds

Refunds take three working days.
"""
)


def test_chunks_follow_headings_and_carry_breadcrumbs():
    chunks = chunk_markdown(DOC, title="Returns and refunds")
    assert len(chunks) >= 3
    assert chunks[0].metadata["breadcrumb"].startswith("Returns and refunds > Returns > Window")
    assert chunks[0].embed_text.startswith("Returns and refunds > Returns > Window")
    assert "30 days" in chunks[0].content
    assert chunks[-1].metadata["section"] == "Refunds"
    assert all(c.token_estimate <= 520 for c in chunks)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_long_sections_split_with_overlap():
    chunks = chunk_markdown(DOC, title="T")
    window = [c for c in chunks if c.metadata["section"] == "Window"]
    assert len(window) >= 2
    assert window[0].content[-40:] in window[1].content or window[1].content[:40] in window[0].content
