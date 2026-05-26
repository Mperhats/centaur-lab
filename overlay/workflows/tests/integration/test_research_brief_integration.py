"""Integration tests for research_brief — real DB, mocked S2 client.

Verifies the brief-plus-papers parent/child write path lands the rows the
unit suite mocks: one ``source_type='research_brief'`` row, N
``source_type='paper'`` rows whose ``parent_document_id`` points back at
the brief, and idempotency on rerun via ``content_hash``.

Test names use the workflow-name prefix (``test_research_brief_*``) rather
than the unit suite's ``test_handler_*`` convention so a CI failure log
makes clear which workflow regressed without context-switching to the
file path.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# Make the workflow modules importable. Mirrors the sys.path bootstrap in the
# unit-test conftest.
_WORKFLOWS_DIR = Path(__file__).resolve().parent.parent.parent
if str(_WORKFLOWS_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKFLOWS_DIR))

from tests._fakes import FakeContext, FakeSemanticScholarClient  # noqa: E402


def _paper(paper_id: str, *, title: str | None = None) -> dict[str, Any]:
    """Minimal S2-shaped paper dict sufficient for ``build_paper_document``."""
    return {
        "paperId": paper_id,
        "title": title or f"Paper {paper_id}",
        "authors": [{"authorId": f"a-{paper_id}", "name": f"Author {paper_id}"}],
        "year": 2024,
        "abstract": f"Abstract for {paper_id}.",
        "citationCount": 7,
        "url": f"https://www.semanticscholar.org/paper/{paper_id}",
        "openAccessPdf": None,
        "venue": "Test Venue",
        "externalIds": {"DOI": f"10.0/{paper_id}"},
    }


@pytest.mark.asyncio
async def test_research_brief_writes_brief_and_papers_with_parent_link(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import research_brief

    papers = [_paper("pA"), _paper("pB")]
    monkeypatch.setattr(
        research_brief,
        "SemanticScholarClient",
        lambda: FakeSemanticScholarClient(search_results=papers),
    )

    result = await research_brief.handler(
        research_brief.Input(query="active inference"),
        FakeContext(db_pool),
    )

    assert result["status"] == "completed"
    assert result["results_count"] == 2
    assert result["papers_inserted"] == 2

    brief_rows = await db_pool.fetch(
        "SELECT document_id FROM company_context_documents "
        "WHERE source_type = 'research_brief'",
    )
    assert len(brief_rows) == 1
    brief_document_id = brief_rows[0]["document_id"]
    assert brief_document_id == result["brief_document_id"]

    paper_rows = await db_pool.fetch(
        "SELECT document_id, parent_document_id FROM company_context_documents "
        "WHERE source_type = 'paper' ORDER BY document_id",
    )
    assert len(paper_rows) == 2
    for row in paper_rows:
        assert row["parent_document_id"] == brief_document_id


@pytest.mark.asyncio
async def test_research_brief_is_idempotent_on_rerun(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import research_brief

    papers = [_paper("pA"), _paper("pB")]
    monkeypatch.setattr(
        research_brief,
        "SemanticScholarClient",
        lambda: FakeSemanticScholarClient(search_results=papers),
    )
    inp = research_brief.Input(query="active inference")

    first = await research_brief.handler(inp, FakeContext(db_pool))
    assert first["papers_inserted"] == 2
    assert first["papers_noop"] == 0
    assert first["brief_action"] == "inserted"

    second = await research_brief.handler(inp, FakeContext(db_pool))
    assert second["papers_inserted"] == 0
    assert second["papers_updated"] == 0
    assert second["papers_noop"] == 2
    assert second["brief_action"] == "noop"

    count = await db_pool.fetchval("SELECT COUNT(*) FROM company_context_documents")
    assert count == 3


@pytest.mark.asyncio
async def test_research_brief_no_results_writes_brief_only(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import research_brief

    monkeypatch.setattr(
        research_brief,
        "SemanticScholarClient",
        lambda: FakeSemanticScholarClient(search_results=[]),
    )

    result = await research_brief.handler(
        research_brief.Input(query="quantum gravity nothing matches"),
        FakeContext(db_pool),
    )

    assert result["status"] == "completed"
    assert result["results_count"] == 0
    assert result["papers_inserted"] == 0

    brief_count = await db_pool.fetchval(
        "SELECT COUNT(*) FROM company_context_documents "
        "WHERE source_type = 'research_brief'",
    )
    paper_count = await db_pool.fetchval(
        "SELECT COUNT(*) FROM company_context_documents "
        "WHERE source_type = 'paper'",
    )
    assert brief_count == 1
    assert paper_count == 0
