"""Tests for the ``SemanticScholarClient.research_brief`` tool method.

The method searches Semantic Scholar, renders a Markdown brief, and
persists both the brief and each underlying paper into
``company_context_documents``. Every test stubs both asyncpg and the
S2 search call — no network or DB I/O happens here. The mocks live
inline (not in a shared module) so this file stays a drop-in template
for future "do-the-whole-thing" tool methods that wrap an async
helper around a fresh asyncpg connection.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest

from semantic_scholar import client as s2_client
from semantic_scholar.client import SemanticScholarClient
from shared.paper_document import _content_hash

# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


class MockAsyncpgConn:
    """Minimal stand-in for ``asyncpg.Connection``.

    Extends the ``test_search_hybrid`` flavor with ``fetchval`` and
    ``execute`` so ``upsert_document`` can drive a complete
    insert/update/noop cycle without touching a real database.

    ``fetchval_for_doc_id`` maps document_ids to the "existing"
    ``content_hash`` returned by the SELECT inside ``upsert_document``;
    absent keys return ``None`` (i.e. the row does not yet exist, so the
    upsert is an INSERT). ``execute_status`` is the command tag returned
    by the UPSERT — ``"INSERT 0 1"`` covers both insert and update paths
    since ``upsert_document`` only checks ``status.endswith(" 1")``.
    """

    def __init__(
        self,
        *,
        fetchval_for_doc_id: dict[str, str | None] | None = None,
        execute_status: str = "INSERT 0 1",
        fetch_rows: list[dict[str, Any]] | None = None,
        fetch_exc: BaseException | None = None,
        fetchval_exc: BaseException | None = None,
        execute_exc: BaseException | None = None,
    ) -> None:
        self._fetchval_for_doc_id = dict(fetchval_for_doc_id or {})
        self._execute_status = execute_status
        self._fetch_rows = fetch_rows or []
        self._fetch_exc = fetch_exc
        self._fetchval_exc = fetchval_exc
        self._execute_exc = execute_exc
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.close_count = 0

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append((sql, args))
        if self._fetch_exc is not None:
            raise self._fetch_exc
        return self._fetch_rows

    async def fetchval(self, sql: str, *args: Any) -> str | None:
        self.fetchval_calls.append((sql, args))
        if self._fetchval_exc is not None:
            raise self._fetchval_exc
        # upsert_document calls fetchval(sql, document_id), so args[0] is
        # the document_id we look up in the configured map.
        doc_id = args[0] if args else None
        return self._fetchval_for_doc_id.get(str(doc_id))

    async def execute(self, sql: str, *args: Any) -> str:
        self.execute_calls.append((sql, args))
        if self._execute_exc is not None:
            raise self._execute_exc
        return self._execute_status

    async def close(self) -> None:
        self.close_count += 1


def _install_mock_conn(
    monkeypatch: pytest.MonkeyPatch,
    mock: MockAsyncpgConn | None,
    *,
    connect_exc: BaseException | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Patch ``asyncpg.connect`` to return ``mock`` (or raise)."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _connect(url: str, **kwargs: Any) -> MockAsyncpgConn:
        calls.append((url, kwargs))
        if connect_exc is not None:
            raise connect_exc
        assert mock is not None
        return mock

    monkeypatch.setattr(asyncpg, "connect", _connect)
    return calls


def _install_database_url(
    monkeypatch: pytest.MonkeyPatch,
    url: str = "postgres://test/db",
) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
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

    ``_research_brief_async`` now calls the async sibling
    ``search_papers_async`` so retry backoff awaits instead of blocking
    the event loop. Patching the sync ``search_papers`` would no-op here
    and let the production code reach the real ``httpx.AsyncClient.get``.
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


class MetricsRecorder:
    """Lightweight stand-in for ``emit_document_metrics`` used by tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], str]] = []

    def __call__(self, document: dict[str, Any], action: str) -> None:
        self.calls.append((document, action))


def _install_metrics(monkeypatch: pytest.MonkeyPatch) -> MetricsRecorder:
    recorder = MetricsRecorder()
    monkeypatch.setattr(s2_client, "emit_document_metrics", recorder)
    return recorder


