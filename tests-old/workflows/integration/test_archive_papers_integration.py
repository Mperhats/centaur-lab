"""Integration tests for archive_papers — real DB, stubbed S2 client.

Mirrors ``test_save_papers_integration.py`` and
``test_research_brief_integration.py``: stub the S2/PDF network surface
(``SemanticScholarClient.archive_paper`` returns a fully-formed bundle),
call the handler against the live ``centaur_test`` Postgres, then assert
on what landed in both ``paper_archives`` AND ``company_context_documents``.

The bundle shape is byte-identical to what the real
``client.archive_paper`` returns post-refactor — that's deliberate so
this test would catch a wire-format drift between the tool and the
workflow's inlined upsert helpers. Without this, the only place that
contract is exercised end-to-end is a live Slack run, which is too
slow and too flaky (publisher 403s, network) to function as a
regression test.

Why a separate integration test even though unit tests already cover
the handler? Three things only show up against a real DB:

1. The compound-hash idempotency contract in ``_upsert_document``
   (intrinsic_hash + effective_parent) — unit tests assert ordering
   but not that a real INSERT…ON CONFLICT round-trip preserves it.
2. The ``paper_archives`` schema match — column names, BYTEA round-trip,
   JSONB metadata serialization. The migration we shipped this branch
   defines the table; this test is what proves the workflow speaks the
   same shape.
3. Cross-table parent linkage — ``paper_fulltext`` rows hang off
   ``paper`` rows via ``parent_document_id``. A mock pool can't catch a
   regression where the FK target gets the wrong document_id.
"""

from __future__ import annotations

import json
from typing import Any

import archive_papers
import pytest

from tests._helpers import MockContext


def _ok_bundle(
    paper_id: str,
    *,
    pdf_sha256: str | None = None,
    title: str = "Sample Paper",
    body: str = "# Sample\n\nFull body content.",
) -> dict[str, Any]:
    """Build a bundle byte-identical to ``client.archive_paper``'s shape.

    Keep aligned with ``tools/semantic_scholar/projections/{paper,fulltext,archive}.py``
    — if those projection builders gain a field, mirror it here so the
    test reflects what the tool actually returns.
    """
    sha = pdf_sha256 or f"sha-{paper_id}"
    paper_doc = {
        "document_id": f"semantic_scholar:paper:{paper_id}",
        "source": "semantic_scholar",
        "source_type": "paper",
        "source_document_id": paper_id,
        "source_chunk_id": "",
        "parent_document_id": None,
        "title": title,
        "body": f"{title}\n\nAbstract for {paper_id}.",
        "url": f"https://www.semanticscholar.org/paper/{paper_id}",
        "author_id": f"a-{paper_id}",
        "author_name": f"Author {paper_id}",
        "access_scope": "company",
        "occurred_at": None,
        "source_updated_at": None,
        "content_hash": f"hash-paper-{paper_id}",
        "metadata": {"paperId": paper_id, "year": 2024},
    }
    fulltext_doc = {
        **paper_doc,
        "document_id": f"semantic_scholar:paper_fulltext:{paper_id}",
        "source_type": "paper_fulltext",
        "parent_document_id": paper_doc["document_id"],
        "body": body,
        "content_hash": f"hash-fulltext-{paper_id}",
        "metadata": {
            "paperId": paper_id,
            "parserUsed": "pymupdf4llm",
            "sizeBytes": len(body.encode("utf-8")),
        },
    }
    archive_row = {
        "paper_id": paper_id,
        "source_url": f"https://arxiv.org/pdf/{paper_id}.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 1024,
        "pdf_sha256": sha,
        "pdf_bytes": b"%PDF-1.4 fake bytes for " + paper_id.encode(),
        "parsed_text": body,
        "parser_used": "pymupdf4llm",
        "truncated": False,
        "metadata": {"paperId": paper_id, "url": paper_doc["url"]},
    }
    return {
        "status": "ok",
        "paper_id": paper_id,
        "pdf_sha256": sha,
        "source_url": archive_row["source_url"],
        "size_bytes": archive_row["size_bytes"],
        "mime_type": archive_row["mime_type"],
        "parser_used": archive_row["parser_used"],
        "paper_doc": paper_doc,
        "fulltext_doc": fulltext_doc,
        "archive_row": archive_row,
    }


