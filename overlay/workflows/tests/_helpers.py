"""Unit-test mocks for the workflows suite.

``MockAsyncpgConn``, ``MockPool``, ``MockContext``, ``install_mock_conn``,
and ``EXECUTE_ARG_INDEX`` stand in for asyncpg connections/pools and the
workflow context so unit tests can drive each workflow's inlined
``_upsert_document`` SQL without touching a real database.

A near-identical copy lives at
``overlay/tools/semantic_scholar/tests/_helpers.py``; the duplication is
intentional — each tree owns its own test helpers so a refactor in one
test suite can't silently break the other. When that drift becomes a
problem (it hasn't yet), extract a shared ``overlay/_test_helpers``
package; until then the inline-helpers convention from upstream
workflows applies to tests too.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest

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
"""Positional-arg index for ``_upsert_document``'s UPSERT statement.

Tests assert on individual columns by name via ``args[EXECUTE_ARG_INDEX[
"parent_document_id"]]`` rather than threading the column order through
every assertion — when the SQL changes, this map is the single thing
that needs updating.
"""


class MockAsyncpgConn:
    """Minimal stand-in for ``asyncpg.Connection``.

    ``fetchval_for_doc_id`` maps document_ids to the "existing"
    ``content_hash`` returned by the SELECT inside ``_upsert_document``;
    absent keys return ``None`` (i.e. the row does not yet exist, so the
    upsert is an INSERT). ``execute_status`` is the command tag returned
    by the UPSERT — ``"INSERT 0 1"`` covers both insert and update paths
    since ``_upsert_document`` only checks ``status.endswith(" 1")``.
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
        doc_id = args[0] if args else None
        return self._fetchval_for_doc_id.get(str(doc_id))

    async def execute(self, sql: str, *args: Any) -> str:
        self.execute_calls.append((sql, args))
        if self._execute_exc is not None:
            raise self._execute_exc
        return self._execute_status

    async def close(self) -> None:
        self.close_count += 1


def install_mock_conn(
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


class MockPool:
    """Async-pool mock recording fetchval/execute calls for assertions.

    Matches the surface of the asyncpg pool that the inlined
    ``_upsert_document`` actually consumes — a single ``fetchval``
    (existing content hash lookup) followed by an ``execute`` (the
    UPSERT). Both calls are appended to public ``*_calls`` lists so
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

    ``pool`` is intentionally typed ``Any`` so the same mock can wrap
    either the in-memory :class:`MockPool` (unit tests) or a real
    ``asyncpg.Pool`` (integration tests under ``tests/integration/``).
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self.logs: list[tuple[str, dict[str, Any]]] = []

    def log(self, event: str, **kwargs: Any) -> None:
        self.logs.append((event, kwargs))
