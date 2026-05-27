"""Schema pre-flight check for the overlay-owned BFTS tables.

Background — this exists to catch the failure mode described in
``docs/overlay-db-migrations.md`` ("Drift recovery"): the BFTS overlay
tables get dropped out-of-band (manual ``DROP TABLE``, restored
snapshot, dev-tool reset) while the corresponding row in
``schema_migrations_overlay`` survives, so dbmate's "version-in-table →
skip" idempotency hides the drift and the next BFTS run fails
mid-flight inside the workflow's first DB call with a confusing
``UndefinedTableError``.

``assert_bfts_schema_present`` runs a single ``SELECT … LIMIT 0``
against every BFTS-owned table at workflow entry. ``LIMIT 0`` does no
work in Postgres — the planner short-circuits the query before
materialising rows — but the relation lookup still happens, so a
missing table raises ``asyncpg.UndefinedTableError`` which we re-raise
as a ``RuntimeError`` naming both the table and the recovery procedure.

Workflows wrap the call in ``ctx.step("preflight_schema_check", …)`` so
the result is checkpointed once per run (replay-safe) and the failure
is visible in the workflow trace at iteration 0 instead of mid-flight.
"""
from __future__ import annotations

import asyncpg

# All overlay-owned BFTS tables. Kept as a tuple so callers can iterate
# in declaration order for log readability. The single source of truth
# for "which tables does this overlay add?" — extending it does not
# require touching the workflow handlers.
BFTS_OVERLAY_TABLES: tuple[str, ...] = (
    "bfts_runs",
    "bfts_nodes",
    "bfts_artifacts",
    "bfts_hyperparams",
)


async def assert_bfts_schema_present(pool: asyncpg.Pool) -> dict[str, str]:
    """Verify every overlay-owned BFTS table is queryable.

    Returns a dict mapping ``table_name -> "ok"`` for every table that
    resolved successfully, so the wrapping ``ctx.step`` checkpoint
    captures a non-trivial value instead of ``None``. The structure
    (rather than a bare ``True``) makes future per-table extensions
    (e.g. column-presence checks) backwards compatible without
    rewriting checkpoint history.

    Raises:
        RuntimeError: if any BFTS table is missing. The message names
            the table that failed and points operators at the recovery
            procedure in ``docs/overlay-db-migrations.md``. Chained
            from the underlying asyncpg exception so the original
            error is still inspectable.
    """
    results: dict[str, str] = {}
    for table in BFTS_OVERLAY_TABLES:
        try:
            # ``LIMIT 0`` short-circuits in Postgres so there's no row
            # cost; the relation lookup still happens, which is the
            # part we actually want.
            await pool.fetchval(f"SELECT 1 FROM {table} LIMIT 0")
        except asyncpg.UndefinedTableError as exc:
            msg = (
                f"BFTS overlay table {table!r} is missing but the "
                f"corresponding row in ``schema_migrations_overlay`` "
                f"is present. This is the schema-drift state described "
                f"in ``docs/overlay-db-migrations.md`` (Drift "
                f"recovery): a ``DROP TABLE`` happened out-of-band "
                f"and dbmate's version-in-table idempotency now hides "
                f"the recreate. Recover by running, against the API "
                f"DB: ``DELETE FROM schema_migrations_overlay WHERE "
                f"version IN (<affected versions>);`` and restarting "
                f"the API pod so dbmate reapplies the migrations."
            )
            raise RuntimeError(msg) from exc
        results[table] = "ok"
    return results
