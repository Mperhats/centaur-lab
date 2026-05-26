"""Tests for the live-only ``SemanticScholarClient.search`` method."""

from __future__ import annotations

from typing import Any

import pytest
from semanticscholar.Paper import Paper

from tools.semantic_scholar.client import SemanticScholarClient


def _live_paper_dict(paper_id: str, *, year: int = 2024) -> dict[str, Any]:
    return {
        "paperId": paper_id,
        "title": f"Paper {paper_id}",
        "year": year,
        "authors": [{"name": "A. Author"}],
        "abstract": "An abstract.",
        "url": f"https://example.invalid/{paper_id}",
        "citationCount": 10,
        "openAccessPdf": None,
    }


def _live_paper(paper_id: str, *, year: int = 2024) -> Paper:
    """Build an upstream :class:`Paper` for use as a ``search_papers`` mock return.

    The library's ``Paper(data)`` stores ``data`` by reference; ``raw_data``
    returns the input dict verbatim, which is what the agent boundary
    contract assertion below (``result["results"] == [_live_paper_dict(...)]``)
    relies on.
    """
    return Paper(_live_paper_dict(paper_id, year=year))


def _install_search_papers(
    monkeypatch: pytest.MonkeyPatch,
    papers: list[Paper] | None = None,
    *,
    exc: BaseException | None = None,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _stub(
        self: SemanticScholarClient,
        query: str,
        limit: int = 10,
        year_from: int | None = None,
        **kwargs: Any,
    ) -> list[Paper]:
        calls.append(
            {
                "query": query,
                "limit": limit,
                "year_from": year_from,
            }
        )
        if exc is not None:
            raise exc
        return papers or []

    monkeypatch.setattr(
        SemanticScholarClient,
        "search_papers",
        _stub,
        raising=True,
    )
    return calls


def test_search_empty_query_returns_error() -> None:
    client = SemanticScholarClient(api_key="")
    assert client.search("") == {"status": "error", "error": "query cannot be empty"}
    assert client.search("   ") == {"status": "error", "error": "query cannot be empty"}


def test_search_returns_live_papers(monkeypatch: pytest.MonkeyPatch) -> None:
    papers = [_live_paper("p1"), _live_paper("p2")]
    calls = _install_search_papers(monkeypatch, papers)

    result = SemanticScholarClient(api_key="").search("graph neural networks")

    assert result["status"] == "ok"
    assert result["query"] == "graph neural networks"
    assert result["count"] == 2
    # Wire shape contract: ``Paper(data).raw_data`` returns ``data`` by
    # reference, so results equal the original dicts the agent harness /
    # Slack renderer / CLI --json depend on.
    assert result["results"] == [_live_paper_dict("p1"), _live_paper_dict("p2")]
    assert calls == [
        {
            "query": "graph neural networks",
            "limit": 10,
            "year_from": None,
        }
    ]


def test_search_clamps_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_search_papers(monkeypatch, [])

    result = SemanticScholarClient(api_key="").search("foo", limit=999)

    assert result["status"] == "ok"
    assert result["limit"] == 50
    assert calls[-1]["limit"] == 50


def test_search_passes_year_from(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_search_papers(monkeypatch, [])

    result = SemanticScholarClient(api_key="").search("xyz", year_from=2020)

    assert result["status"] == "ok"
    assert result["year_from"] == 2020
    assert calls[-1]["year_from"] == 2020


def test_search_strips_query_before_use(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_search_papers(monkeypatch, [])

    result = SemanticScholarClient(api_key="").search("   foo bar  ")

    assert result["status"] == "ok"
    assert result["query"] == "foo bar"
    assert calls[-1]["query"] == "foo bar"


def test_search_handles_api_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_search_papers(monkeypatch, exc=RuntimeError("S2 down"))

    result = SemanticScholarClient(api_key="").search("q")

    assert result["status"] == "error"
    assert result["error"] == "S2 down"


def test_search_does_not_require_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """``search()`` is read-only against S2 — no DB plumbing involved.

    The ``database_url`` kwarg was removed from the constructor when the
    persistence-adjacent methods (``research_brief`` / ``archive_paper``)
    were converted to return projection bundles instead of opening their
    own pool. Sentinel test guarding the read-only contract: a client
    instantiated with no DB envvars and no constructor arg still
    services ``search()`` happily.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _install_search_papers(monkeypatch, [_live_paper("p1")])

    client = SemanticScholarClient(api_key="")
    result = client.search("hello")

    assert result["status"] == "ok"
    assert result["count"] == 1


def test_search_returns_raw_dicts_at_agent_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent-boundary contract: ``search()`` returns plain dicts even though
    ``search_papers()`` returns typed :class:`Paper` from the upstream
    library.

    Pinned because the rest of the codebase (CLI ``--json``, Slack tool
    runtime, downstream agents) depends on ``result["results"]`` being a
    JSON-serialisable list of dicts. ``search()`` accomplishes this by
    calling ``paper.raw_data`` on each result.
    """
    papers = [_live_paper("p1")]
    _install_search_papers(monkeypatch, papers)

    result = SemanticScholarClient(api_key="").search("anything")

    assert result["status"] == "ok"
    assert isinstance(result["results"], list)
    assert all(isinstance(item, dict) for item in result["results"])
    assert result["results"][0]["paperId"] == "p1"
