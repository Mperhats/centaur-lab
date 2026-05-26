"""Integration tests for paper workflows — gated on CENTAUR_TEST_DATABASE_URL.

These tests exercise save_papers and research_brief against a real Postgres
database with the centaur schema and pg_search migrations applied. The
Semantic Scholar HTTP client is still mocked because flaky external calls
don't add coverage we don't already have in the unit suite.

Recommended local setup (see db/README.md:67-78 for the canonical recipe):

    kubectl port-forward -n centaur-system svc/centaur-centaur-postgres 5432:5432 &
    PGPASSWORD=$(kubectl get secret -n centaur-system centaur-infra-env \
        -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)
    export CENTAUR_TEST_DATABASE_URL="postgres://tempo:$PGPASSWORD@localhost:5432/ai_v2"
    just overlay::test-workflows-integration

When CENTAUR_TEST_DATABASE_URL is unset, every test in this directory is
skipped with a clear reason. Mirrors the gate in
.centaur/services/api/tests/conftest.py:113-125.
"""

from __future__ import annotations

import os

import asyncpg
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def db_pool():
    """Yield an asyncpg pool connected to CENTAUR_TEST_DATABASE_URL.

    Performs a scoped cleanup of company_context_documents rows with
    source = 'semantic_scholar' before yielding so each test starts
    from a known-empty state for the workflows under test. Rows from
    other sources (e.g. Slack ETL) on the shared dev DB are left
    untouched. Pool is closed after the test.
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

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        # Scoped cleanup: never touch rows written by Slack ETL or any other
        # source. These workflows only write source = 'semantic_scholar', so
        # deleting that subset gives us a clean slate without nuking the
        # shared dev DB.
        await pool.execute(
            "DELETE FROM company_context_documents WHERE source = 'semantic_scholar'"
        )
        yield pool
    finally:
        await pool.close()
