"""Shared pytest plugin for overlay integration suites.

Both ``overlay/tools/semantic_scholar/tests/integration/conftest.py`` and
``overlay/workflows/tests/integration/conftest.py`` need the same DSN
re-basing, ``CREATE DATABASE`` guard, dbmate migration apply pass, and
per-test ``TRUNCATE`` — defining the fixtures once here keeps the two
conftests from drifting (review finding A10).

Each conftest enables this plugin by setting::

    pytest_plugins = ["centaur_lab.testing"]

The ``centaur_lab`` package sits at ``overlay/centaur_lab/`` and is on
``pythonpath`` for both test suites via their respective ``pyproject.toml``
``[tool.pytest.ini_options]`` blocks, so the import resolves identically
from either tree.

Mirrors ``.centaur/services/api/tests/conftest.py:37-125``: if upstream
tweaks the bootstrap there, mirror the change here so both suites pick
it up.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

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
