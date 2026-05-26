"""Unit tests for ``SemanticScholarClient.research_brief``.

The method is now pure with respect to Postgres — it searches Semantic
Scholar, renders a Markdown brief, projects each paper into a
``company_context_documents`` row dict, and returns the bundle. No
asyncpg, no pool. Persistence-level idempotency and parent linkage
across real rows is the workflow handler's job; that's covered in
``overlay/workflows/tests/test_research_brief.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from semanticscholar.Paper import Paper

from semantic_scholar.client import SemanticScholarClient


def _run_brief(client: SemanticScholarClient, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Drive the async ``research_brief`` from a sync test."""
    return asyncio.run(client.research_brief(*args, **kwargs))


def _install_search_papers(
    monkeypatch: pytest.MonkeyPatch,
    papers: list[Paper] | None = None,
    *,
    exc: BaseException | None = None,
) -> list[dict[str, Any]]:
    """Patch ``SemanticScholarClient.search_papers`` and record its calls."""
    calls: list[dict[str, Any]] = []

    def _search_papers(self, query, limit=10, year_from=None, fields=None):  # type: ignore[no-untyped-def]
        calls.append({"query": query, "limit": limit, "year_from": year_from})
        if exc is not None:
            raise exc
        return list(papers or [])

    monkeypatch.setattr(SemanticScholarClient, "search_papers", _search_papers, raising=True)
    return calls


def _paper(paper_id: str) -> Paper:
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


def _client() -> SemanticScholarClient:
    return SemanticScholarClient(api_key="")


def test_research_brief_empty_query_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace-only query short-circuits before any I/O."""
    search_calls = _install_search_papers(monkeypatch, [])

    result = _run_brief(_client(), "   ")

    assert result == {"status": "error", "query": "   ", "error": "query cannot be empty"}
    assert search_calls == []


def test_research_brief_non_positive_limit_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """``limit <= 0`` short-circuits before any I/O."""
    search_calls = _install_search_papers(monkeypatch, [])

    result = _run_brief(_client(), "anything", limit=0)

    assert result == {
        "status": "error",
        "query": "anything",
        "error": "limit must be positive",
    }
    assert search_calls == []


def test_research_brief_clamps_limit_above_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """``limit`` is clamped to ``MAX_RESEARCH_BRIEF_LIMIT`` before reaching S2.

    The workflow's input schema bounds limit to 1..100, but the client clamps
    further to keep the S2 fan-out and per-brief LLM/index budget bounded.
    Asserts the *clamped* value reaches ``search_papers``, not the raw user
    input.
    """
    from semantic_scholar.client import MAX_RESEARCH_BRIEF_LIMIT

    search_calls = _install_search_papers(monkeypatch, [])

    result = _run_brief(_client(), "anything", limit=100)

    assert result["status"] == "ok"
    assert result["limit"] == MAX_RESEARCH_BRIEF_LIMIT
    assert search_calls == [
        {"query": "anything", "limit": MAX_RESEARCH_BRIEF_LIMIT, "year_from": None}
    ]


def test_research_brief_returns_bundle_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Success path: bundle has brief_doc + paper_docs with parent linkage."""
    papers = [_paper("p1"), _paper("p2"), _paper("p3")]
    search_calls = _install_search_papers(monkeypatch, papers)

    result = _run_brief(_client(), "active inference", limit=3, year_from=2020)

    assert result["status"] == "ok"
    assert result["query"] == "active inference"
    assert result["year_from"] == 2020
    assert result["limit"] == 3
    assert result["results_count"] == 3
    assert isinstance(result["markdown"], str) and result["markdown"]

    brief_doc = result["brief_doc"]
    assert brief_doc["source_type"] == "research_brief"
    assert brief_doc["document_id"].startswith("semantic_scholar:research_brief:")
    assert brief_doc["parent_document_id"] is None

    paper_docs = result["paper_docs"]
    assert len(paper_docs) == 3
    for paper_doc in paper_docs:
        assert paper_doc["source_type"] == "paper"
        assert paper_doc["parent_document_id"] == brief_doc["document_id"]

    assert search_calls == [{"query": "active inference", "limit": 3, "year_from": 2020}]


def test_research_brief_no_results_returns_empty_paper_docs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A query with zero results still produces a brief — no paper rows."""
    _install_search_papers(monkeypatch, [])

    result = _run_brief(_client(), "no matches please")

    assert result["status"] == "ok"
    assert result["results_count"] == 0
    assert result["paper_docs"] == []
    assert result["brief_doc"]["metadata"]["results_count"] == 0


def test_research_brief_skips_papers_missing_paperId(monkeypatch: pytest.MonkeyPatch) -> None:
    """Papers without ``paperId`` are dropped from ``paper_docs`` (no stable PK).

    The render-side helper still walks every paper for the Markdown
    body, so the bad paper needs the full wire shape minus ``paperId``
    — only the projection step is expected to drop it.
    """
    good = _paper("p1")
    bad_dict = {
        "title": "no id",
        "authors": [],
        "year": 2024,
        "abstract": "",
        "citationCount": 0,
        "url": "https://example.invalid/no-id",
        "openAccessPdf": None,
        "venue": "",
        "externalIds": {},
    }
    bad = Paper(bad_dict)
    _install_search_papers(monkeypatch, [good, bad])

    result = _run_brief(_client(), "anything")

    assert result["status"] == "ok"
    assert len(result["paper_docs"]) == 1
    assert result["paper_docs"][0]["source_document_id"] == "p1"


def test_research_brief_search_failure_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """S2 failure surfaces as a structured error envelope; never raises."""
    _install_search_papers(monkeypatch, exc=RuntimeError("S2 down"))

    result = _run_brief(_client(), "anything")

    assert result == {"status": "error", "query": "anything", "error": "S2 down"}


def test_research_brief_does_not_touch_asyncpg(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bundle method must never reach for asyncpg — the workflow owns the pool.

    Sentinel test for the tool-vs-workflow boundary: if a future refactor
    re-introduces ``asyncpg.connect`` inside ``research_brief``, this fails
    loudly. The shim raises rather than mocking so any code path that tries
    to open a connection blows up the test.
    """
    import asyncpg

    def _explode(*_args, **_kwargs):
        raise AssertionError("research_brief must not call asyncpg.connect")

    monkeypatch.setattr(asyncpg, "connect", _explode)
    _install_search_papers(monkeypatch, [_paper("p1")])

    result = _run_brief(_client(), "anything")

    assert result["status"] == "ok"