class _StubClient:
    """Replace ``SemanticScholarClient`` with a stub that returns bundles
    from an in-memory map. Unknown paper IDs return an error envelope so
    the partial-failure branch can be exercised without a real 403/timeout.
    """

    def __init__(self, bundles: dict[str, dict[str, Any]]) -> None:
        self._bundles = bundles

    async def archive_paper(
        self, paper_id: str, *, source_url: str | None = None
    ) -> dict[str, Any]:
        if paper_id in self._bundles:
            return self._bundles[paper_id]
        return {
            "status": "error",
            "paper_id": paper_id,
            "stage": "fetch",
            "reason": "http_error",
            "error": f"stub: no bundle for {paper_id}",
        }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_archive_papers_writes_three_rows_with_parent_link(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: one paper → one ``paper_archives`` row + ``paper`` doc
    + ``paper_fulltext`` doc parented under the paper.
    """
    bundle = _ok_bundle("p1")
    monkeypatch.setattr(
        archive_papers,
        "SemanticScholarClient",
        lambda: _StubClient({"p1": bundle}),
    )

    result = await archive_papers.handler(
        archive_papers.Input(paper_ids=["p1"]),
        MockContext(db_pool),
    )

    assert result["status"] == "completed"
    assert result["papers_archived"] == 1
    assert result["papers_failed"] == 0
    item = result["results"][0]
    assert item["paper_action"] == "inserted"
    assert item["fulltext_action"] == "inserted"
    assert item["archive_action"] == "inserted"

    archive_rows = await db_pool.fetch(
        "SELECT paper_id, source_url, mime_type, size_bytes, pdf_sha256, "
        "pdf_bytes, parsed_text, parser_used, truncated, metadata "
        "FROM paper_archives"
    )
    assert len(archive_rows) == 1
    arow = archive_rows[0]
    assert arow["paper_id"] == "p1"
    assert arow["source_url"] == bundle["archive_row"]["source_url"]
    assert arow["mime_type"] == "application/pdf"
    assert arow["size_bytes"] == 1024
    assert arow["pdf_sha256"] == "sha-p1"
    assert bytes(arow["pdf_bytes"]) == bundle["archive_row"]["pdf_bytes"]
    assert arow["parsed_text"] == bundle["archive_row"]["parsed_text"]
    assert arow["parser_used"] == "pymupdf4llm"
    assert arow["truncated"] is False
    metadata = json.loads(arow["metadata"])
    assert metadata["paperId"] == "p1"

    doc_rows = await db_pool.fetch(
        "SELECT document_id, source_type, parent_document_id "
        "FROM company_context_documents "
        "WHERE source_type IN ('paper', 'paper_fulltext') "
        "ORDER BY source_type"
    )
    assert len(doc_rows) == 2
    paper_row = next(r for r in doc_rows if r["source_type"] == "paper")
    fulltext_row = next(r for r in doc_rows if r["source_type"] == "paper_fulltext")
    assert paper_row["document_id"] == "semantic_scholar:paper:p1"
    assert paper_row["parent_document_id"] is None
    assert fulltext_row["document_id"] == "semantic_scholar:paper_fulltext:p1"
    assert fulltext_row["parent_document_id"] == paper_row["document_id"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_archive_papers_idempotent_on_unchanged_pdf(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second run with identical ``pdf_sha256`` short-circuits to noop and
    skips all three upserts. This is the cheapest cost-saver the workflow
    has — re-archiving an unchanged PDF must not re-write rows.
    """
    bundle = _ok_bundle("p1")
    monkeypatch.setattr(
        archive_papers,
        "SemanticScholarClient",
        lambda: _StubClient({"p1": bundle}),
    )

    first = await archive_papers.handler(
        archive_papers.Input(paper_ids=["p1"]),
        MockContext(db_pool),
    )
    assert first["papers_archived"] == 1
    assert first["papers_noop"] == 0

    second = await archive_papers.handler(
        archive_papers.Input(paper_ids=["p1"]),
        MockContext(db_pool),
    )
    assert second["papers_noop"] == 1
    assert second["papers_archived"] == 0
    item = second["results"][0]
    assert item["status"] == "noop"
    assert item["archive_action"] == "noop"

    archive_count = await db_pool.fetchval("SELECT COUNT(*) FROM paper_archives")
    assert archive_count == 1
    doc_count = await db_pool.fetchval(
        "SELECT COUNT(*) FROM company_context_documents "
        "WHERE source_type IN ('paper', 'paper_fulltext')"
    )
    assert doc_count == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_archive_papers_re_archives_when_pdf_sha_changes(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different ``pdf_sha256`` on the same ``paper_id`` should UPDATE,
    not insert a second row. Validates the ``paper_archives`` PK
    contract (paper_id) plus the workflow's update path.
    """
    bundle_v1 = _ok_bundle("p1", pdf_sha256="sha-v1", body="# v1 body")
    monkeypatch.setattr(
        archive_papers,
        "SemanticScholarClient",
        lambda: _StubClient({"p1": bundle_v1}),
    )
    first = await archive_papers.handler(
        archive_papers.Input(paper_ids=["p1"]),
        MockContext(db_pool),
    )
    assert first["results"][0]["archive_action"] == "inserted"

    bundle_v2 = _ok_bundle("p1", pdf_sha256="sha-v2", body="# v2 body")
    monkeypatch.setattr(
        archive_papers,
        "SemanticScholarClient",
        lambda: _StubClient({"p1": bundle_v2}),
    )
    second = await archive_papers.handler(
        archive_papers.Input(paper_ids=["p1"]),
        MockContext(db_pool),
    )
    assert second["results"][0]["archive_action"] == "updated"

    archive_rows = await db_pool.fetch(
        "SELECT paper_id, pdf_sha256, parsed_text FROM paper_archives"
    )
    assert len(archive_rows) == 1
    assert archive_rows[0]["pdf_sha256"] == "sha-v2"
    assert archive_rows[0]["parsed_text"] == "# v2 body"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_archive_papers_partial_failure_persists_successful_ones(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One paper fails (stub returns error envelope), the other succeeds.
    The successful paper must still land in both tables — verifies the
    biorxiv/Cloudflare-style real-world scenario from today's Slack
    test, where 1-of-3 PDFs 403'd but the rest were archived cleanly.
    """
    ok_bundle = _ok_bundle("p_ok")
    monkeypatch.setattr(
        archive_papers,
        "SemanticScholarClient",
        lambda: _StubClient({"p_ok": ok_bundle}),
    )

    result = await archive_papers.handler(
        archive_papers.Input(paper_ids=["p_ok", "p_fail_unknown"]),
        MockContext(db_pool),
    )

    assert result["status"] == "completed"
    assert result["papers_archived"] == 1
    assert result["papers_failed"] == 1

    archive_count = await db_pool.fetchval("SELECT COUNT(*) FROM paper_archives")
    assert archive_count == 1
    surviving = await db_pool.fetchval("SELECT paper_id FROM paper_archives")
    assert surviving == "p_ok"