def _paper(paper_id: str, *, title: str | None = None, year: int = 2024) -> dict[str, Any]:
    return {
        "paperId": paper_id,
        "title": title or f"Paper {paper_id}",
        "authors": [{"authorId": f"a-{paper_id}", "name": f"Author {paper_id}"}],
        "year": year,
        "abstract": f"Abstract for {paper_id}.",
        "citationCount": 7,
        "url": f"https://www.semanticscholar.org/paper/{paper_id}",
        "openAccessPdf": None,
        "venue": "Test Venue",
        "externalIds": {"DOI": f"10.0/{paper_id}"},
    }


def _client() -> SemanticScholarClient:
    # Pass an explicit empty api_key so ``__init__`` doesn't read secrets.
    return SemanticScholarClient(api_key="")


# upsert_document's SQL binds (document_id, source, source_type,
# source_document_id, source_chunk_id, parent_document_id, ...).
# Mirrors EXECUTE_ARG_INDEX in overlay/workflows/tests/_mocks.py.
_EXECUTE_ARG_INDEX: dict[str, int] = {
    "document_id": 0,
    "source": 1,
    "source_type": 2,
    "source_document_id": 3,
    "source_chunk_id": 4,
    "parent_document_id": 5,
    "title": 6,
    "body": 7,
    "url": 8,
    "author_id": 9,
    "author_name": 10,
    "access_scope": 11,
    "occurred_at": 12,
    "source_updated_at": 13,
    "content_hash": 14,
    "metadata": 15,
}


# ---------------------------------------------------------------------------
# 1. Validation: empty / whitespace query → error envelope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_query", ["", "   ", "\t\n  "])
def test_research_brief_empty_query_returns_error(
    monkeypatch: pytest.MonkeyPatch, bad_query: str
) -> None:
    _install_database_url(monkeypatch)
    connect_calls = _install_mock_conn(monkeypatch, MockAsyncpgConn())
    search_calls = _install_search_papers(monkeypatch, [])

    result = _client().research_brief(bad_query)
    assert result == {"status": "error", "error": "query cannot be empty"}
    assert connect_calls == []
    assert search_calls == []


# ---------------------------------------------------------------------------
# 2. Validation: non-positive limit → error envelope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_limit", [0, -1, -5])
def test_research_brief_non_positive_limit_returns_error(
    monkeypatch: pytest.MonkeyPatch, bad_limit: int
) -> None:
    _install_database_url(monkeypatch)
    connect_calls = _install_mock_conn(monkeypatch, MockAsyncpgConn())
    search_calls = _install_search_papers(monkeypatch, [])

    result = _client().research_brief("anything", limit=bad_limit)
    assert result == {"status": "error", "error": "limit must be positive"}
    assert connect_calls == []
    assert search_calls == []


# ---------------------------------------------------------------------------
# 3. DATABASE_URL missing → error envelope
# ---------------------------------------------------------------------------


def test_research_brief_no_database_url_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "semantic_scholar.client.secret",
        lambda _key, default="": "",
    )
    connect_calls = _install_mock_conn(monkeypatch, MockAsyncpgConn())
    search_calls = _install_search_papers(monkeypatch, [])

    result = _client().research_brief("anything")
    assert result == {
        "status": "error",
        "error": "DATABASE_URL is required for semantic_scholar.research_brief",
    }
    assert connect_calls == []
    assert search_calls == []


# ---------------------------------------------------------------------------
# 4. Limit clamping (above MAX) — proceeds with clamped value
# ---------------------------------------------------------------------------


def test_research_brief_clamps_limit_above_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn()
    _install_mock_conn(monkeypatch, mock)
    search_calls = _install_search_papers(monkeypatch, [])
    _install_metrics(monkeypatch)

    result = _client().research_brief("active inference", limit=999)
    assert result["status"] == "completed"
    assert len(search_calls) == 1
    assert search_calls[0]["limit"] == s2_client.MAX_RESEARCH_BRIEF_LIMIT
    assert search_calls[0]["limit"] == 20


# ---------------------------------------------------------------------------
# 5. S2 RuntimeError → error envelope, no DB writes
# ---------------------------------------------------------------------------


