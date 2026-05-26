"""Integration test bootstrap for the workflows suite.

Mirrors ``overlay/tools/semantic_scholar/tests/integration/conftest.py``
and ``.centaur/services/api/tests/conftest.py:37-125``. If upstream
tweaks the DSN re-basing / ``CREATE DATABASE`` / dbmate migration apply
flow, propagate the change to both overlay conftests so the trees stay
in sync.

The helpers are intentionally inlined (rather than imported from a
shared module) because each test tree owns its own integration setup
under the upstream convention; a sibling module would just recreate
the ``centaur_lab`` anti-pattern this branch removed.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit

import asyncpg
import pytest
import pytest_asyncio

_TEST_DATABASE = "centaur_test"

# This conftest lives at ``overlay/workflows/tests/integration/conftest.py``;
# the canonical upstream migrations directory is
# ``.centaur/services/api/db/migrations``. ``parents[4]`` walks:
# conftest.py → integration → tests → workflows → overlay → repo-root.
_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[4]
    / ".centaur"
    / "services"
    / "api"
    / "db"
    / "migrations"
)


def _dsn_with_db(dsn: str, database: str) -> str:
    """Re-base a DSN onto a different database name."""
    parts = urlsplit(dsn)
    return urlunsplit(
        SplitResult(
            scheme=parts.scheme,
            netloc=parts.netloc,
            path=f"/{database}",
            query=parts.query,
            fragment=parts.fragment,
        )
    )


async def _can_connect(dsn: str) -> bool:
    """Probe a DSN by opening a connection and running ``SELECT 1``."""
    try:
        conn = await asyncpg.connect(dsn)
    except Exception:
        return False
    try:
        await conn.execute("SELECT 1")
        return True
    finally:
        await conn.close()


async def _ensure_database(admin_dsn: str, database: str) -> None:
    """Create ``database`` if it doesn't already exist (no-op otherwise)."""
    conn = await asyncpg.connect(admin_dsn)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", database
        )
        if not exists:
            safe_db = database.replace('"', '""')
            await conn.execute(f'CREATE DATABASE "{safe_db}"')
    finally:
        await conn.close()


def _extract_up_sql(path: Path) -> str:
    """Extract the ``-- migrate:up`` section from a dbmate-style migration file."""
    text = path.read_text()
    match = re.search(
        r"-- migrate:up\s*\n(.*?)(?=-- migrate:down|$)", text, re.DOTALL
    )
    if not match:
        raise ValueError(f"No '-- migrate:up' section found in {path}")
    return match.group(1).strip()


async def _run_migrations_async(dsn: str, migrations_dir: Path) -> None:
    """Apply every ``-- migrate:up`` section in sorted order.

    Idempotent — upstream migrations use ``CREATE TABLE IF NOT EXISTS``
    and ``CREATE INDEX IF NOT EXISTS`` everywhere, so re-running on an
    existing DB is safe.
    """
    conn = await asyncpg.connect(dsn)
    try:
        for migration_file in sorted(migrations_dir.glob("*.sql")):
            up_sql = _extract_up_sql(migration_file)
            await conn.execute(up_sql)
    finally:
        await conn.close()


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

    admin_dsn = _dsn_with_db(dsn, "postgres")
    test_dsn = _dsn_with_db(dsn, _TEST_DATABASE)

    if not asyncio.run(_can_connect(admin_dsn)):
        pytest.skip(f"CENTAUR_TEST_DATABASE_URL is set but unreachable: {dsn}")

    asyncio.run(_ensure_database(admin_dsn, _TEST_DATABASE))
    asyncio.run(_run_migrations_async(test_dsn, _MIGRATIONS_DIR))
    return test_dsn


@pytest_asyncio.fixture
async def db_pool(_test_dsn: str):
    """Yield an asyncpg pool against the ``centaur_test`` database."""
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
