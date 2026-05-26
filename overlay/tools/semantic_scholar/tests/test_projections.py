"""Unit tests for ``semantic_scholar.projections`` — pure projection functions.

Covers the projection helpers at their tool-scoped home plus the
``build_paper_archive_row`` projection that the workflow handler
consumes when producing a ``paper_archives`` row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from semanticscholar.Paper import Paper

from semantic_scholar.projections import (
    FULLTEXT_BODY_MAX_BYTES,
    brief_id_for,
    build_brief_document,
    build_fulltext_document,
    build_paper_archive_row,
    build_paper_document,
    render_brief,
)
from semantic_scholar.utils import content_hash

_RECENCY_TOLERANCE = timedelta(seconds=30)


def _assert_recent_utc(value: Any) -> None:
    assert isinstance(value, datetime) and value.utcoffset() == timedelta(0)
    now = datetime.now(UTC)
    assert now - _RECENCY_TOLERANCE <= value <= now + _RECENCY_TOLERANCE


def _sample_paper_dict() -> dict[str, Any]:
    return {
        "paperId": "abc123",
        "title": "Attention Is All You Need",
        "authors": [
            {"authorId": "1", "name": "Ashish Vaswani"},
            {"authorId": "2", "name": "Noam Shazeer"},
        ],
        "year": 2017,
        "abstract": "We propose a new simple network architecture, the Transformer.",
        "citationCount": 75000,
        "url": "https://www.semanticscholar.org/paper/abc123",
        "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762.pdf", "status": "GREEN"},
        "venue": "NeurIPS",
        "externalIds": {"DOI": "10.5555/3295222.3295349", "ArXiv": "1706.03762"},
    }


def _sample_paper() -> Paper:
    return Paper(_sample_paper_dict())


# ---------------------------------------------------------------------------
# build_paper_document
# ---------------------------------------------------------------------------


def test_build_paper_document_golden_shape() -> None:
    doc = build_paper_document(_sample_paper(), query="transformers")

    assert doc["document_id"] == "semantic_scholar:paper:abc123"
    assert doc["source"] == "semantic_scholar"
    assert doc["source_type"] == "paper"
    assert doc["source_document_id"] == "abc123"
    assert doc["source_chunk_id"] == ""
    assert doc["parent_document_id"] is None
    assert doc["title"] == "Attention Is All You Need"
    assert doc["author_id"] == "1"
    assert doc["author_name"] == "Ashish Vaswani"
    assert doc["access_scope"] == "company"
    assert doc["occurred_at"] == datetime(2017, 1, 1, tzinfo=UTC)
    _assert_recent_utc(doc["source_updated_at"])
    body = doc["body"]
    assert "# Attention Is All You Need" in body
    assert "- Authors: Ashish Vaswani, Noam Shazeer" in body
    assert "- DOI: 10.5555/3295222.3295349" in body
    assert "## Abstract" in body
    assert doc["metadata"]["query"] == "transformers"
    assert doc["metadata"]["arxivId"] == "1706.03762"
    assert doc["metadata"]["openAccessPdf"] == "https://arxiv.org/pdf/1706.03762.pdf"


def test_build_paper_document_without_query_persists_none() -> None:
    doc = build_paper_document(_sample_paper())

    assert doc["parent_document_id"] is None
    assert doc["metadata"]["query"] is None


def test_build_paper_document_with_parent_document_id() -> None:
    """``parent_document_id`` flows straight into the projected row."""
    doc = build_paper_document(_sample_paper(), parent_document_id="brief:foo")

    assert doc["parent_document_id"] == "brief:foo"


def test_build_paper_document_raises_on_missing_paperId() -> None:
    bad = _sample_paper_dict()
    del bad["paperId"]
    with pytest.raises(ValueError, match="paperId"):
        build_paper_document(Paper(bad))


# ---------------------------------------------------------------------------
# build_fulltext_document
# ---------------------------------------------------------------------------


def test_build_fulltext_document_under_cap_not_truncated() -> None:
    text = "# Body\n\nA short parsed body."
    doc = build_fulltext_document(
        _sample_paper(),
        parsed_text=text,
        parser_used="pymupdf4llm",
        truncated=False,
        pdf_sha256="deadbeef",
        source_url="https://arxiv.org/pdf/1706.03762.pdf",
    )

    assert doc["document_id"] == "semantic_scholar:paper_fulltext:abc123"
    assert doc["source_type"] == "paper_fulltext"
    assert doc["parent_document_id"] == "semantic_scholar:paper:abc123"
    assert doc["body"] == text
    assert doc["metadata"]["truncated"] is False
    assert doc["metadata"]["pdfSha256"] == "deadbeef"
    assert doc["metadata"]["sourceUrl"] == "https://arxiv.org/pdf/1706.03762.pdf"
    assert doc["metadata"]["parserUsed"] == "pymupdf4llm"


def test_build_fulltext_document_over_cap_sets_truncated_flag() -> None:
    """Body over the byte cap is truncated; ``truncated`` reflects that."""
    big_text = "a" * (FULLTEXT_BODY_MAX_BYTES + 100)
    doc = build_fulltext_document(
        _sample_paper(),
        parsed_text=big_text,
        parser_used="pymupdf4llm",
        truncated=False,
        pdf_sha256="abc",
    )

    body_bytes = doc["body"].encode("utf-8")
    assert len(body_bytes) <= FULLTEXT_BODY_MAX_BYTES
    assert doc["metadata"]["truncated"] is True


def test_build_fulltext_document_caller_truncated_flag_or_in() -> None:
    """When caller hints truncated, the metadata flag stays True even under cap."""
    doc = build_fulltext_document(
        _sample_paper(),
        parsed_text="short body",
        parser_used="pymupdf",
        truncated=True,
        pdf_sha256="x",
    )

    assert doc["metadata"]["truncated"] is True


def test_build_fulltext_document_raises_on_missing_paperId() -> None:
    bad = _sample_paper_dict()
    del bad["paperId"]
    with pytest.raises(ValueError, match="paperId"):
        build_fulltext_document(
            Paper(bad),
            parsed_text="x",
            parser_used="pymupdf",
            truncated=False,
            pdf_sha256="abc",
        )


# ---------------------------------------------------------------------------
# build_paper_archive_row
# ---------------------------------------------------------------------------


def test_build_paper_archive_row_golden_shape() -> None:
    data = b"%PDF-1.4 fake body"
    row = build_paper_archive_row(
        _sample_paper(),
        data=data,
        mime="application/pdf",
        pdf_sha256="deadbeef",
        parsed_text="# parsed",
        parser_used="pymupdf4llm",
        source_url="https://arxiv.org/pdf/1706.03762.pdf",
    )

    expected_keys = {
        "paper_id",
        "source_url",
        "mime_type",
        "size_bytes",
        "pdf_sha256",
        "pdf_bytes",
        "parsed_text",
        "parser_used",
        "truncated",
        "metadata",
    }
    assert set(row.keys()) == expected_keys
    assert row["paper_id"] == "abc123"
    assert row["source_url"] == "https://arxiv.org/pdf/1706.03762.pdf"
    assert row["mime_type"] == "application/pdf"
    assert row["size_bytes"] == len(data)
    assert row["pdf_sha256"] == "deadbeef"
    assert row["pdf_bytes"] == data
    assert row["parsed_text"] == "# parsed"
    assert row["parser_used"] == "pymupdf4llm"
    assert row["truncated"] is False
    assert row["metadata"] == {
        "paperId": "abc123",
        "url": "https://www.semanticscholar.org/paper/abc123",
    }


def test_build_paper_archive_row_truncated_flag_passthrough() -> None:
    row = build_paper_archive_row(
        _sample_paper(),
        data=b"",
        mime="application/pdf",
        pdf_sha256="x",
        parsed_text="",
        parser_used="pymupdf",
        source_url="https://x/y.pdf",
        truncated=True,
    )

    assert row["truncated"] is True


def test_build_paper_archive_row_raises_on_missing_paperId() -> None:
    bad = _sample_paper_dict()
    del bad["paperId"]
    with pytest.raises(ValueError, match="paperId"):
        build_paper_archive_row(
            Paper(bad),
            data=b"",
            mime="application/pdf",
            pdf_sha256="x",
            parsed_text="",
            parser_used="pymupdf",
            source_url="https://x/y.pdf",
        )


# ---------------------------------------------------------------------------
# brief_id_for
# ---------------------------------------------------------------------------


def test_brief_id_for_is_deterministic() -> None:
    assert brief_id_for("foo", 2020) == brief_id_for("foo", 2020)


def test_brief_id_for_is_case_insensitive() -> None:
    """Stripping + lowercasing makes ``Foo`` and ``  foo `` collide."""
    assert brief_id_for("Foo", None) == brief_id_for("  foo  ", None)


def test_brief_id_for_year_from_differentiates() -> None:
    assert brief_id_for("foo", 2020) != brief_id_for("foo", 2021)
    assert brief_id_for("foo", 2020) != brief_id_for("foo", None)


def test_brief_id_for_is_short_hex_string() -> None:
    suffix = brief_id_for("anything", None)
    assert len(suffix) == 16
    int(suffix, 16)  # all-hex


# ---------------------------------------------------------------------------
# render_brief
# ---------------------------------------------------------------------------


def test_render_brief_with_empty_papers_includes_no_papers_line() -> None:
    out = render_brief("transformers", 2017, [])

    assert "# Research Brief: transformers" in out
    assert "- Results: 0 papers" in out
    assert "No papers found for this query." in out


def test_render_brief_single_paper() -> None:
    out = render_brief("transformers", 2017, [_sample_paper()])

    assert "## Papers" in out
    assert "### 1. Attention Is All You Need" in out
    assert "- Authors: Ashish Vaswani, Noam Shazeer" in out
    assert "- Citations: 75000" in out


def test_render_brief_multiple_papers_numbered() -> None:
    p1 = Paper({**_sample_paper_dict(), "paperId": "p1", "title": "First"})
    p2 = Paper({**_sample_paper_dict(), "paperId": "p2", "title": "Second"})

    out = render_brief("x", None, [p1, p2])

    assert "- Year filter: any" in out
    assert "### 1. First" in out
    assert "### 2. Second" in out


# ---------------------------------------------------------------------------
# build_brief_document
# ---------------------------------------------------------------------------


def test_build_brief_document_shape_and_content_hash() -> None:
    papers = [_sample_paper()]
    markdown = render_brief("transformers", None, papers)

    doc = build_brief_document(
        query="transformers",
        year_from=None,
        limit=5,
        papers=papers,
        markdown=markdown,
    )

    suffix = brief_id_for("transformers", None)
    assert doc["document_id"] == f"semantic_scholar:research_brief:{suffix}"
    assert doc["source"] == "semantic_scholar"
    assert doc["source_type"] == "research_brief"
    assert doc["source_document_id"] == suffix
    assert doc["title"] == "Research Brief: transformers"
    assert doc["body"] == markdown
    assert doc["parent_document_id"] is None
    assert doc["metadata"]["paper_ids"] == ["abc123"]
    assert doc["metadata"]["limit"] == 5
    assert doc["metadata"]["results_count"] == 1
    assert doc["content_hash"] == content_hash(doc["title"], markdown, "", doc["metadata"])


def test_build_brief_document_truncates_long_query_in_title() -> None:
    long_query = "x" * 200
    doc = build_brief_document(
        query=long_query,
        year_from=None,
        limit=0,
        papers=[],
        markdown="",
    )

    assert doc["title"].startswith("Research Brief: ")
    assert len(doc["title"]) <= len("Research Brief: ") + 80