def test_research_brief_search_failure_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn()
    connect_calls = _install_mock_conn(monkeypatch, mock)
    _install_search_papers(monkeypatch, exc=RuntimeError("S2 down"))
    recorder = _install_metrics(monkeypatch)

    result = _client().research_brief("anything")
    assert result == {"status": "error", "error": "S2 down"}
    # The async helper now does the S2 search BEFORE opening a DB
    # connection (see _research_brief_async). When the search raises,
    # asyncpg.connect is never reached — so no connection is opened
    # and the close branch never fires.
    assert connect_calls == []
    assert mock.close_count == 0
    assert mock.fetchval_calls == []
    assert mock.execute_calls == []
    assert recorder.calls == []


# ---------------------------------------------------------------------------
# 6. No results → brief still upserts, papers counters all zero
# ---------------------------------------------------------------------------


def test_research_brief_no_results_persists_brief_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn()
    _install_mock_conn(monkeypatch, mock)
    _install_search_papers(monkeypatch, [])
    _install_metrics(monkeypatch)

    result = _client().research_brief("quantum gravity")

    assert result["status"] == "completed"
    assert result["results_count"] == 0
    assert result["papers_inserted"] == 0
    assert result["papers_updated"] == 0
    assert result["papers_noop"] == 0
    assert result["brief_action"] == "inserted"
    assert result["brief_document_id"].startswith("semantic_scholar:research_brief:")
    assert "No papers found for this query." in result["markdown"]
    # Exactly one upsert pair (brief only).
    assert len(mock.fetchval_calls) == 1
    assert len(mock.execute_calls) == 1
    assert mock.close_count == 1


# ---------------------------------------------------------------------------
# 7. Successful flow: brief + each paper upserts with parent linkage
# ---------------------------------------------------------------------------


def test_research_brief_persists_brief_and_papers_with_parent_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn()
    _install_mock_conn(monkeypatch, mock)
    _install_search_papers(monkeypatch, [_paper("p1"), _paper("p2"), _paper("p3")])
    _install_metrics(monkeypatch)

    result = _client().research_brief("active inference")

    assert result["status"] == "completed"
    assert result["results_count"] == 3
    assert result["papers_inserted"] == 3
    assert result["papers_updated"] == 0
    assert result["papers_noop"] == 0
    assert result["brief_action"] == "inserted"

    # 1 brief upsert + 3 paper upserts = 4 execute calls.
    assert len(mock.execute_calls) == 4
    brief_call = mock.execute_calls[0]
    brief_document_id = brief_call[1][_EXECUTE_ARG_INDEX["document_id"]]
    assert brief_document_id == result["brief_document_id"]

    parent_idx = _EXECUTE_ARG_INDEX["parent_document_id"]
    for paper_call in mock.execute_calls[1:]:
        assert paper_call[1][parent_idx] == brief_document_id


# ---------------------------------------------------------------------------
# 8. Idempotent rerun: brief + all papers noop when content_hash matches
# ---------------------------------------------------------------------------


