"""Tests for the hybrid ``SemanticScholarClient.search`` method.

The method consults ``company_context_documents`` first for already-saved
Semantic Scholar papers, then tops up via the live S2 API for genuinely
newer results. Every test stubs both asyncpg and the live S2 call — no
network or DB I/O happens here.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest

from semantic_scholar.client import SemanticScholarClient

# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


class MockAsyncpgConn:
    """Minimal stand-in for ``asyncpg.Connection``.

    ``fetch`` records every call and returns the configured rows (or
    raises the configured exception). ``close`` is a no-op coroutine
    that increments a counter so tests can assert cleanup.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        fetch_exc: BaseException | None = None,
    ) -> None:
        self._rows = rows or []
        self._fetch_exc = fetch_exc
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.close_count = 0

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append((sql, args))
        if self._fetch_exc is not None:
            raise self._fetch_exc
        return self._rows

    async def close(self) -> None:
        self.close_count += 1


def _install_mock_conn(
    monkeypatch: pytest.MonkeyPatch,
    mock: MockAsyncpgConn | None,
    *,
    connect_exc: BaseException | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Patch ``asyncpg.connect`` to return ``mock`` (or raise).

    Returns the list of ``(url, kwargs)`` connect invocations so tests
    can assert against them.
    """
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _connect(url: str, **kwargs: Any) -> MockAsyncpgConn:
        calls.append((url, kwargs))
        if connect_exc is not None:
            raise connect_exc
        assert mock is not None
        return mock

    monkeypatch.setattr(asyncpg, "connect", _connect)
    return calls


def _install_database_url(monkeypatch: pytest.MonkeyPatch, url: str = "postgres://test/db") -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    # ``secret`` is read as a fallback; force a deterministic value so
    # the resolution order is observable in the tests below.
    monkeypatch.setattr(
        "semantic_scholar.client.secret",
        lambda _key, default="": default,
    )


def _install_search_papers(
    monkeypatch: pytest.MonkeyPatch,
    papers: list[dict[str, Any]] | None = None,
    *,
    exc: BaseException | None = None,
) -> list[dict[str, Any]]:
    """Patch ``SemanticScholarClient.search_papers_async`` and record its calls.

    ``_search_async`` (the hybrid live top-up path) now calls the async
    sibling ``search_papers_async`` so retry backoff awaits instead of
    blocking the event loop. Patching the sync ``search_papers`` would
    no-op here and let the production code reach the real
    ``httpx.AsyncClient.get``.
    """
    calls: list[dict[str, Any]] = []

    async def _search_papers_async(self, query, limit=10, year_from=None, fields=None):  # type: ignore[no-untyped-def]
        calls.append({"query": query, "limit": limit, "year_from": year_from})
        if exc is not None:
            raise exc
        return list(papers or [])

    monkeypatch.setattr(
        SemanticScholarClient,
        "search_papers_async",
        _search_papers_async,
        raising=True,
    )
    return calls


def _indexed_row(
    *,
    paper_id: str,
    year: int | None = None,
    title: str = "Paper Title",
    body: str = "Some body text mentioning the query terms.",
    score: float = 1.0,
) -> dict[str, Any]:
    """Build a mock DB row shaped like asyncpg returns for the SELECT."""
    metadata: dict[str, Any] = {"paperId": paper_id}
    if year is not None:
        metadata["year"] = year
    return {
        "document_id": f"semantic_scholar:paper:{paper_id}",
        "source": "semantic_scholar",
        "source_type": "paper",
        "source_document_id": paper_id,
        "source_chunk_id": "",
        "parent_document_id": None,
        "title": title,
        "url": f"https://www.semanticscholar.org/paper/{paper_id}",
        "author_name": "First Author",
        "access_scope": "company",
        "body": body,
        "occurred_at": None,
        "source_updated_at": None,
        "metadata": metadata,
        "score": score,
    }


def _live_paper(
    paper_id: str,
    *,
    year: int = 2024,
    title: str | None = None,
) -> dict[str, Any]:
    return {
        "paperId": paper_id,
        "title": title or f"Live Paper {paper_id}",
        "year": year,
        "authors": [{"authorId": "a1", "name": "Live Author"}],
        "abstract": "Live abstract.",
        "url": f"https://www.semanticscholar.org/paper/{paper_id}",
        "citationCount": 7,
        "openAccessPdf": None,
    }


def _client() -> SemanticScholarClient:
    # Pass an explicit empty api_key so ``__init__`` doesn't touch
    # ``secret(...)`` — tests want the BM25 + live wiring, not the
    # network configuration.
    return SemanticScholarClient(api_key="")


# ---------------------------------------------------------------------------
# 1. Validation: empty / whitespace queries
# ---------------------------------------------------------------------------


def test_search_empty_query_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_database_url(monkeypatch)
    connect_calls = _install_mock_conn(monkeypatch, MockAsyncpgConn())

    client = _client()
    assert client.search("") == {"status": "error", "error": "query cannot be empty"}
    assert client.search("   ") == {"status": "error", "error": "query cannot be empty"}
    assert connect_calls == []


# ---------------------------------------------------------------------------
# 2. DATABASE_URL resolution
# ---------------------------------------------------------------------------


def test_search_no_database_url_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "semantic_scholar.client.secret",
        lambda _key, default="": "",
    )
    connect_calls = _install_mock_conn(monkeypatch, MockAsyncpgConn())

    client = _client()
    result = client.search("anything")
    assert result == {
        "status": "error",
        "error": "DATABASE_URL is required for semantic_scholar.search",
    }
    assert connect_calls == []


# ---------------------------------------------------------------------------
# 3. Indexed-only when live returns nothing
# ---------------------------------------------------------------------------


def test_search_indexed_only_when_live_returns_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_database_url(monkeypatch)
    rows = [
        _indexed_row(paper_id="A", year=2023, title="Paper A"),
        _indexed_row(paper_id="B", year=2022, title="Paper B"),
    ]
    mock = MockAsyncpgConn(rows)
    _install_mock_conn(monkeypatch, mock)
    live_calls = _install_search_papers(monkeypatch, [])

    result = _client().search("active inference")

    assert result["status"] == "ok"
    assert result["indexed_count"] == 2
    assert result["live_count"] == 0
    assert result["count"] == 2
    assert result["indexed_cutoff_year"] == 2023
    assert result["live_year_from"] == 2024
    assert [r["paperId"] for r in result["results"]] == ["A", "B"]
    assert all(r["lane"] == "indexed" for r in result["results"])
    # Live API was still called (to give it a chance to top up).
    assert len(live_calls) == 1
    assert live_calls[0]["year_from"] == 2024


# ---------------------------------------------------------------------------
# 4. Live-only when index is empty
# ---------------------------------------------------------------------------


def test_search_live_only_when_index_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn([])
    _install_mock_conn(monkeypatch, mock)
    live_calls = _install_search_papers(
        monkeypatch,
        [_live_paper("X"), _live_paper("Y"), _live_paper("Z")],
    )

    result = _client().search("graph neural networks")

    assert result["status"] == "ok"
    assert result["indexed_count"] == 0
    assert result["live_count"] == 3
    assert result["indexed_cutoff_year"] is None
    assert result["live_year_from"] is None
    assert [r["paperId"] for r in result["results"]] == ["X", "Y", "Z"]
    assert all(r["lane"] == "live" for r in result["results"])
    assert len(live_calls) == 1
    assert live_calls[0]["year_from"] is None


# ---------------------------------------------------------------------------
# 5. Merge order: indexed first, then live
# ---------------------------------------------------------------------------


def test_search_merges_indexed_then_live_in_results_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    rows = [
        _indexed_row(paper_id="A", year=2020),
        _indexed_row(paper_id="B", year=2019),
    ]
    mock = MockAsyncpgConn(rows)
    _install_mock_conn(monkeypatch, mock)
    _install_search_papers(
        monkeypatch,
        [_live_paper("C"), _live_paper("D"), _live_paper("E")],
    )

    result = _client().search("alignment")
    paper_ids = [r["paperId"] for r in result["results"]]
    lanes = [r["lane"] for r in result["results"]]
    assert paper_ids == ["A", "B", "C", "D", "E"]
    assert lanes == ["indexed", "indexed", "live", "live", "live"]


# ---------------------------------------------------------------------------
# 6. Dedupe live against indexed by paperId
# ---------------------------------------------------------------------------


def test_search_dedupes_live_against_indexed_by_paperid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    rows = [_indexed_row(paper_id="A", year=2020)]
    mock = MockAsyncpgConn(rows)
    _install_mock_conn(monkeypatch, mock)
    _install_search_papers(monkeypatch, [_live_paper("A"), _live_paper("C")])

    result = _client().search("dedupe me")
    assert [r["paperId"] for r in result["results"]] == ["A", "C"]
    assert result["results"][0]["lane"] == "indexed"
    assert result["results"][1]["lane"] == "live"
    assert result["live_count"] == 1
    assert result["indexed_count"] == 1
    assert result["count"] == 2


# ---------------------------------------------------------------------------
# 7. Limit clamping
# ---------------------------------------------------------------------------


def test_search_clamps_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn([])
    _install_mock_conn(monkeypatch, mock)
    _install_search_papers(monkeypatch, [])

    result = _client().search("foo", limit=999)
    assert result["limit"] == 50
    # Last positional arg to the SQL is the LIMIT.
    assert mock.fetch_calls, "expected fetch to be called once"
    _sql, args = mock.fetch_calls[0]
    assert args[-1] == 50


# ---------------------------------------------------------------------------
# 8. Cutoff year drives live year_from when caller doesn't supply one
# ---------------------------------------------------------------------------


def test_search_uses_cutoff_year_for_live_when_year_from_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn([_indexed_row(paper_id="A", year=2023)])
    _install_mock_conn(monkeypatch, mock)
    live_calls = _install_search_papers(monkeypatch, [])

    result = _client().search("xyz")
    assert live_calls[-1]["year_from"] == 2024
    assert result["live_year_from"] == 2024


# ---------------------------------------------------------------------------
# 9. Provided year_from beats cutoff when larger
# ---------------------------------------------------------------------------


def test_search_uses_max_of_provided_and_cutoff_year_for_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn([_indexed_row(paper_id="A", year=2023)])
    _install_mock_conn(monkeypatch, mock)
    live_calls = _install_search_papers(monkeypatch, [])

    result = _client().search("xyz", year_from=2025)
    assert live_calls[-1]["year_from"] == 2025
    assert result["live_year_from"] == 2025
    # The original arg is echoed back too, untouched.
    assert result["year_from"] == 2025


# ---------------------------------------------------------------------------
# 10. None year_from with empty index → live gets None
# ---------------------------------------------------------------------------


def test_search_passes_none_year_from_to_live_when_no_cutoff_and_no_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn([])
    _install_mock_conn(monkeypatch, mock)
    live_calls = _install_search_papers(monkeypatch, [])

    result = _client().search("xyz")
    assert live_calls[-1]["year_from"] is None
    assert result["live_year_from"] is None


# ---------------------------------------------------------------------------
# 11. Live failure is captured, indexed results still returned
# ---------------------------------------------------------------------------


def test_search_handles_live_failure_returns_indexed_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    rows = [_indexed_row(paper_id="A", year=2020), _indexed_row(paper_id="B", year=2019)]
    mock = MockAsyncpgConn(rows)
    _install_mock_conn(monkeypatch, mock)
    _install_search_papers(monkeypatch, exc=RuntimeError("S2 down"))

    result = _client().search("q")
    assert result["status"] == "ok"
    assert result["indexed_count"] == 2
    assert result["live_count"] == 0
    assert result["live_error"] == "S2 down"
    assert [r["paperId"] for r in result["results"]] == ["A", "B"]


# ---------------------------------------------------------------------------
# 12. DB failure surfaces as a top-level error dict
# ---------------------------------------------------------------------------


def test_search_handles_db_failure_top_level(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_database_url(monkeypatch)
    _install_mock_conn(
        monkeypatch,
        None,
        connect_exc=RuntimeError("could not connect to database"),
    )
    _install_search_papers(monkeypatch, [])

    result = _client().search("q")
    assert result["status"] == "error"
    assert "could not connect to database" in result["error"]


# ---------------------------------------------------------------------------
# 13. Indexed result shape: score / preview / lane / paperId
# ---------------------------------------------------------------------------


def test_search_indexed_result_includes_score_preview_lane_paperid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    row = _indexed_row(
        paper_id="X1",
        year=2023,
        title="Some Paper Title",
        body="The query terms appear in this body so the preview is non-empty.",
        score=2.5,
    )
    mock = MockAsyncpgConn([row])
    _install_mock_conn(monkeypatch, mock)
    _install_search_papers(monkeypatch, [])

    result = _client().search("query")
    assert result["indexed_count"] == 1
    entry = result["results"][0]
    assert isinstance(entry["score"], float)
    assert entry["score"] == pytest.approx(2.5)
    assert isinstance(entry["preview"], str)
    assert entry["preview"] != ""
    assert entry["lane"] == "indexed"
    assert entry["result_type"] == "paper"
    assert entry["paperId"] == "X1"
    # paperId must equal source_document_id when both happen to match.
    assert entry["source_document_id"] == "X1"


# ---------------------------------------------------------------------------
# 14. Live result shape: lane and score
# ---------------------------------------------------------------------------


def test_search_live_result_has_lane_and_score_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    _install_mock_conn(monkeypatch, MockAsyncpgConn([]))
    _install_search_papers(
        monkeypatch,
        [_live_paper("L1"), _live_paper("L2")],
    )

    result = _client().search("q")
    assert result["live_count"] == 2
    for entry in result["results"]:
        assert entry["lane"] == "live"
        assert entry["score"] is None
        assert entry["result_type"] == "paper"
        assert entry["paperId"] in {"L1", "L2"}


# ---------------------------------------------------------------------------
# 15. Query is stripped before use everywhere
# ---------------------------------------------------------------------------


def test_search_strips_query_before_use(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn([])
    _install_mock_conn(monkeypatch, mock)
    live_calls = _install_search_papers(monkeypatch, [])

    result = _client().search("   foo bar  ")
    assert result["query"] == "foo bar"
    # First bind param is the original query (post-strip).
    _sql, args = mock.fetch_calls[0]
    assert args[0] == "foo bar"
    # Live API also receives the stripped query.
    assert live_calls[-1]["query"] == "foo bar"


# ---------------------------------------------------------------------------
# 16. conn.close is always reached even if fetch raises
# ---------------------------------------------------------------------------


def test_search_calls_close_on_db_error_inside_async(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn([], fetch_exc=RuntimeError("boom"))
    _install_mock_conn(monkeypatch, mock)
    _install_search_papers(monkeypatch, [])

    result = _client().search("q")
    assert result["status"] == "error"
    assert "boom" in result["error"]
    assert mock.close_count == 1


# ---------------------------------------------------------------------------
# 17. Constructor-injected database_url bypasses env + secret resolution
# ---------------------------------------------------------------------------


def test_search_uses_constructor_database_url_over_env_and_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``database_url`` passed to ``__init__`` wins over env + secret.

    Pins the new test seam introduced for review.md A6 — callers (and
    tests) can inject a DSN directly into the constructor instead of
    monkeypatching env vars + ``secret(...)``. The constructor's
    resolution chain is constructor arg → env → secret, so a non-empty
    constructor arg must short-circuit the rest of the chain.
    """
    # Stack the env var and ``secret`` with deliberately-wrong values
    # so the assertion below proves the constructor arg wins, not that
    # the test happens to coincide with whatever env resolves to.
    monkeypatch.setenv("DATABASE_URL", "postgres://from-env/should-not-win")
    monkeypatch.setattr(
        "semantic_scholar.client.secret",
        lambda _key, default="": "postgres://from-secret/should-not-win",
    )
    mock = MockAsyncpgConn([])
    connect_calls = _install_mock_conn(monkeypatch, mock)
    _install_search_papers(monkeypatch, [])

    client = SemanticScholarClient(
        api_key="",
        database_url="postgres://from-constructor/db",
    )
    result = client.search("hello")

    assert result["status"] == "ok"
    assert len(connect_calls) == 1
    assert connect_calls[0][0] == "postgres://from-constructor/db"
    assert connect_calls[0][1] == {"command_timeout": 30}


def test_search_empty_constructor_database_url_falls_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty constructor arg must not block env-var fallback.

    The resolution chain treats an empty string the same as ``None``
    (Python's ``or`` short-circuits on falsy), so a caller passing
    ``database_url=""`` still gets the env DSN. Without this the API
    pod — which constructs the client with no args — would break
    silently the first time a caller explicitly passed ``""``.
    """
    monkeypatch.setenv("DATABASE_URL", "postgres://from-env/wins")
    monkeypatch.setattr(
        "semantic_scholar.client.secret",
        lambda _key, default="": default,
    )
    mock = MockAsyncpgConn([])
    connect_calls = _install_mock_conn(monkeypatch, mock)
    _install_search_papers(monkeypatch, [])

    client = SemanticScholarClient(api_key="", database_url="")
    result = client.search("hello")

    assert result["status"] == "ok"
    assert connect_calls[0][0] == "postgres://from-env/wins"
