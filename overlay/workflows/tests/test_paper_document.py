"""Tests for the shared paper-document helpers (pure functions + async upsert)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from centaur_lab.paper_document import (
    _canonical_json,
    _content_hash,
    build_paper_document,
    upsert_document,
)
from centaur_lab.testing import EXECUTE_ARG_INDEX, MockPool

# ``build_paper_document`` stamps ``source_updated_at`` with
# ``datetime.now(UTC)`` at projection time. Tests treat that as
# "recent UTC datetime" rather than pinning it to a literal value; the
# fudge factor below absorbs CI clock skew without letting genuinely
# stale projections slip through.
_RECENCY_TOLERANCE = timedelta(seconds=30)


def _assert_recent_utc(value: Any) -> None:
    """Assert ``value`` is a UTC datetime taken within the last few seconds."""
    assert isinstance(value, datetime)
    assert value.tzinfo is not None
    assert value.utcoffset() == timedelta(0)
    now = datetime.now(UTC)
    assert now - _RECENCY_TOLERANCE <= value <= now + _RECENCY_TOLERANCE


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
    # ``source_updated_at`` tracks sync time (datetime.now(UTC) at
    # projection), not publication year — see S10 in docs/review.md.
    _assert_recent_utc(doc["source_updated_at"])

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
    # ``source_updated_at`` is sync time, not publication time, so it
    # stays populated even when the paper has no known year.
    _assert_recent_utc(doc["source_updated_at"])
    assert "- Year: Unknown" in doc["body"]
    # ``year`` is preserved as an explicit ``None`` in metadata (S11)
    # so JSONB key-presence checks behave the same way for papers
    # missing optional fields as they do for Slack rows upstream.
    assert "year" in doc["metadata"]
    assert doc["metadata"]["year"] is None


def test_no_authors_yields_empty_author_fields() -> None:
    paper = _sample_paper()
    paper["authors"] = []
    doc = build_paper_document(paper)
    assert doc["author_id"] == ""
    assert doc["author_name"] == ""
    assert "- Authors: Unknown" in doc["body"]
    assert doc["metadata"]["authors"] == []


def test_metadata_includes_query_with_explicit_null_when_absent() -> None:
    paper = _sample_paper()
    doc_without = build_paper_document(paper)
    # S11: metadata keys are present with explicit nulls rather than
    # being dropped, so downstream ``metadata ? 'query'`` checks see
    # the key on every row regardless of whether a query was passed.
    assert "query" in doc_without["metadata"]
    assert doc_without["metadata"]["query"] is None

    doc_with = build_paper_document(paper, query="diffusion models")
    assert doc_with["metadata"]["query"] == "diffusion models"


def test_metadata_preserves_explicit_nulls_for_missing_optional_fields() -> None:
    """S11: a paper missing every optional metadata field still surfaces
    each key with an explicit ``None`` value so JSONB key-presence checks
    (e.g. ``metadata ? 'doi'``) match the behaviour of upstream Slack
    rows that always list every key.
    """
    paper = _sample_paper()
    paper["externalIds"] = {}  # no DOI, no ArXiv
    paper["openAccessPdf"] = None
    paper["venue"] = None
    paper["year"] = None

    doc = build_paper_document(paper)

    meta = doc["metadata"]
    for key in ("doi", "arxivId", "openAccessPdf", "venue", "year", "query"):
        assert key in meta, f"{key!r} should be present even when value is None"
        assert meta[key] is None, f"{key!r} should be explicit None, got {meta[key]!r}"


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


def test_canonical_json_preserves_non_ascii_literally() -> None:
    """Non-ASCII content must be serialized as literal Unicode bytes, not
    \\uXXXX escapes, so content_hash bytes match what upstream
    ``api.runtime_control.canonical_json`` would compute for the same input.
    """
    title_jp = "深層学習"
    title_de = "Übergang"

    assert _canonical_json(title_jp) == f'"{title_jp}"'
    assert _canonical_json(title_de) == f'"{title_de}"'
    assert "\\u" not in _canonical_json({"title": title_jp, "name": title_de})


def test_content_hash_for_non_ascii_paper_matches_upstream_byte_form() -> None:
    """Strongest form: the hash must equal what we'd get if we re-canonicalized
    the same parts with upstream's exact ``json.dumps`` arguments.
    """
    paper = _sample_paper()
    paper["title"] = "深層学習: 変換器の基礎"
    paper["authors"] = [{"authorId": "1", "name": "山田太郎"}]
    doc = build_paper_document(paper, query="変換器")

    parts = (doc["title"], doc["body"], doc["url"], doc["metadata"])
    upstream_canonical = json.dumps(
        parts, separators=(",", ":"), sort_keys=True, ensure_ascii=False
    )
    expected_hash = hashlib.sha256(upstream_canonical.encode("utf-8")).hexdigest()

    assert doc["content_hash"] == expected_hash
    assert "深層学習" in upstream_canonical
    assert "山田太郎" in upstream_canonical


def test_canonical_json_raises_typeerror_on_non_serializable_value() -> None:
    """Dropping ``default=str`` means real serialization bugs surface as
    ``TypeError`` instead of being silently coerced to ``str(value)``.
    """
    with pytest.raises(TypeError):
        _canonical_json({"tags": {"a", "b"}})

    class _Opaque:
        pass

    with pytest.raises(TypeError):
        _canonical_json(_Opaque())


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
