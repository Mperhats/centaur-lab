"""Integration sanity tests for ``SemanticScholarClient.research_brief``.

After the tool-vs-workflow refactor, ``research_brief`` returns a
projection bundle and does NOT persist. The persistence contract lives
in the workflow handler — see
``overlay/workflows/tests/integration/test_research_brief_integration.py``
for the real-DB round-trip that asserts on rows landing in
``company_context_documents``.

This file retains only the cheap structural assertions that confirm
the tool's bundle shape under a realistic stub: that the brief row dict
and each paper row dict are well-formed and stable across reruns. The
``db_pool`` fixture is unused but the file is kept under
``tests/integration/`` so it shares the same gating env var setup with
its sibling, making sense of CI logs without context-switching.
"""

from __future__ import annotations

import pytest
from semanticscholar.Paper import Paper

from tools.semantic_scholar.client import SemanticScholarClient


def _paper(paper_id: str) -> Paper:
    """Minimal S2-shaped :class:`Paper` sufficient for the projection layer."""
    return Paper(
        {
            "paperId": paper_id,
            "title": f"Paper {paper_id}",
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
    """Build a closure suitable for ``monkeypatch.setattr`` on the class."""

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
async def test_research_brief_bundle_shape_and_parent_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bundle has ``brief_doc`` + ``paper_docs``; every paper points at the brief."""
    papers = [_paper("pA"), _paper("pB"), _paper("pC")]
    monkeypatch.setattr(
        SemanticScholarClient,
        "search_papers",
        _stub_search_papers(papers),
        raising=True,
    )

    client = SemanticScholarClient(api_key="")
    bundle = await client.research_brief(query="active inference", limit=3)

    assert bundle["status"] == "ok"
    assert bundle["results_count"] == 3
    brief_doc_id = bundle["brief_doc"]["document_id"]
    assert brief_doc_id.startswith("semantic_scholar:research_brief:")
    paper_doc_ids = {pd["document_id"] for pd in bundle["paper_docs"]}
    expected = {f"semantic_scholar:paper:{p.paperId}" for p in papers}
    assert paper_doc_ids == expected
    for paper_doc in bundle["paper_docs"]:
        assert paper_doc["parent_document_id"] == brief_doc_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_research_brief_bundle_is_stable_across_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identical inputs → identical brief ``document_id`` (idempotency input)."""
    papers = [_paper("pA"), _paper("pB")]
    monkeypatch.setattr(
        SemanticScholarClient,
        "search_papers",
        _stub_search_papers(papers),
        raising=True,
    )

    client = SemanticScholarClient(api_key="")
    first = await client.research_brief(query="active inference", year_from=2023)
    second = await client.research_brief(query="active inference", year_from=2023)

    assert first["brief_doc"]["document_id"] == second["brief_doc"]["document_id"]
    assert first["brief_doc"]["content_hash"] == second["brief_doc"]["content_hash"]
