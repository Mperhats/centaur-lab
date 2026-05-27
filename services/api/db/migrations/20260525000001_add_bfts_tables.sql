-- migrate:up
-- BFTS-on-Centaur tree state (Phase 2).
--
-- The tree itself lives here; the workflow's checkpoints hold only IDs
-- pointing into these tables. This keeps each workflow_checkpoints row
-- tiny (one JSON object per ctx.step, per research 03 §State & durability
-- storage).
--
-- Object creation uses ``IF NOT EXISTS`` as defence-in-depth against
-- out-of-band drift: if these tables exist but no row is in
-- ``schema_migrations_overlay`` (e.g. ``DROP`` happened, then the
-- migration was re-stamped manually), reapplying this file is a no-op
-- instead of a hard failure. ``IF NOT EXISTS`` does NOT recover the
-- inverse drift state (table missing while version row is present);
-- that recovery is documented in ``docs/overlay-db-migrations.md``
-- under "Drift recovery".

CREATE TABLE IF NOT EXISTS bfts_runs (
    run_id          TEXT PRIMARY KEY,
    parent_run_id   TEXT,
    idea_json       JSONB NOT NULL,
    config_json     JSONB NOT NULL,
    stage_name      TEXT NOT NULL DEFAULT 'stage_1',
    seed            INT NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'running',
    best_node_id    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bfts_nodes (
    node_id              TEXT PRIMARY KEY,
    run_id               TEXT NOT NULL REFERENCES bfts_runs(run_id) ON DELETE CASCADE,
    parent_node_id       TEXT REFERENCES bfts_nodes(node_id) ON DELETE CASCADE,
    step                 INT NOT NULL,
    stage_name           TEXT NOT NULL,
    plan                 TEXT NOT NULL DEFAULT '',
    code                 TEXT NOT NULL DEFAULT '',
    plot_code            TEXT,
    term_out_json        JSONB,
    exec_time_seconds    DOUBLE PRECISION,
    exc_type             TEXT,
    exc_info_json        JSONB,
    exc_stack_json       JSONB,
    parse_metrics_code   TEXT NOT NULL DEFAULT '',
    parse_term_out_json  JSONB,
    parse_exc_type       TEXT,
    plot_term_out_json   JSONB,
    plot_exec_time_seconds DOUBLE PRECISION,
    plot_exc_type        TEXT,
    analysis             TEXT,
    metric_json          JSONB,
    is_buggy             BOOLEAN,
    is_buggy_plots       BOOLEAN,
    plot_analyses_json   JSONB,
    vlm_feedback_summary TEXT,
    debug_depth          INT NOT NULL DEFAULT 0,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS bfts_nodes_run_idx ON bfts_nodes(run_id, step);
CREATE INDEX IF NOT EXISTS bfts_nodes_parent_idx ON bfts_nodes(parent_node_id);

CREATE TABLE IF NOT EXISTS bfts_artifacts (
    artifact_id   TEXT PRIMARY KEY,
    node_id       TEXT NOT NULL REFERENCES bfts_nodes(node_id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    bytes         BYTEA NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (node_id, relative_path)
);

-- migrate:down
DROP TABLE bfts_artifacts;
DROP TABLE bfts_nodes;
DROP TABLE bfts_runs;
