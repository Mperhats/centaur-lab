"""Shared test plugin and unit-test mocks for the overlay suites.

This module wears two hats:

1. **Integration pytest plugin.** Both
   ``overlay/tools/semantic_scholar/tests/integration/conftest.py`` and
   ``overlay/workflows/tests/integration/conftest.py`` enable this module
   via ``pytest_plugins = ["centaur_lab.testing"]`` so the DSN re-basing,
   ``CREATE DATABASE`` guard, dbmate migration apply pass, and per-test
   ``TRUNCATE`` are defined once and stay in sync (review finding A10).
2. **Shared unit-test mocks.** ``MockAsyncpgConn``, ``MockPool``,
   ``MockContext``, ``install_mock_conn``, and ``EXECUTE_ARG_INDEX``
   stand in for asyncpg connections/pools and the workflow context. The
   workflow tests and the semantic_scholar tool tests both consume the
   same upsert SQL, so consolidating these mocks here is the single
   source of truth that keeps the two trees from drifting.

The ``centaur_lab`` package sits at ``overlay/centaur_lab/`` and is on
``pythonpath`` for both test suites via their respective ``pyproject.toml``
``[tool.pytest.ini_options]`` blocks, so ``from centaur_lab.testing
import X`` resolves identically from either tree. ``pytest_plugins``
itself only triggers under the ``tests/integration/`` scope, so the
unit-test helpers below are plain importable symbols and don't activate
the integration fixtures for unit-only runs.

Mirrors ``.centaur/services/api/tests/conftest.py:37-125`` for the
integration bootstrap: if upstream tweaks that, mirror the change here
so both suites pick it up.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from centaur_lab.integration_db import (
    can_connect,
    dsn_with_db,
    ensure_database,
    run_migrations_async,
)

_TEST_DATABASE = "centaur_test"

# This module lives at ``overlay/centaur_lab/testing.py``; the canonical
# upstream migrations directory is ``.centaur/services/api/db/migrations``.
# ``parents[2]`` walks: testing.py → centaur_lab → overlay → repo-root.
_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2] / ".centaur" / "services" / "api" / "db" / "migrations"
)


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``integration`` marker so ``--strict-markers`` and CI
    can target this suite with ``-m "not integration"`` instead of the
    coarser ``--ignore=tests/integration``.
    """
    config.addinivalue_line(
        "markers",
        "integration: requires CENTAUR_TEST_DATABASE_URL pointing at a real Postgres",
    )


@pytest.fixture(scope="session")
def _test_dsn() -> str:
    """Resolve a DSN pointing at the dedicated ``centaur_test`` database.

    Session-scoped: ensures the DB exists and applies migrations exactly
    once per pytest session, regardless of how many integration tests run.

    When ``CENTAUR_TEST_DATABASE_URL`` is unset every dependent test is
    skipped with a setup snippet rather than failing.
    """
    dsn = os.environ.get("CENTAUR_TEST_DATABASE_URL", "").strip()  # noqa: TID251
    if not dsn:
        pytest.skip(
            "CENTAUR_TEST_DATABASE_URL not set; integration tests require a "
            "real Postgres. Quick setup against the cluster:\n"
            "  kubectl port-forward -n centaur-system "
            "svc/centaur-centaur-postgres 5432:5432 &\n"
            "  PGPASSWORD=$(kubectl get secret -n centaur-system "
            "centaur-infra-env -o jsonpath='{.data.POSTGRES_PASSWORD}' "
            "| base64 -d)\n"
            "  export CENTAUR_TEST_DATABASE_URL="
            "postgres://tempo:$PGPASSWORD@localhost:5432/ai_v2"
        )

    admin_dsn = dsn_with_db(dsn, "postgres")
    test_dsn = dsn_with_db(dsn, _TEST_DATABASE)

    if not asyncio.run(can_connect(admin_dsn)):
        pytest.skip(f"CENTAUR_TEST_DATABASE_URL is set but unreachable: {dsn}")

    asyncio.run(ensure_database(admin_dsn, _TEST_DATABASE))
    asyncio.run(run_migrations_async(test_dsn, _MIGRATIONS_DIR))
    return test_dsn


@pytest_asyncio.fixture
async def db_pool(_test_dsn: str):
    """Yield an asyncpg pool against the ``centaur_test`` database.

    Pool lifecycle only — per-test table cleanup lives in
    ``_clear_company_context_tables`` below so the lifecycle and isolation
    concerns stay orthogonal (mirrors
    ``.centaur/services/api/tests/test_company_context_documents.py``).
    """
    pool = await asyncpg.create_pool(_test_dsn, min_size=1, max_size=2)
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def _clear_company_context_tables(db_pool):
    """Truncate test tables before every integration test.

    Autouse + dependent on ``db_pool`` so adding new tables to clean up
    later is a one-line edit here instead of perturbing pool lifecycle.
    Because ``centaur_test`` is dedicated to integration tests, the
    full-table ``TRUNCATE … CASCADE`` is safe and noticeably faster than
    a scoped ``DELETE``.
    """
    await db_pool.execute("TRUNCATE TABLE company_context_documents CASCADE")
    yield


# ---------------------------------------------------------------------------
# Unit-test mocks
#
# Shared stand-ins for ``asyncpg`` connections/pools and the workflow
# ``WorkflowContext`` so the workflow suite and the semantic_scholar tool
# suite drive ``centaur_lab.paper_document.upsert_document`` (and the
# workflows that wrap it) against the same SQL contract without touching
# a real database. Keeping them here — alongside the integration plugin
# fixtures above — prevents the two test trees from drifting on the
# upsert SQL's positional-arg layout.
# ---------------------------------------------------------------------------


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
"""Positional-arg index for ``upsert_document``'s UPSERT statement.

Tests assert on individual columns by name via ``args[EXECUTE_ARG_INDEX[
"parent_document_id"]]`` rather than threading the column order through
every assertion — when the SQL changes, this map is the single thing
that needs updating.
"""


class MockAsyncpgConn:
    """Minimal stand-in for ``asyncpg.Connection``.

    Extends the basic asyncpg stand-in with ``fetchval`` and
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
