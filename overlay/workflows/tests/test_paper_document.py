"""Tests for the shared paper-document helpers (pure functions + async upsert)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

# The workflows directory isn't a package (no __init__.py — it's a runtime
# WORKFLOW_DIRS drop folder, not an importable Python package). Tests run
# under it, so we stitch the parent on the path to import the module by file
# name without changing the production layout.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.paper_document import _content_hash, build_paper_document, upsert_document

from ._mocks import EXECUTE_ARG_INDEX, MockPool


def _sample_paper() -> dict[str, Any]:
    """A representative S2 Graph API response for the happy-path tests."""
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


def test_basic_paper_builds_full_document() -> None:
    paper = _sample_paper()
    doc = build_paper_document(paper, query="transformers")

    assert doc["document_id"] == "semantic_scholar:paper:abc123"
    assert doc["source"] == "semantic_scholar"
    assert doc["source_type"] == "paper"
    assert doc["source_document_id"] == "abc123"
    assert doc["source_chunk_id"] == ""
    assert doc["parent_document_id"] is None
    assert doc["title"] == "Attention Is All You Need"
    assert doc["url"] == "https://www.semanticscholar.org/paper/abc123"
    assert doc["author_id"] == "1"
    assert doc["author_name"] == "Ashish Vaswani"
    assert doc["access_scope"] == "company"
    assert doc["occurred_at"] == datetime(2017, 1, 1, tzinfo=UTC)
    assert doc["source_updated_at"] == datetime(2017, 1, 1, tzinfo=UTC)

    body = doc["body"]
    assert "# Attention Is All You Need" in body
    assert "- Authors: Ashish Vaswani, Noam Shazeer" in body
    assert "- Year: 2017" in body
    assert "- Venue: NeurIPS" in body
    assert "- Citations: 75000" in body
    assert "- DOI: 10.5555/3295222.3295349" in body
    assert "## Abstract" in body
    assert "We propose a new simple network architecture" in body

    meta = doc["metadata"]
    assert meta["paperId"] == "abc123"
    assert meta["year"] == 2017
    assert meta["venue"] == "NeurIPS"
    assert meta["citationCount"] == 75000
    assert meta["authors"] == [
        {"authorId": "1", "name": "Ashish Vaswani"},
        {"authorId": "2", "name": "Noam Shazeer"},
    ]
    assert meta["doi"] == "10.5555/3295222.3295349"
    assert meta["arxivId"] == "1706.03762"
    assert meta["openAccessPdf"] == "https://arxiv.org/pdf/1706.03762.pdf"
    assert meta["query"] == "transformers"


def test_missing_paperId_raises_ValueError() -> None:
    paper = _sample_paper()
    del paper["paperId"]
    with pytest.raises(ValueError, match="paperId"):
        build_paper_document(paper)


def test_missing_title_falls_back_to_untitled() -> None:
    paper = _sample_paper()
    paper["title"] = None
    doc = build_paper_document(paper)
    assert doc["title"] == "Untitled"
    assert doc["body"].startswith("# Untitled")


def test_missing_url_falls_back_to_s2_canonical() -> None:
    paper = _sample_paper()
    paper["url"] = None
    doc = build_paper_document(paper)
    assert doc["url"] == "https://www.semanticscholar.org/paper/abc123"
    assert "- URL: https://www.semanticscholar.org/paper/abc123" in doc["body"]


def test_missing_year_yields_null_occurred_at() -> None:
    paper = _sample_paper()
    paper["year"] = None
    doc = build_paper_document(paper)
    assert doc["occurred_at"] is None
    assert doc["source_updated_at"] is None
    assert "- Year: Unknown" in doc["body"]
    # year=None must be dropped from metadata per the "drop None values" rule.
    assert "year" not in doc["metadata"]


def test_no_authors_yields_empty_author_fields() -> None:
    paper = _sample_paper()
    paper["authors"] = []
    doc = build_paper_document(paper)
    assert doc["author_id"] == ""
    assert doc["author_name"] == ""
    assert "- Authors: Unknown" in doc["body"]
    assert doc["metadata"]["authors"] == []


def test_metadata_includes_query_only_when_provided() -> None:
    paper = _sample_paper()
    doc_without = build_paper_document(paper)
    assert "query" not in doc_without["metadata"]

    doc_with = build_paper_document(paper, query="diffusion models")
    assert doc_with["metadata"]["query"] == "diffusion models"


def test_content_hash_stable_across_calls_with_same_input() -> None:
    paper = _sample_paper()
    first = build_paper_document(paper, query="transformers")
    second = build_paper_document(paper, query="transformers")
    assert first["content_hash"] == second["content_hash"]
    # _content_hash itself must also be deterministic for the same inputs.
    assert _content_hash("a", "b", {"k": 1}) == _content_hash("a", "b", {"k": 1})


def test_content_hash_changes_when_title_changes() -> None:
    paper = _sample_paper()
    baseline = build_paper_document(paper)
    paper["title"] = "Attention Is All You Need v2"
    mutated = build_paper_document(paper)
    assert baseline["content_hash"] != mutated["content_hash"]


@pytest.mark.asyncio
async def test_upsert_document_returns_noop_when_hash_matches() -> None:
    doc = build_paper_document(_sample_paper())
    # The persisted hash combines the intrinsic content_hash with the
    # effective parent (None here) so reparenting forces an update; see the
    # relink test below.
    persisted_hash = _content_hash(doc["content_hash"], None)
    pool = MockPool(existing_hash=persisted_hash)

    result = await upsert_document(pool, doc)

    assert result == "noop"
    assert len(pool.fetchval_calls) == 1
    assert pool.execute_calls == []


@pytest.mark.asyncio
async def test_upsert_document_returns_inserted_when_no_existing_row() -> None:
    doc = build_paper_document(_sample_paper())
    pool = MockPool(existing_hash=None, execute_status="INSERT 0 1")

    result = await upsert_document(pool, doc)

    assert result == "inserted"
    assert len(pool.execute_calls) == 1


@pytest.mark.asyncio
async def test_upsert_document_returns_updated_when_hash_differs() -> None:
    doc = build_paper_document(_sample_paper())
    pool = MockPool(existing_hash="old_hash", execute_status="INSERT 0 1")

    result = await upsert_document(pool, doc)

    assert result == "updated"
    assert len(pool.execute_calls) == 1


@pytest.mark.asyncio
async def test_upsert_document_returns_noop_when_execute_status_zero() -> None:
    doc = build_paper_document(_sample_paper())
    # Defensive: even with a hash mismatch, the SQL's
    # `WHERE content_hash IS DISTINCT FROM EXCLUDED.content_hash` clause can
    # report "INSERT 0 0" — treat that as a no-op.
    pool = MockPool(existing_hash="old_hash", execute_status="INSERT 0 0")

    result = await upsert_document(pool, doc)

    assert result == "noop"


@pytest.mark.asyncio
async def test_upsert_document_parent_kwarg_overrides_document_field() -> None:
    doc = build_paper_document(_sample_paper())
    doc["parent_document_id"] = "doc:from-document"
    pool = MockPool(existing_hash=None, execute_status="INSERT 0 1")

    result = await upsert_document(pool, doc, parent_document_id="doc:from-kwarg")

    assert result == "inserted"
    _query, execute_args = pool.execute_calls[0]
    assert execute_args[EXECUTE_ARG_INDEX["parent_document_id"]] == "doc:from-kwarg"


@pytest.mark.asyncio
async def test_upsert_document_uses_document_parent_when_kwarg_omitted() -> None:
    doc = build_paper_document(_sample_paper())
    doc["parent_document_id"] = "doc:from-document"
    pool = MockPool(existing_hash=None, execute_status="INSERT 0 1")

    result = await upsert_document(pool, doc)

    assert result == "inserted"
    _query, execute_args = pool.execute_calls[0]
    assert execute_args[EXECUTE_ARG_INDEX["parent_document_id"]] == "doc:from-document"


@pytest.mark.asyncio
async def test_upsert_document_relinks_parent_when_content_unchanged() -> None:
    """A paper saved with no parent should be re-parented when later
    encountered as part of a research brief — the intrinsic content didn't
    change, but the parent did. Earlier upsert tests only seeded
    existing_hash=None (fresh-insert path) so this regression was invisible.
    """
    doc = build_paper_document(_sample_paper())
    intrinsic_hash = doc["content_hash"]
    no_parent_persisted_hash = _content_hash(intrinsic_hash, None)
    pool = MockPool(existing_hash=no_parent_persisted_hash, execute_status="INSERT 0 1")

    result = await upsert_document(pool, doc, parent_document_id="brief:Q")

    assert result == "updated"
    assert len(pool.execute_calls) == 1
    _query, args = pool.execute_calls[0]
    assert args[EXECUTE_ARG_INDEX["parent_document_id"]] == "brief:Q"
    new_persisted_hash = _content_hash(intrinsic_hash, "brief:Q")
    assert args[EXECUTE_ARG_INDEX["content_hash"]] == new_persisted_hash
