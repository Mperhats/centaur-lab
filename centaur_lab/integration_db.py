"""Postgres bootstrap helpers shared by overlay integration test suites.

These helpers are lifted verbatim from
``.centaur/services/api/tests/conftest.py:37-104`` (upstream commit
``6a96324c``). Both ``workflows/tests/integration/conftest.py``
and ``tools/semantic_scholar/tests/integration/conftest.py``
need the same DSN re-basing, connectivity probe, ``CREATE DATABASE``
guard, and dbmate ``-- migrate:up`` extractor — defining them once here
keeps the two conftests honest. Originally they were copy-pasted; review
finding **A10** flagged the drift risk (e.g. an upstream tweak to the
``-- migrate:up (no-transaction)`` regex would silently desync the
overlay copies).

If upstream tweaks any of these helpers in
``.centaur/services/api/tests/conftest.py``, propagate the change here
so both integration suites pick it up automatically.

The helpers are intentionally package-public (no leading underscore)
because they're imported across overlay packages. ``migrations_dir`` is
passed in by the caller, since the relative depth from each conftest to
``.centaur/services/api/db/migrations`` differs per suite.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit

import asyncpg


def dsn_with_db(dsn: str, database: str) -> str:
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


async def can_connect(dsn: str) -> bool:
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


async def ensure_database(admin_dsn: str, database: str) -> None:
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


def extract_up_sql(path: Path) -> str:
    """Extract the ``-- migrate:up`` section from a dbmate-style migration file."""
    text = path.read_text()
    match = re.search(
        r"-- migrate:up\s*\n(.*?)(?=-- migrate:down|$)", text, re.DOTALL
    )
    if not match:
        raise ValueError(f"No '-- migrate:up' section found in {path}")
    return match.group(1).strip()


async def run_migrations_async(dsn: str, migrations_dir: Path) -> None:
    """Apply every ``-- migrate:up`` section in sorted order. Idempotent —
    upstream migrations use ``CREATE TABLE IF NOT EXISTS`` and
    ``CREATE INDEX IF NOT EXISTS`` everywhere, so re-running on an
    existing DB is safe.
    """
    conn = await asyncpg.connect(dsn)
    try:
        for migration_file in sorted(migrations_dir.glob("*.sql")):
            up_sql = extract_up_sql(migration_file)
            await conn.execute(up_sql)
    finally:
        await conn.close()
