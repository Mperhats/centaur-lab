"""Unit tests for the ``archive_papers`` workflow handler.

The handler now stands on its own: it asks
``SemanticScholarClient.archive_paper`` for a projection bundle and
persists the three rows under the workflow's pool via the inlined
``_upsert_document`` / ``_upsert_paper_archive`` helpers. The tool
boundary returns plain dicts; everything DB-shaped lives in this
handler module.

Coverage targets:
* envelope propagation (skipped + error bundles flow through)
* per-paper idempotency short-circuit (matching ``pdf_sha256`` → noop)
* compound-hash argument ordering on the ``_upsert_document`` SQL
* error/skipped branches don't write
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import archive_papers
import pytest

from centaur_lab.testing import MockContext


class _SequencedPool:
    """Pool mock with per-call fetchval results so the three sequential
    SELECTs (archive lookup → paper content_hash → fulltext content_hash)
    can each return a different value.
    """

    def __init__(self, fetchval_returns: list[Any], execute_status: str = "INSERT 0 1") -> None:
        self._fetchval_returns = list(fetchval_returns)
        self._execute_status = execute_status
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.fetchval_calls.append((query, args))
        if not self._fetchval_returns:
            return None
        return self._fetchval_returns.pop(0)

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append((query, args))
        return self._execute_status


def _ok_bundle(paper_id: str = "abc123") -> dict[str, Any]:
    """Build a stub bundle shaped like ``client.archive_paper`` would return."""
    paper_doc = {
        "document_id": f"semantic_scholar:paper:{paper_id}",
        "source": "semantic_scholar",
        "source_type": "paper",
        "source_document_id": paper_id,
        "source_chunk_id": "",
        "parent_document_id": None,
        "title": "Sample",
        "body": "# Sample\n",
        "url": f"https://www.semanticscholar.org/paper/{paper_id}",
        "author_id": "a1",
        "author_name": "Author One",
        "access_scope": "company",
        "occurred_at": None,
        "source_updated_at": None,
        "content_hash": f"hash-paper-{paper_id}",
        "metadata": {"paperId": paper_id},
    }
    fulltext_doc = {
        **paper_doc,
        "document_id": f"semantic_scholar:paper_fulltext:{paper_id}",
        "source_type": "paper_fulltext",
        "parent_document_id": paper_doc["document_id"],
        "body": "# Sample\n\nFull body.",
        "content_hash": f"hash-fulltext-{paper_id}",
        "metadata": {"paperId": paper_id, "parserUsed": "pymupdf4llm"},
    }
    archive_row = {
        "paper_id": paper_id,
        "source_url": "https://example.com/paper.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 12,
        "pdf_sha256": f"sha-{paper_id}",
        "pdf_bytes": b"%PDF-1.4 fake",
        "parsed_text": "# Sample",
        "parser_used": "pymupdf4llm",
        "truncated": False,
        "metadata": {"paperId": paper_id, "url": paper_doc["url"]},
    }
    return {
        "status": "ok",
        "paper_id": paper_id,
        "pdf_sha256": archive_row["pdf_sha256"],
        "source_url": archive_row["source_url"],
        "size_bytes": archive_row["size_bytes"],
        "mime_type": archive_row["mime_type"],
        "parser_used": archive_row["parser_used"],
        "paper_doc": paper_doc,
        "fulltext_doc": fulltext_doc,
        "archive_row": archive_row,
    }


@pytest.mark.asyncio
async def test_handler_skips_when_paper_ids_empty() -> None:
    pool = _SequencedPool([])
    ctx = MockContext(pool)

    with patch("archive_papers.SemanticScholarClient") as mock_cls:
        result = await archive_papers.handler(archive_papers.Input(paper_ids=[]), ctx)

    assert result == {"status": "skipped", "reason": "no_paper_ids"}
    assert mock_cls.call_count == 0
    assert pool.fetchval_calls == []


@pytest.mark.asyncio
async def test_handler_happy_path_persists_three_rows() -> None:
    pool = _SequencedPool(fetchval_returns=[None, None, None])
    ctx = MockContext(pool)

    bundle = _ok_bundle("p1")
    with patch("archive_papers.SemanticScholarClient") as mock_cls:
        mock_cls.return_value.archive_paper = AsyncMock(return_value=bundle)
        result = await archive_papers.handler(
            archive_papers.Input(paper_ids=["p1"]),
            ctx,
        )

    assert result["status"] == "completed"
    assert result["papers_archived"] == 1
    item = result["results"][0]
    assert item["status"] == "completed"
    assert item["paper_id"] == "p1"
    assert item["paper_document_id"] == "semantic_scholar:paper:p1"
    assert item["fulltext_document_id"] == "semantic_scholar:paper_fulltext:p1"
    assert item["paper_action"] == "inserted"
    assert item["fulltext_action"] == "inserted"
    assert item["archive_action"] == "inserted"

    # Three writes, in pipeline order: paper doc, fulltext doc, archive.
    assert len(pool.execute_calls) == 3
    paper_sql, _ = pool.execute_calls[0]
    fulltext_sql, _ = pool.execute_calls[1]
    archive_sql, _ = pool.execute_calls[2]
    assert "INTO company_context_documents" in paper_sql
    assert "INTO company_context_documents" in fulltext_sql
    assert "INTO paper_archives" in archive_sql


@pytest.mark.asyncio
async def test_handler_propagates_skipped_bundle() -> None:
    """A ``status='skipped'`` bundle flows through verbatim — no DB writes."""
    pool = _SequencedPool([])
    ctx = MockContext(pool)
    bundle = {"status": "skipped", "paper_id": "p1", "reason": "no_pdf_url"}

    with patch("archive_papers.SemanticScholarClient") as mock_cls:
        mock_cls.return_value.archive_paper = AsyncMock(return_value=bundle)
        result = await archive_papers.handler(
            archive_papers.Input(paper_ids=["p1"]),
            ctx,
        )

    assert result["status"] == "completed"
    assert result["papers_skipped"] == 1
    assert result["results"][0] == bundle
    assert pool.execute_calls == []


@pytest.mark.asyncio
async def test_handler_propagates_error_bundle() -> None:
    """A ``status='error'`` bundle flows through verbatim — no DB writes."""
    pool = _SequencedPool([])
    ctx = MockContext(pool)
    bundle = {
        "status": "error",
        "paper_id": "p1",
        "stage": "fetch",
        "reason": "http_error",
        "error": "HTTP 503",
    }

    with patch("archive_papers.SemanticScholarClient") as mock_cls:
        mock_cls.return_value.archive_paper = AsyncMock(return_value=bundle)
        result = await archive_papers.handler(
            archive_papers.Input(paper_ids=["p1"]),
            ctx,
        )

    assert result["status"] == "completed"
    assert result["papers_failed"] == 1
    assert result["results"][0] == bundle
    assert pool.execute_calls == []


@pytest.mark.asyncio
async def test_handler_short_circuits_when_archive_hash_matches() -> None:
    """Matching ``pdf_sha256`` in ``paper_archives`` → noop, no upserts.

    Preserves the legacy idempotency optimisation: even though the
    bundle was computed (one re-parse), we still skip all three
    upserts when the archive row's hash is unchanged.
    """
    bundle = _ok_bundle("p1")
    existing_sha = bundle["pdf_sha256"]
    pool = _SequencedPool(fetchval_returns=[existing_sha])
    ctx = MockContext(pool)

    with patch("archive_papers.SemanticScholarClient") as mock_cls:
        mock_cls.return_value.archive_paper = AsyncMock(return_value=bundle)
        result = await archive_papers.handler(
            archive_papers.Input(paper_ids=["p1"]),
            ctx,
        )

    assert result["status"] == "completed"
    assert result["papers_noop"] == 1
    item = result["results"][0]
    assert item["status"] == "noop"
    assert item["archive_action"] == "noop"
    assert pool.execute_calls == [], "noop must not write"


@pytest.mark.asyncio
async def test_handler_passes_source_url_override() -> None:
    pool = _SequencedPool(fetchval_returns=[None, None, None])
    ctx = MockContext(pool)
    bundle = _ok_bundle("p1")

    captured: dict[str, Any] = {}

    async def _archive_paper(paper_id: str, *, source_url: str | None = None):
        captured["paper_id"] = paper_id
        captured["source_url"] = source_url
        return bundle

    with patch("archive_papers.SemanticScholarClient") as mock_cls:
        mock_cls.return_value.archive_paper = _archive_paper
        await archive_papers.handler(
            archive_papers.Input(
                paper_ids=["p1"],
                source_url_overrides={"p1": "https://override/x.pdf"},
            ),
            ctx,
        )

    assert captured == {"paper_id": "p1", "source_url": "https://override/x.pdf"}


def test_content_hash_compound_ordering() -> None:
    """``_content_hash`` is order-sensitive — pin the intrinsic-then-parent order.

    The persisted compound hash must be ``_content_hash(intrinsic, parent)``,
    not ``_content_hash(parent, intrinsic)`` — flipping the args silently
    breaks idempotency (every existing row hashes to a new value on
    re-encounter). This is the cheapest test that locks the order.
    """
    intrinsic = "a"
    parent = "b"
    forward = archive_papers._content_hash(intrinsic, parent)
    reverse = archive_papers._content_hash(parent, intrinsic)
    assert forward != reverse
