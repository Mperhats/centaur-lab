"""Shared test mocks for the overlay workflow test suite.

Test-internal helpers — the leading underscore mirrors the workflow loader's
``startswith("_")`` skip convention and signals that this module is not a
workflow handler. Imported by sibling test modules
(``test_paper_document.py``, ``test_save_papers.py``,
``test_research_brief.py``, and the integration suite under ``integration/``)
so the same asyncpg pool mock, workflow context mock, and UPSERT
argument-position map have a single source of truth.

Doubles for third-party clients (e.g. ``SemanticScholarClient``) live inline
next to the test that uses them — see upstream
``.centaur/services/api/tests/test_company_context_documents.py`` for the
self-contained convention.
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
    """Async-pool mock recording fetchval/fetchrow/execute calls for assertions.

    Matches the surface of the asyncpg pool that
    ``_paper_document.upsert_document`` actually consumes — a single
    ``fetchval`` (existing content hash lookup) followed by an ``execute``
    (the UPSERT). ``fetchrow`` is also recorded for the
    ``_bfts_hyperparams.latest_hyperparams`` DAO. All calls are appended to
    public ``*_calls`` lists so tests can assert on positional arguments via
    ``EXECUTE_ARG_INDEX``.
    """

    def __init__(
        self,
        *,
        existing_hash: str | None = None,
        execute_status: str = "INSERT 0 1",
        fetchrow_result: dict[str, Any] | None = None,
    ) -> None:
        self._existing_hash = existing_hash
        self._execute_status = execute_status
        self._fetchrow_result = fetchrow_result
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, query: str, *args: Any) -> str | None:
        self.fetchval_calls.append((query, args))
        return self._existing_hash

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.fetchrow_calls.append((query, args))
        return self._fetchrow_result

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
