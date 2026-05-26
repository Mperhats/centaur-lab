"""Integration tests for the ``research_brief`` workflow handler.

Exercises the full handler against a real Postgres: the inlined
``_upsert_document`` SQL lands rows in ``company_context_documents``
(one ``source_type='research_brief'`` plus N ``source_type='paper'``
rows, each parented under the brief). The Semantic Scholar HTTP call
is replaced with a stub so a flaky external dependency can't sink an
otherwise-deterministic persistence assertion.

When the tool's ``research_brief`` lived under ``tools/`` and persisted
as a side effect, this contract was asserted at the tool boundary. The
boundary moved to the workflow when the tool became a pure bundle
builder, and so did this test.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import research_brief
from semantic_scholar.client import SemanticScholarClient
from semanticscholar.Paper import Paper

from centaur_lab.testing import MockContext


def _paper(paper_id: str, *, title: str | None = None) -> Paper:
    return Paper(
        {
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
    )


def _stub_search_papers(papers: list[Paper]):
    """Closure suitable for ``monkeypatch.setattr`` on the SS client class."""

    def _search_papers(
        self: SemanticScholarClient,
        query: str,
        limit: int = 10,
        year_from: int | None = None,
        fields: list[str] | None = None,
    ) -> list[Paper]:
        return list(papers)

    return _search_papers


@pytest.mark.integration
@pytest.mark.asyncio
async def test_research_brief_persists_brief_and_papers_with_parent_link(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    papers = [_paper("pA"), _paper("pB"), _paper("pC")]
    monkeypatch.setattr(
        SemanticScholarClient,
        "search_papers",
        _stub_search_papers(papers),
        raising=True,
    )

    result = await research_brief.handler(
        research_brief.Input(query="active inference", limit=3),
        MockContext(db_pool),
    )

    assert result["status"] == "completed"
    assert result["results_count"] == 3
    assert result["papers_inserted"] == 3

    brief_rows = await db_pool.fetch(
        "SELECT document_id, source_type, metadata FROM company_context_documents "
        "WHERE source_type = 'research_brief'",
    )
    assert len(brief_rows) == 1
    brief_row = brief_rows[0]
    brief_document_id = brief_row["document_id"]
    assert brief_document_id == result["brief_document_id"]
    assert brief_document_id.startswith("semantic_scholar:research_brief:")
    brief_metadata = json.loads(brief_row["metadata"])
    assert brief_metadata["query"] == "active inference"

    paper_rows = await db_pool.fetch(
        "SELECT document_id, source_type, parent_document_id "
        "FROM company_context_documents "
        "WHERE source_type = 'paper' ORDER BY document_id",
    )
    assert len(paper_rows) == 3
    expected_doc_ids = {f"semantic_scholar:paper:{p.paperId}" for p in papers}
    actual_doc_ids = {row["document_id"] for row in paper_rows}
    assert actual_doc_ids == expected_doc_ids
    for row in paper_rows:
        assert row["parent_document_id"] == brief_document_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_research_brief_idempotent_rerun(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    papers = [_paper("pA"), _paper("pB")]
    monkeypatch.setattr(
        SemanticScholarClient,
        "search_papers",
        _stub_search_papers(papers),
        raising=True,
    )

    first = await research_brief.handler(
        research_brief.Input(query="active inference", year_from=2023),
        MockContext(db_pool),
    )
    assert first["status"] == "completed"
    assert first["brief_action"] == "inserted"
    assert first["papers_inserted"] == 2

    second = await research_brief.handler(
        research_brief.Input(query="active inference", year_from=2023),
        MockContext(db_pool),
    )
    assert second["status"] == "completed"
    assert second["brief_action"] == "noop"
    assert second["brief_document_id"] == first["brief_document_id"]
    assert second["papers_inserted"] == 0
    assert second["papers_updated"] == 0
    assert second["papers_noop"] == 2

    brief_count = await db_pool.fetchval(
        "SELECT COUNT(*) FROM company_context_documents WHERE document_id = $1",
        first["brief_document_id"],
    )
    assert brief_count == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_research_brief_no_results_brief_only(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        SemanticScholarClient,
        "search_papers",
        _stub_search_papers([]),
        raising=True,
    )

    result = await research_brief.handler(
        research_brief.Input(query="quantum gravity nothing matches"),
        MockContext(db_pool),
    )
    assert result["status"] == "completed"
    assert result["results_count"] == 0
    assert result["papers_inserted"] == 0

    brief_rows = await db_pool.fetch(
        "SELECT document_id, metadata FROM company_context_documents "
        "WHERE source_type = 'research_brief'",
    )
    assert len(brief_rows) == 1
    brief_metadata = json.loads(brief_rows[0]["metadata"])
    assert brief_metadata["results_count"] == 0

    paper_count = await db_pool.fetchval(
        "SELECT COUNT(*) FROM company_context_documents "
        "WHERE source_type = 'paper' AND parent_document_id = $1",
        brief_rows[0]["document_id"],
    )
    assert paper_count == 0
