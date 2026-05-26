"""Unit tests for ``SemanticScholarClient.research_brief``.

Stubs asyncpg and the S2 search call — no network or DB I/O. Shared
mocks live in ``centaur_lab.testing``. Real-DB persistence (rows
landing, SQL-level idempotency, no-results brief-only path) is covered
by ``tests/integration/test_research_brief_integration.py`` and
intentionally NOT duplicated here.
"""

from __future__ import annotations

from typing import Any

import pytest

from centaur_lab.testing import (
    EXECUTE_ARG_INDEX,
    MockAsyncpgConn,
    install_mock_conn,
)
from semantic_scholar.client import SemanticScholarClient


def _install_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://test/db")
    monkeypatch.setattr(
        "semantic_scholar.client.secret", lambda _k, default="": default, raising=True
    )


def _install_search_papers(
    monkeypatch: pytest.MonkeyPatch,
    papers: list[dict[str, Any]] | None = None,
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


class _MetricsRecorder:
    """Captures ``observe_document_size`` + ``record_document_change`` calls."""

    def __init__(self) -> None:
        self.observe_calls: list[dict[str, Any]] = []
        self.change_calls: list[tuple[dict[str, Any], str]] = []

    def observe(self, document: dict[str, Any]) -> None:
        self.observe_calls.append(document)

    def record(self, document: dict[str, Any], action: str) -> None:
        self.change_calls.append((document, action))


def _install_metrics(monkeypatch: pytest.MonkeyPatch) -> _MetricsRecorder:
    import centaur_lab.brief as brief_module

    recorder = _MetricsRecorder()
    monkeypatch.setattr(brief_module, "observe_document_size", recorder.observe, raising=True)
    monkeypatch.setattr(brief_module, "record_document_change", recorder.record, raising=True)
    return recorder


def _paper(paper_id: str) -> dict[str, Any]:
    return {
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


def _client() -> SemanticScholarClient:
    return SemanticScholarClient(api_key="")


def test_research_brief_empty_query_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace-only query short-circuits before any I/O."""
    _install_database_url(monkeypatch)
    connect_calls = install_mock_conn(monkeypatch, MockAsyncpgConn())
    search_calls = _install_search_papers(monkeypatch, [])

    result = _client().research_brief("   ")

    assert result == {"status": "error", "error": "query cannot be empty"}
    assert connect_calls == []
    assert search_calls == []


def test_research_brief_non_positive_limit_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """``limit <= 0`` short-circuits before any I/O."""
    _install_database_url(monkeypatch)
    connect_calls = install_mock_conn(monkeypatch, MockAsyncpgConn())
    search_calls = _install_search_papers(monkeypatch, [])

    result = _client().research_brief("anything", limit=0)

    assert result == {"status": "error", "error": "limit must be positive"}
    assert connect_calls == []
    assert search_calls == []


def test_research_brief_no_database_url_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing ``DATABASE_URL`` returns the env error envelope and skips I/O."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("semantic_scholar.client.secret", lambda _key, default="": "", raising=True)
    connect_calls = install_mock_conn(monkeypatch, MockAsyncpgConn())
    search_calls = _install_search_papers(monkeypatch, [])

    result = _client().research_brief("anything")

    assert result == {
        "status": "error",
        "error": "DATABASE_URL is required for semantic_scholar.research_brief",
    }
    assert connect_calls == []
    assert search_calls == []


def test_research_brief_clamps_limit_above_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """``limit`` is clamped to ``MAX_RESEARCH_BRIEF_LIMIT`` before reaching S2.

    The workflow's input schema bounds limit to 1..100, but the client clamps
    further to 1..``MAX_RESEARCH_BRIEF_LIMIT`` (currently 20) to keep the
    S2 fan-out and per-brief LLM/index budget bounded. Asserts the *clamped*
    value reaches ``search_papers``, not the raw user input.
    """
    from semantic_scholar.client import MAX_RESEARCH_BRIEF_LIMIT

    _install_database_url(monkeypatch)
    install_mock_conn(monkeypatch, MockAsyncpgConn())
    search_calls = _install_search_papers(monkeypatch, [])

    result = _client().research_brief("anything", limit=100)

    assert result["status"] == "completed"
    assert search_calls == [
        {"query": "anything", "limit": MAX_RESEARCH_BRIEF_LIMIT, "year_from": None}
    ]


def test_research_brief_persists_brief_and_papers_with_parent_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Success path: brief upserts, every paper upserts with parent_document_id linked."""
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn()
    install_mock_conn(monkeypatch, mock)
    papers = [_paper("p1"), _paper("p2"), _paper("p3")]
    search_calls = _install_search_papers(monkeypatch, papers)

    result = _client().research_brief("active inference", limit=3, year_from=2020)

    assert result["status"] == "completed"
    brief_document_id = result["brief_document_id"]
    assert brief_document_id

    assert search_calls == [{"query": "active inference", "limit": 3, "year_from": 2020}]

    # 1 brief + 3 paper upserts = 4 execute calls.
    assert len(mock.execute_calls) == 4
    assert mock.execute_calls[0][1][EXECUTE_ARG_INDEX["document_id"]] == brief_document_id
    parent_idx = EXECUTE_ARG_INDEX["parent_document_id"]
    for paper_call in mock.execute_calls[1:]:
        assert paper_call[1][parent_idx] == brief_document_id

    total = result["papers_inserted"] + result["papers_updated"] + result["papers_noop"]
    assert total == len(papers)
    assert mock.close_count == 1


def test_research_brief_search_failure_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """S2 failure short-circuits before opening a DB connection — no metrics, no writes."""
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn()
    connect_calls = install_mock_conn(monkeypatch, mock)
    _install_search_papers(monkeypatch, exc=RuntimeError("S2 down"))
    recorder = _install_metrics(monkeypatch)

    result = _client().research_brief("anything")

    assert result == {"status": "error", "error": "S2 down"}
    # _research_brief_async runs the S2 search BEFORE asyncpg.connect; when
    # it raises, no connection is opened and no metrics are emitted.
    assert connect_calls == []
    assert mock.execute_calls == []
    assert recorder.observe_calls == []
    assert recorder.change_calls == []


def test_research_brief_idempotent_rerun_returns_all_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running with identical inputs yields ``brief_action='noop'`` and every paper noop."""
    _install_database_url(monkeypatch)
    papers = [_paper("p1"), _paper("p2")]

    # Pass 1: capture the document_id → effective content_hash map the
    # production code writes. Without a real DB to read back from, we
    # mine execute_calls and feed those hashes to a second mock so the
    # upsert short-circuits on rerun.
    discover_mock = MockAsyncpgConn()
    install_mock_conn(monkeypatch, discover_mock)
    _install_search_papers(monkeypatch, papers)
    first = _client().research_brief("active inference")
    assert first["status"] == "completed"
    assert first["brief_action"] == "inserted"

    hashes: dict[str, str] = {
        str(args[EXECUTE_ARG_INDEX["document_id"]]): str(args[EXECUTE_ARG_INDEX["content_hash"]])
        for _sql, args in discover_mock.execute_calls
    }

    # Pass 2: preload fetchval so every upsert sees a matching existing
    # hash and returns "noop" without ever calling execute.
    rerun_mock = MockAsyncpgConn(fetchval_for_doc_id=hashes)
    install_mock_conn(monkeypatch, rerun_mock)
    _install_search_papers(monkeypatch, papers)
    second = _client().research_brief("active inference")

    assert second["status"] == "completed"
    assert second["brief_action"] == "noop"
    assert second["papers_noop"] == len(papers)
    assert second["papers_inserted"] == 0
    assert second["papers_updated"] == 0
    assert rerun_mock.execute_calls == []


def test_research_brief_emits_metrics_for_brief_and_papers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``observe_document_size`` + ``record_document_change`` fire once per upserted doc."""
    _install_database_url(monkeypatch)
    install_mock_conn(monkeypatch, MockAsyncpgConn())
    papers = [_paper("p1"), _paper("p2")]
    _install_search_papers(monkeypatch, papers)
    recorder = _install_metrics(monkeypatch)

    result = _client().research_brief("active inference")

    assert result["status"] == "completed"
    # 1 brief + 2 papers = 3 of each metric call, brief recorded first.
    assert len(recorder.observe_calls) == 3
    assert len(recorder.change_calls) == 3
    source_types = [doc["source_type"] for doc, _action in recorder.change_calls]
    assert source_types == ["research_brief", "paper", "paper"]
