---
name: bfts-experiments
description: "Use when the user asks to kick off, run, or verify a BFTS / bfts_root / tree-search experiment — including num_seeds, seed children, metric_json aggregates, or post-run inspection of bfts_nodes. Triggers on \"BFTS\", \"bfts_root\", \"tree search\", \"num_seeds\", \"seed children\", \"is_seed_node\", F.4 verification, or \"kick off a run\" without an idea."
---

# BFTS experiments

BFTS runs are **long** (often hours). They need a **real research idea** before
`bfts_root` — not hyperparameter knobs alone.

## Hard gate: idea before `bfts_root`

**Do not** call `bfts_root` when the user only specifies search knobs
(`num_seeds`, `num_drafts`, `num_workers`, …) or says "kick off a BFTS run"
without a populated `idea`.

Required `idea` fields (same bar as `ideation` / `bfts_root`):

- `Name`
- `Title`
- `Short Hypothesis`
- `Experiments` (non-empty list)

If any are missing, **stop in Slack** and ask:

1. What research question or topic should this run test? (one sentence is enough)
2. Offer to run **`ideation`** first, then `bfts_root` with the returned `idea`.

Only call `bfts_root` after you have a full `idea` dict (from the user or from
`ideation` output). Passing `idea: {}` makes the workflow substitute the
**toy-linreg-smoke** fixture (`idea_was_defaulted: true`). That is for infra
smoke only — it will **not** produce `best_node_id`, seed aggregates
(`aggregate_mean` / `aggregate_std` / `aggregate_n`), or `is_seed_node=true`
children, even when `num_seeds=3`.

## Default path: ideation → bfts_root

```bash
# 1) Synthesize idea (minutes, not hours)
call workflow run '{
  "workflow_name": "ideation",
  "eager_start": true,
  "input": {"topic": "<user research question>"}
}'
```

When `ideation` completes, take `output_json.idea` and start BFTS. Seed
papers are **already persisted** (`papers_persisted` in the output) — do
not call `save_papers` again unless the user asks to save additional IDs.

```bash
call workflow run '{
  "workflow_name": "bfts_root",
  "eager_start": true,
  "input": {
    "idea": <paste ideation idea dict>,
    "num_seeds": 3,
    "thread_key": "'"$CENTAUR_THREAD_KEY"'",
    "delivery": {
      "platform": "slack",
      "channel": "<channel_id>",
      "thread_ts": "<thread ts>",
      "recipient_user_id": "<Slack user id>"
    }
  }
}'
```

Tell the user the `run_id` once in chat (one short line). **Do not** post your
own Slack kickoff/progress/completion messages — `bfts_root` owns thread
notifications when `thread_key` / `delivery` are set. Do **not** echo long idea
text into Slack after starting the run (the workflow kickoff already includes
the title and resolved `num_drafts` / `num_seeds` sources).

**Do not** start a background poll loop or block the agent turn waiting on
`call workflow get`. The workflow posts into the **same Slack thread**
automatically when `thread_key` / `delivery` are set (pass them in run input;
sandbox `call workflow run` also sends `X-Centaur-Thread-Key`, which the API
merges when the centaur pin includes the header-enrichment router patch):

- kickoff (with resolved config) + periodic progress + final @-mention summary
- a one-line mirror on `#bfts-runs` at the end only

Do **not** tell users to watch only `#bfts-runs` unless thread delivery failed.

Pass `num_seeds` (and `num_drafts` if not the default 4) in **run input** when
the user asks — otherwise `BFTS_NUM_SEEDS` from Helm `api.extraEnv` wins and
the kickoff line will show `(num_seeds, env)`.

## Post-run verification (only after a real idea)

When the user wants `metric_json` checks (`aggregate_mean`, `aggregate_std`,
`aggregate_n`), `best_node_id`, or seed-child rows:

- Confirm the run used a **non-default** idea (`idea_was_defaulted` must be
  false in workflow output).
- If they skipped ideation, explain **before** starting that empty ideas cannot
  satisfy F.4 verification — offer ideation first.
- After completion (user asks, or workflow notified them), query `bfts_nodes`
  / read `trees[]` from `call workflow get <run_id>` — one-shot, not a poll loop.

## What not to do

- Do not call `bfts_root` with only `num_seeds` / `num_drafts` and no `idea`.
- `num_seeds` controls seed re-eval **per tree at the end**; `num_drafts` controls
  **how many parallel trees** (default 4). Pass both when the user wants three
  trees with three seeds: `"num_drafts": 3, "num_seeds": 3`.
- Do not poll `workflow get` for hours; use `eager_start: true` + Slack
  `delivery`.
- Do not treat toy-defaulted runs as evidence or as successful F.4 verification.
- Do not burn LLM budget on 4×20-iter trees when the user never supplied a hypothesis.