def test_research_brief_idempotent_rerun_returns_all_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-running on identical inputs returns ``"noop"`` everywhere.

    To simulate "already in the DB" we precompute what
    ``upsert_document`` would write as ``content_hash`` for the brief
    and for each paper (folding in the effective parent), then preload
    the mock's fetchval map. The upsert then short-circuits before
    calling ``execute``.
    """
    _install_database_url(monkeypatch)
    _install_metrics(monkeypatch)

    papers = [_paper("p1"), _paper("p2")]
    # First, run once to capture the document_ids and intrinsic content
    # hashes the production code will compute. We do this by patching
    # search_papers and reading the actual execute args off the mock.
    discover_mock = MockAsyncpgConn()
    _install_mock_conn(monkeypatch, discover_mock)
    _install_search_papers(monkeypatch, papers)

    first = _client().research_brief("active inference")
    assert first["status"] == "completed"
    assert first["brief_action"] == "inserted"

    # Build a map of document_id -> effective_hash from the first run's
    # execute calls. The 14th positional arg (content_hash index) is the
    # effective hash already (upsert_document folds in the parent).
    hashes: dict[str, str] = {}
    for _sql, args in discover_mock.execute_calls:
        doc_id = str(args[_EXECUTE_ARG_INDEX["document_id"]])
        hashes[doc_id] = str(args[_EXECUTE_ARG_INDEX["content_hash"]])

    # Now run a second time with fetchval preloaded with those hashes —
    # every upsert should short-circuit to "noop" before reaching
    # execute.
    rerun_mock = MockAsyncpgConn(fetchval_for_doc_id=hashes)
    _install_mock_conn(monkeypatch, rerun_mock)
    _install_search_papers(monkeypatch, papers)

    second = _client().research_brief("active inference")
    assert second["status"] == "completed"
    assert second["brief_action"] == "noop"
    assert second["papers_inserted"] == 0
    assert second["papers_updated"] == 0
    assert second["papers_noop"] == 2
    assert second["results_count"] == 2
    # No row was actually written on the rerun.
    assert rerun_mock.execute_calls == []
    # But we still hit fetchval once per document (brief + 2 papers).
    assert len(rerun_mock.fetchval_calls) == 3


# ---------------------------------------------------------------------------
# 9. Mixed insert/update/noop: counters accurate
# ---------------------------------------------------------------------------


def test_research_brief_counts_mixed_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One paper exists with matching hash (noop), one exists with stale hash
    (update), one is new (insert). Brief is also new."""
    _install_database_url(monkeypatch)
    _install_metrics(monkeypatch)

    papers = [_paper("noop_p"), _paper("stale_p"), _paper("new_p")]
    # Discover the effective hash of "noop_p" by running once with empty
    # fetchval map, then plucking the content_hash arg.
    discover_mock = MockAsyncpgConn()
    _install_mock_conn(monkeypatch, discover_mock)
    _install_search_papers(monkeypatch, papers)
    _client().research_brief("topic")
    noop_doc_id = "semantic_scholar:paper:noop_p"
    noop_hash = ""
    for _sql, args in discover_mock.execute_calls:
        if str(args[_EXECUTE_ARG_INDEX["document_id"]]) == noop_doc_id:
            noop_hash = str(args[_EXECUTE_ARG_INDEX["content_hash"]])
            break
    assert noop_hash, "expected to capture noop_p's effective content_hash"

    # Re-run with a fetchval map that returns the matching hash for
    # noop_p (→ noop) and a different "old" hash for stale_p (→ update).
    rerun_mock = MockAsyncpgConn(
        fetchval_for_doc_id={
            noop_doc_id: noop_hash,
            "semantic_scholar:paper:stale_p": "old_hash_from_before",
        }
    )
    _install_mock_conn(monkeypatch, rerun_mock)
    _install_search_papers(monkeypatch, papers)

    result = _client().research_brief("topic")
    assert result["status"] == "completed"
    assert result["results_count"] == 3
    # 1 noop, 1 update, 1 insert across the papers.
    assert result["papers_noop"] == 1
    assert result["papers_updated"] == 1
    assert result["papers_inserted"] == 1


# ---------------------------------------------------------------------------
# 10. Paper without paperId is skipped (build_paper_document raises ValueError)
# ---------------------------------------------------------------------------


def test_research_brief_skips_paper_without_paper_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn()
    _install_mock_conn(monkeypatch, mock)

    invalid_paper: dict[str, Any] = {
        "title": "Missing ID Paper",
        "authors": [],
        "year": 2024,
    }
    _install_search_papers(monkeypatch, [_paper("ok1"), invalid_paper, _paper("ok2")])
    _install_metrics(monkeypatch)

    result = _client().research_brief("topic")

    assert result["status"] == "completed"
    # 3 papers requested but only 2 are upsertable.
    assert result["results_count"] == 3
    assert result["papers_inserted"] == 2
    # 1 brief + 2 paper upserts → 3 execute calls.
    assert len(mock.execute_calls) == 3


# ---------------------------------------------------------------------------
# 11. Metrics emitted for the brief and every successfully-upserted paper
# ---------------------------------------------------------------------------


def test_research_brief_emits_metrics_for_brief_and_papers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn()
    _install_mock_conn(monkeypatch, mock)
    _install_search_papers(monkeypatch, [_paper("p1"), _paper("p2")])
    recorder = _install_metrics(monkeypatch)

    result = _client().research_brief("active inference")
    assert result["status"] == "completed"
    assert len(recorder.calls) == 3
    source_types = [doc["source_type"] for doc, _action in recorder.calls]
    assert source_types.count("research_brief") == 1
    assert source_types.count("paper") == 2
    # The brief is recorded first (before the papers).
    assert recorder.calls[0][0]["source_type"] == "research_brief"


