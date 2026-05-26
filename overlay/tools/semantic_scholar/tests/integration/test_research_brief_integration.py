"""Integration tests for ``SemanticScholarClient.research_brief`` — real
DB, mocked S2 client.

Verifies the brief-plus-papers parent/child write path lands the rows
the unit suite mocks: one ``source_type='research_brief'`` row, N
``source_type='paper'`` rows whose ``parent_document_id`` points back at
the brief, and idempotency on rerun via ``content_hash``.

The Semantic Scholar HTTP call is replaced via ``monkeypatch.setattr``
on the client class itself — same pattern the unit suite uses — so a
flaky external dependency can't sink an otherwise-deterministic
persistence assertion. Everything below the ``search_papers``
boundary runs against the real Postgres provided by the ``db_pool``
fixture in ``conftest.py``.

Test names use the tool-method prefix (``test_research_brief_*``) to
make a CI failure log unambiguous about which surface regressed without
context-switching to the file path. The persistence contract these
tests enforce was previously asserted by
``overlay/workflows/tests/integration/test_research_brief_integration.py``;
that file was removed when the workflow collapsed into a thin wrapper
around this tool method.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from semanticscholar.Paper import Paper

from semantic_scholar.client import SemanticScholarClient


def _paper(paper_id: str, *, title: str | None = None) -> Paper:
    """Minimal S2-shaped :class:`Paper` sufficient for ``build_paper_document``."""
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
    """Build a closure suitable for ``monkeypatch.setattr`` on the class.

    ``research_brief`` bounces the sync ``search_papers`` through
    ``asyncio.to_thread`` rather than maintaining a parallel async HTTP
    path, so the stub stays sync.
    """

    def _search_papers(
        self: SemanticScholarClient,
        query: str,
        limit: int = 10,
        year_from: int | None = None,
        fields: list[str] | None = None,
    ) -> list[Paper]:
        return list(papers)

    return _search_papers


def _set_database_url(monkeypatch: pytest.MonkeyPatch, dsn: str) -> None:
    """Point the client's DATABASE_URL fallback chain at the test DSN.

    The constructor resolves ``database_url`` via constructor arg → env var
    → ``secret(...)``; setting the env var hits the second branch. Patching
    ``secret`` is defensive — keeps the test from hitting the real
    centaur_sdk secret resolver in the (unlikely) event the env var is
    masked.
    """
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setattr(
        "semantic_scholar.client.secret",
        lambda _key, default="": default,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_research_brief_persists_brief_and_papers_with_parent_link(
    db_pool: Any, _test_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_database_url(monkeypatch, _test_dsn)
    papers = [_paper("pA"), _paper("pB"), _paper("pC")]
    monkeypatch.setattr(
        SemanticScholarClient,
        "search_papers",
        _stub_search_papers(papers),
        raising=True,
    )

    client = SemanticScholarClient(api_key="")
    result = await client.research_brief(query="active inference", limit=3)

    assert result["status"] == "completed"
    assert result["results_count"] == 3
    assert result["papers_inserted"] == 3
    assert result["markdown"]

    brief_rows = await db_pool.fetch(
        "SELECT document_id, source_type, metadata FROM company_context_documents "
        "WHERE source_type = 'research_brief'",
    )
    assert len(brief_rows) == 1
    brief_row = brief_rows[0]
    brief_document_id = brief_row["document_id"]
    assert brief_document_id == result["brief_document_id"]
    assert brief_document_id.startswith("semantic_scholar:research_brief:")
    assert brief_row["source_type"] == "research_brief"
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
        assert row["source_type"] == "paper"
        assert row["parent_document_id"] == brief_document_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_research_brief_idempotent_rerun(
    db_pool: Any, _test_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_database_url(monkeypatch, _test_dsn)
    papers = [_paper("pA"), _paper("pB")]
    monkeypatch.setattr(
        SemanticScholarClient,
        "search_papers",
        _stub_search_papers(papers),
        raising=True,
    )

    client = SemanticScholarClient(api_key="")
    first = await client.research_brief(query="active inference", year_from=2023)
    assert first["status"] == "completed"
    assert first["brief_action"] == "inserted"
    assert first["papers_inserted"] == 2

    second = await client.research_brief(query="active inference", year_from=2023)
    assert second["status"] == "completed"
    assert second["brief_action"] == "noop"
    assert second["brief_document_id"] == first["brief_document_id"]
    # Identical S2 stub responses across both runs → every paper hash
    # matches the row already on disk → noop everywhere; nothing
    # should land in the inserted/updated buckets.
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
    db_pool: Any, _test_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_database_url(monkeypatch, _test_dsn)
    monkeypatch.setattr(
        SemanticScholarClient,
        "search_papers",
        _stub_search_papers([]),
        raising=True,
    )

    client = SemanticScholarClient(api_key="")
    result = await client.research_brief(query="quantum gravity nothing matches")
    assert result["status"] == "completed"
    assert result["results_count"] == 0
    assert result["papers_inserted"] == 0
    assert result["markdown"]

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
