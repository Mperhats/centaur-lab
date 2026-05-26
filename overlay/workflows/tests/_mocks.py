"""Shared test mocks for the overlay workflow test suite.

Test-internal helpers — the leading underscore mirrors the workflow loader's
``startswith("_")`` skip convention and signals that this module is not a
workflow handler. Imported by sibling test modules
(``test_paper_document.py``, ``test_save_papers.py``, etc.) so the same
asyncpg pool mock and UPSERT argument-position map have a single source of
truth.
"""

from __future__ import annotations

from typing import Any

EXECUTE_ARG_INDEX: dict[str, int] = {
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


class MockPool:
    """Async-pool mock recording fetchval/execute calls for assertions.

    Matches the surface of the asyncpg pool that
    ``_paper_document.upsert_document`` actually consumes — a single
    ``fetchval`` (existing content hash lookup) followed by an ``execute``
    (the UPSERT). Both calls are appended to public ``*_calls`` lists so
    tests can assert on positional arguments via ``EXECUTE_ARG_INDEX``.
    """

    def __init__(
        self,
        *,
        existing_hash: str | None = None,
        execute_status: str = "INSERT 0 1",
    ) -> None:
        self._existing_hash = existing_hash
        self._execute_status = execute_status
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, query: str, *args: Any) -> str | None:
        self.fetchval_calls.append((query, args))
        return self._existing_hash

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append((query, args))
        return self._execute_status


class MockContext:
    """Minimal workflow context exposing ``_pool`` and a recording ``log``.

    ``pool`` is intentionally typed ``Any`` so the same mock can wrap either
    the in-memory ``MockPool`` (unit tests) or a real ``asyncpg.Pool``
    (integration tests under ``tests/integration/``).
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self.logs: list[tuple[str, dict[str, Any]]] = []

    def log(self, event: str, **kwargs: Any) -> None:
        self.logs.append((event, kwargs))


class MetricsRecorder:
    """Lightweight stand-in for ``emit_document_metrics`` used by tests.

    Records each call as ``(document, action)`` so tests can assert call
    counts and argument shape without mocking the real Prometheus
    machinery (which isn't on sys.path during local runs anyway).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], str]] = []

    def __call__(self, document: dict[str, Any], action: str) -> None:
        self.calls.append((document, action))


class MockSemanticScholarClient:
    """Mock of ``SemanticScholarClient`` supporting both lookup paths.

    Real ``SemanticScholarClient`` exposes ``get_paper`` (used by
    ``save_papers``) and ``search_papers`` (used by ``research_brief``);
    this mock mirrors both so a single class can back integration tests
    for either workflow. Configure whichever path the test exercises:
    ``papers_by_id`` for ``get_paper`` calls, ``search_results`` for
    ``search_papers`` calls. Calling an unconfigured path raises
    ``RuntimeError`` so misuse fails loudly.
    """

    def __init__(
        self,
        *,
        papers_by_id: dict[str, dict[str, Any]] | None = None,
        search_results: list[dict[str, Any]] | None = None,
    ) -> None:
        self._papers_by_id = papers_by_id or {}
        self._search_results = search_results

    def get_paper(
        self, paper_id: str, fields: Any = None
    ) -> dict[str, Any]:
        if paper_id not in self._papers_by_id:
            raise RuntimeError(f"unknown paper id in mock: {paper_id}")
        return dict(self._papers_by_id[paper_id])

    def search_papers(
        self,
        query: str,
        limit: int = 10,
        year_from: int | None = None,
        fields: str | None = None,
    ) -> list[dict[str, Any]]:
        if self._search_results is None:
            raise RuntimeError(
                "MockSemanticScholarClient: search_results not configured"
            )
        return [dict(p) for p in self._search_results]

    def close(self) -> None:
        pass