# ---------------------------------------------------------------------------
# 12. Metrics emitted even on a no-results brief
# ---------------------------------------------------------------------------


def test_research_brief_emits_metrics_on_no_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn()
    _install_mock_conn(monkeypatch, mock)
    _install_search_papers(monkeypatch, [])
    recorder = _install_metrics(monkeypatch)

    result = _client().research_brief("quantum gravity")
    assert result["status"] == "completed"
    assert len(recorder.calls) == 1
    assert recorder.calls[0][0]["source_type"] == "research_brief"
    assert recorder.calls[0][1] == "inserted"


# ---------------------------------------------------------------------------
# 13. Brief document_id is stable & case-insensitive across reruns
# ---------------------------------------------------------------------------


def test_research_brief_brief_id_stable_for_same_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    _install_metrics(monkeypatch)

    mock_a = MockAsyncpgConn()
    _install_mock_conn(monkeypatch, mock_a)
    _install_search_papers(monkeypatch, [])
    first = _client().research_brief("Active Inference World Models", year_from=2023)

    mock_b = MockAsyncpgConn()
    _install_mock_conn(monkeypatch, mock_b)
    _install_search_papers(monkeypatch, [])
    second = _client().research_brief("active inference world models", year_from=2023)

    assert first["brief_document_id"] == second["brief_document_id"]

    # Changing year_from changes the brief id.
    mock_c = MockAsyncpgConn()
    _install_mock_conn(monkeypatch, mock_c)
    _install_search_papers(monkeypatch, [])
    third = _client().research_brief("active inference world models", year_from=2020)
    assert first["brief_document_id"] != third["brief_document_id"]


# ---------------------------------------------------------------------------
# 14. DB connection failure → error envelope
# ---------------------------------------------------------------------------


def test_research_brief_db_connection_failure_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    connect_calls = _install_mock_conn(
        monkeypatch,
        None,
        connect_exc=RuntimeError("could not connect to database"),
    )
    search_calls = _install_search_papers(monkeypatch, [_paper("p1")])
    recorder = _install_metrics(monkeypatch)

    result = _client().research_brief("anything", year_from=2022)
    assert result["status"] == "error"
    assert "could not connect to database" in result["error"]
    # The S2 search runs successfully *before* the connect attempt;
    # the connect failure is what surfaces in the error envelope.
    assert len(search_calls) == 1
    assert search_calls[0]["query"] == "anything"
    assert search_calls[0]["year_from"] == 2022
    # And we still attempted to open exactly one connection.
    assert len(connect_calls) == 1
    assert recorder.calls == []


# ---------------------------------------------------------------------------
# 15. Close is always reached even when fetchval raises mid-flight
# ---------------------------------------------------------------------------


def test_research_brief_close_on_db_error_inside_async(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn(fetchval_exc=RuntimeError("boom"))
    _install_mock_conn(monkeypatch, mock)
    _install_search_papers(monkeypatch, [_paper("p1")])
    _install_metrics(monkeypatch)

    result = _client().research_brief("anything")
    assert result["status"] == "error"
    assert "boom" in result["error"]
    assert mock.close_count == 1


# ---------------------------------------------------------------------------
# 16. Markdown round-trips through the success response unchanged
# ---------------------------------------------------------------------------


def test_research_brief_markdown_contains_query_and_papers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_database_url(monkeypatch)
    mock = MockAsyncpgConn()
    _install_mock_conn(monkeypatch, mock)
    _install_search_papers(
        monkeypatch,
        [_paper("p1", title="First Title"), _paper("p2", title="Second Title")],
    )
    _install_metrics(monkeypatch)

    result = _client().research_brief("retrieval augmented generation", year_from=2022)
    md = result["markdown"]
    assert "# Research Brief: retrieval augmented generation" in md
    assert "First Title" in md
    assert "Second Title" in md
    assert "Year filter: 2022" in md


# ---------------------------------------------------------------------------
# 17. Sanity: _content_hash import from shared resolves (regression guard)
# ---------------------------------------------------------------------------


def test_shared_paper_document_import_resolves() -> None:
    """If this fails, conftest didn't put overlay/ on sys.path."""
    assert callable(_content_hash)
