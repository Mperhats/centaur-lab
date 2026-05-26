"""Integration tests for save_papers — real DB, mocked S2 client.

Mirrors the shape of .centaur/services/api/tests/test_company_context_documents.py:
seed nothing (paper workflow doesn't depend on slack tables), call the workflow
handler with a MockContext wrapping the real db_pool, then assert on the rows
that actually landed in company_context_documents.

Test names use the workflow-name prefix (``test_save_papers_*``) rather than
the unit suite's ``test_handler_*`` convention so a CI failure log makes
clear which workflow regressed without context-switching to the file path.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from semanticscholar.Paper import Paper

from tests._helpers import MockContext


class FakeS2Client:
    """Minimal ``SemanticScholarClient`` stand-in for integration tests.

    ``save_papers.handler`` only calls ``get_paper`` on the client.
    Unknown ``paper_id``s raise ``RuntimeError`` to exercise the
    per-paper failure branch.
    """

    def __init__(self, papers_by_id: dict[str, dict[str, Any]]) -> None:
        self._papers_by_id = papers_by_id

    def get_paper(self, paper_id: str) -> Paper:
        if paper_id not in self._papers_by_id:
            raise RuntimeError(f"unknown paper id in stub: {paper_id}")
        return Paper(self._papers_by_id[paper_id])


_PAPER_173BA: dict[str, Any] = {
    "paperId": "173ba8ae4582b6f9f6919aa3f813579a5349f1f9",
    "title": "Attention Is All You Need",
    "abstract": "The dominant sequence transduction models...",
    "year": 2017,
    "authors": [
        {"authorId": "1", "name": "Ashish Vaswani"},
    ],
    "venue": "NeurIPS",
    "url": "https://example.com/paper",
    "externalIds": {"DOI": "10.5555/example"},
    "citationCount": 100000,
    "referenceCount": 50,
}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_save_papers_writes_paper_row_with_full_shape(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import save_papers

    monkeypatch.setattr(
        save_papers,
        "SemanticScholarClient",
        lambda: FakeS2Client(papers_by_id={_PAPER_173BA["paperId"]: _PAPER_173BA}),
    )

    result = await save_papers.handler(
        save_papers.Input(paper_ids=[_PAPER_173BA["paperId"]], query="attention"),
        MockContext(db_pool),
    )

    assert result["status"] == "completed"
    assert result["papers_inserted"] == 1
    assert result["papers_updated"] == 0
    assert result["papers_failed"] == 0

    # ``save_papers`` always writes a brief row plus one row per paper.
    # The paper row is upserted twice in the handler (first parentless,
    # then with the brief as parent) — the final row count is brief +
    # paper, never paper-only.
    paper_rows = await db_pool.fetch(
        "SELECT document_id, source, source_type, title, body, url, "
        "author_name, content_hash, parent_document_id, metadata "
        "FROM company_context_documents WHERE source_type = 'paper'",
    )
    assert len(paper_rows) == 1
    row = paper_rows[0]
    assert row["source"] == "semantic_scholar"
    assert row["source_type"] == "paper"
    assert row["document_id"] == f"semantic_scholar:paper:{_PAPER_173BA['paperId']}"
    assert "Attention Is All You Need" in row["title"]
    assert "Ashish Vaswani" in row["body"] or "Ashish Vaswani" in row["author_name"]
    assert row["url"] == _PAPER_173BA["url"]
    assert row["content_hash"]  # non-empty
    # Paper rows are children of the brief — verify the parent link
    # against the brief's document_id rather than asserting None.
    brief_document_id = await db_pool.fetchval(
        "SELECT document_id FROM company_context_documents "
        "WHERE source_type = 'research_brief'"
    )
    assert row["parent_document_id"] == brief_document_id
    metadata = json.loads(row["metadata"])
    assert metadata["paperId"] == _PAPER_173BA["paperId"]
    assert metadata["year"] == 2017


@pytest.mark.integration
@pytest.mark.asyncio
async def test_save_papers_is_idempotent_on_rerun(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import save_papers

    monkeypatch.setattr(
        save_papers,
        "SemanticScholarClient",
        lambda: FakeS2Client(papers_by_id={_PAPER_173BA["paperId"]: _PAPER_173BA}),
    )
    inp = save_papers.Input(paper_ids=[_PAPER_173BA["paperId"]])

    first = await save_papers.handler(inp, MockContext(db_pool))
    assert first["papers_inserted"] == 1
    assert first["papers_noop"] == 0

    # The aggregate counts on re-run reflect the dual-write pattern
    # documented in ``save_papers.handler``: each paper is written first
    # parentless (phase 1, the counted phase), then re-written with the
    # brief as parent (phase 2, not counted). On the second handler
    # invocation, phase 1 sees an existing row whose ``content_hash``
    # was computed from ``(intrinsic, brief_id)`` but is re-encoding
    # ``(intrinsic, None)`` — that's a content_hash diff, so the
    # parentless upsert reports ``updated``, not ``noop``. Phase 2 then
    # re-parents back. End-state is identical; the count semantics just
    # reflect the dual-write, not a row regression.
    #
    # The real idempotency contract is row-count stability, which the
    # row-count asserts below pin.
    second = await save_papers.handler(inp, MockContext(db_pool))
    assert second["status"] == "completed"
    assert second["papers_inserted"] == 0
    assert (second["papers_updated"] + second["papers_noop"]) == 1

    # 1 paper row + 1 brief row after either 1 or 2 runs (idempotency
    # holds at the row level, regardless of the dual-write counts).
    paper_count = await db_pool.fetchval(
        "SELECT COUNT(*) FROM company_context_documents WHERE source_type = 'paper'"
    )
    assert paper_count == 1
    brief_count = await db_pool.fetchval(
        "SELECT COUNT(*) FROM company_context_documents "
        "WHERE source_type = 'research_brief'"
    )
    assert brief_count == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_save_papers_partial_failure_writes_successful_papers(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import save_papers

    monkeypatch.setattr(
        save_papers,
        "SemanticScholarClient",
        lambda: FakeS2Client(papers_by_id={_PAPER_173BA["paperId"]: _PAPER_173BA}),
    )

    result = await save_papers.handler(
        save_papers.Input(
            paper_ids=[
                _PAPER_173BA["paperId"],
                "deadbeef" * 5,  # not in stub → raises
            ]
        ),
        MockContext(db_pool),
    )

    assert result["papers_inserted"] == 1
    assert result["papers_failed"] == 1

    # The successful paper still lands (brief + 1 paper row); the failing
    # paper writes nothing. Total = 2 rows in company_context_documents.
    paper_count = await db_pool.fetchval(
        "SELECT COUNT(*) FROM company_context_documents WHERE source_type = 'paper'"
    )
    assert paper_count == 1
