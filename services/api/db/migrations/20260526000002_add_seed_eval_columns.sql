-- migrate:up
-- Multi-seed re-evaluation of the best node (Sakana parity, F.4).
--
-- A single-seed "best" Stage-1 node is brittle — small loss-function noise
-- can flip the ranking between runs. Sakana's multi_seed_eval re-runs the
-- best node N times with different seeds and aggregates the final metric.
-- We port that as a trailing fan-out in bfts_tree.handler; this migration
-- adds the two columns the DAO + selector need to distinguish "real"
-- expansion nodes from "seed re-eval bookkeeping" nodes.
--
-- ``is_seed_node = TRUE`` rows are excluded from ``_buggy_leaf_nodes`` /
-- ``_good_nodes`` / ``_draft_nodes`` in the selector so they don't pollute
-- ``select_next`` accounting (they're a metric-aggregation side-channel,
-- not selection candidates). ``seed`` is the per-node deterministic
-- np/random/torch seed.
--
-- The partial index on (parent_node_id) WHERE is_seed_node = TRUE makes
-- ``list_seed_children(parent_node_id=$1)`` a single seek for the trailing
-- aggregation step.
--
-- ``IF NOT EXISTS`` is defence-in-depth against out-of-band drift; see
-- the same comment in ``20260525000001_add_bfts_tables.sql``.

ALTER TABLE bfts_nodes
    ADD COLUMN IF NOT EXISTS is_seed_node BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS seed         INT;

CREATE INDEX IF NOT EXISTS bfts_nodes_seed_parent_idx
    ON bfts_nodes(parent_node_id)
    WHERE is_seed_node = TRUE;

-- migrate:down
DROP INDEX IF EXISTS bfts_nodes_seed_parent_idx;
ALTER TABLE bfts_nodes
    DROP COLUMN seed,
    DROP COLUMN is_seed_node;
