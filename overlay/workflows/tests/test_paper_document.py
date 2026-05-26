"""Tests for the shared paper-document helpers (pure functions + async upsert)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from centaur_lab.paper_document import _content_hash, build_paper_document, upsert_document
from centaur_lab.paper_models import Paper
from centaur_lab.testing import EXECUTE_ARG_INDEX, MockPool

# Tolerance for source_updated_at = datetime.now(UTC) at projection.
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
    """Typed sibling of ``_sample_paper_dict`` for the post-Pydantic API."""
    return Paper.model_validate(_sample_paper_dict())


def test_build_paper_document_full_happy_shape() -> None:
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
    assert "- Year: 2017" in body
    assert "- Venue: NeurIPS" in body
    assert "- Citations: 75000" in body
    assert "- DOI: 10.5555/3295222.3295349" in body
    assert "## Abstract" in body
    assert doc["metadata"] == {
        "paperId": "abc123",
        "year": 2017,
        "venue": "NeurIPS",
        "citationCount": 75000,
        "authors": _sample_paper_dict()["authors"],
        "doi": "10.5555/3295222.3295349",
        "arxivId": "1706.03762",
        "openAccessPdf": "https://arxiv.org/pdf/1706.03762.pdf",
        "query": "transformers",
    }


def test_build_paper_document_raises_on_missing_paperId() -> None:
    paper_data = _sample_paper_dict()
    del paper_data["paperId"]
    with pytest.raises(ValueError, match="paperId"):
        build_paper_document(Paper.model_validate(paper_data))


def test_build_paper_document_preserves_explicit_nulls_for_missing_optional_fields() -> None:
    """S11: a paper missing every optional field still surfaces each
    metadata key with explicit ``None`` (so ``metadata ? 'doi'`` checks
    behave like upstream Slack rows), reports ``occurred_at=None`` for an
    unknown year, and yields empty author fields when ``authors=[]``.
    """
    paper_data = _sample_paper_dict() | {
        "externalIds": {},
        "openAccessPdf": None,
        "venue": None,
        "year": None,
        "authors": [],
    }
    doc = build_paper_document(Paper.model_validate(paper_data))

    assert doc["occurred_at"] is None
    assert doc["author_id"] == "" and doc["author_name"] == ""
    meta = doc["metadata"]
    for key in ("doi", "arxivId", "openAccessPdf", "venue", "year", "query"):
        assert key in meta and meta[key] is None, f"{key!r} should be explicit None"


def test_content_hash_non_ascii_byte_form_matches_upstream() -> None:
    """Strongest hash test: independently recompute SHA256 over the exact
    upstream ``json.dumps`` byte form and assert equality. Implicitly
    proves determinism, non-ASCII literal-byte serialization, and exact
    cross-system agreement with ``api.runtime_control.canonical_json``.
    """
    paper_data = _sample_paper_dict() | {
        "title": "深層学習: 変換器の基礎",
        "authors": [{"authorId": "1", "name": "山田太郎"}],
    }
    doc = build_paper_document(Paper.model_validate(paper_data), query="変換器")

    parts = (doc["title"], doc["body"], doc["url"], doc["metadata"])
    upstream = json.dumps(parts, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    expected_hash = hashlib.sha256(upstream.encode("utf-8")).hexdigest()

    assert doc["content_hash"] == expected_hash
    assert "深層学習" in upstream and "山田太郎" in upstream


@pytest.mark.asyncio
async def test_upsert_document_branches_across_insert_update_noop() -> None:
    """The three-way decision tree: no existing row → ``inserted``;
    existing-but-different hash → ``updated``; existing hash matches the
    persisted (intrinsic + parent) compound → ``noop`` and short-circuits
    before issuing the UPSERT.
    """
    doc = build_paper_document(_sample_paper())
    insert_pool = MockPool(existing_hash=None)
    assert await upsert_document(insert_pool, doc) == "inserted"
    assert len(insert_pool.execute_calls) == 1
    update_pool = MockPool(existing_hash="old_hash")
    assert await upsert_document(update_pool, doc) == "updated"
    assert len(update_pool.execute_calls) == 1
    noop_pool = MockPool(existing_hash=_content_hash(doc["content_hash"], None))
    assert await upsert_document(noop_pool, doc) == "noop"
    assert len(noop_pool.fetchval_calls) == 1
    assert noop_pool.execute_calls == []


@pytest.mark.asyncio
async def test_upsert_document_uses_document_parent() -> None:
    """Locks the contract that ``upsert_document`` reads parent linkage
    straight out of ``document["parent_document_id"]``.

    Callers like ``build_fulltext_document`` and the research-brief paper
    loop set this field at build time; ``upsert_document`` has no kwarg
    override. Without this contract the persisted ``parent_document_id``
    and compound ``content_hash`` would diverge from what was first
    written and re-parenting would silently NULL the link.
    """
    doc = build_paper_document(_sample_paper())
    doc["parent_document_id"] = "semantic_scholar:paper:parent"
    pool = MockPool(existing_hash=None)

    result = await upsert_document(pool, doc)

    assert result == "inserted"
    assert len(pool.execute_calls) == 1
    _query, args = pool.execute_calls[0]
    assert args[EXECUTE_ARG_INDEX["parent_document_id"]] == "semantic_scholar:paper:parent"
    expected_hash = _content_hash(doc["content_hash"], "semantic_scholar:paper:parent")
    assert args[EXECUTE_ARG_INDEX["content_hash"]] == expected_hash


@pytest.mark.asyncio
async def test_upsert_document_relinks_parent_when_content_unchanged() -> None:
    """A paper saved with no parent should be re-parented when later
    encountered as part of a research brief — the intrinsic content didn't
    change, but the parent did. Earlier upsert tests only seeded
    existing_hash=None (fresh-insert path) so this regression was invisible.
    """
    paper = _sample_paper()
    no_parent_doc = build_paper_document(paper)
    intrinsic_hash = no_parent_doc["content_hash"]
    no_parent_persisted_hash = _content_hash(intrinsic_hash, None)
    pool = MockPool(existing_hash=no_parent_persisted_hash, execute_status="INSERT 0 1")

    doc = build_paper_document(paper, parent_document_id="brief:Q")
    result = await upsert_document(pool, doc)

    assert result == "updated"
    assert len(pool.execute_calls) == 1
    _query, args = pool.execute_calls[0]
    assert args[EXECUTE_ARG_INDEX["parent_document_id"]] == "brief:Q"
    new_persisted_hash = _content_hash(intrinsic_hash, "brief:Q")
    assert args[EXECUTE_ARG_INDEX["content_hash"]] == new_persisted_hash
