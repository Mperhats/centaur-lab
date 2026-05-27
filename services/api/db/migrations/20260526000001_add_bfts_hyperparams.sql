-- migrate:up
-- BFTS nightly reflection hyperparameters (Phase 4c).
--
-- Append-only history: each row captures the search-config knobs the
-- reflection workflow chose for the next BFTS run. `effective_from` is the
-- timestamp the row took effect AND the primary key; the latest config is
-- `ORDER BY effective_from DESC LIMIT 1`.
--
-- ``IF NOT EXISTS`` is defence-in-depth against out-of-band drift; see
-- the same comment in ``20260525000001_add_bfts_tables.sql``.

CREATE TABLE IF NOT EXISTS bfts_hyperparams (
    effective_from   TIMESTAMPTZ PRIMARY KEY DEFAULT NOW(),
    debug_prob       DOUBLE PRECISION NOT NULL,
    max_debug_depth  INT NOT NULL,
    num_drafts       INT NOT NULL,
    num_workers      INT NOT NULL,
    metric_reducer   TEXT NOT NULL DEFAULT 'mean',
    notes            TEXT,
    created_by       TEXT NOT NULL DEFAULT 'reflection'
);

CREATE INDEX IF NOT EXISTS bfts_hyperparams_effective_idx ON bfts_hyperparams(effective_from DESC);

-- migrate:down
DROP TABLE bfts_hyperparams;
