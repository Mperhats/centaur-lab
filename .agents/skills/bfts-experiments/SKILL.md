---
name: bfts-experiments
description: "Use when the user asks to kick off, run, or verify a BFTS / bfts_root / bfts_research / tree-search experiment — including num_seeds, seed children, metric_json aggregates, or post-run inspection of bfts_nodes. Triggers on \"BFTS\", \"bfts_root\", \"bfts_research\", \"tree search\", \"num_seeds\", \"seed children\", \"is_seed_node\", F.4 verification, or \"kick off a run\" without an idea."
---

# BFTS experiments

BFTS runs are **long** (often hours). They need a **real research idea** before
tree search — not hyperparameter knobs alone.

## Default path: `bfts_research` (one workflow, Slack streaming)

Use **`bfts_research`** for Slack-driven science. When `SLACKBOT_URL` is
configured the Slack thread gets:

1. **Agent turn** — live stream-of-consciousness only (one short kickoff line
   from the sandbox agent; do not narrate progress).
2. **Plain thread posts** — full research brief, then the structured research
   idea (not agent-session streams).
3. **BFTS stream** — one agent-session message with tree-search kickoff and
   live progress until completion

It then starts **`bfts_root`** with research defaults (`num_seeds=3`,
`num_drafts=2`, `num_workers=1`). **Do not** run `ideation` and `bfts_root`
separately in Slack.

### Start from Slack (required for thread streaming)

Prefer the **`bfts_runner`** tool — it injects `thread_key` + `delivery` from
the sandbox JWT (the plain `call workflow run` path often omits them on the
deployed API pin):

```bash
call bfts_runner start_research '{"topic": "<user research question>"}'
```

Fallback (injects thread into JSON body):

```bash
"$CENTAUR_OVERLAY_DIR/services/sandbox/call-workflow-run.sh" '{
  "workflow_name": "bfts_research",
  "eager_start": true,
  "input": {"topic": "<user research question>"}
}'
```

### Agent reply contract (no redundancy)

After start, reply **once** with a single short line, for example:

`Started bfts_research \`wfr_…\`. Brief and idea post in this thread; BFTS progress streams separately.`

**Do not** repeat the same announcement twice in one message. **Do not** narrate
kickoff/progress — workflows stream that. **Do not** post parallel Slack
kickoff text.

After start, run **one** `call workflow get <run_id>` when the user asks for
status or you need `bfts_run_id` / the idea (~2–5 minutes). Report
`output_json.bfts_run_id` and the idea title on success, or `error_text` on
failure. **Do not** poll for hours.

## Manual two-step path (ideation → bfts_root)

Only when you cannot use `bfts_research`:

```bash
# 1) ideation
call workflow run '{"workflow_name":"ideation","eager_start":true,"input":{"topic":"..."}}'
```

When complete, use **`output_json.bfts_run_input`** verbatim (already includes
`idea`, `num_seeds`, `num_drafts`, `num_workers`):

```bash
call workflow run '{
  "workflow_name": "bfts_root",
  "eager_start": true,
  "input": <paste output_json.bfts_run_input>
}'
```

Do **not** hand-build `bfts_root` input with only `idea` — that drops explicit
hyperparams and lets `BFTS_NUM_SEEDS` / `BFTS_NUM_DRAFTS` env win.

## Hard gate: idea before `bfts_root`

**Do not** call `bfts_root` with `idea: {}` or missing required fields:

- `Name`, `Title`, `Short Hypothesis`, `Experiments` (non-empty list)

Empty idea → toy smoke fixture (`idea_was_defaulted: true`) — no seed aggregates.
Slack-scoped runs without an idea are **rejected** by `bfts_root`.

If the user has not given a research question, ask once, then use
`bfts_research` or `ideation`.

## Hyperparams (for operators, not end users)

| Field | Research default | Meaning |
|-------|------------------|---------|
| `num_drafts` | 2 | Parallel trees at root |
| `num_workers` | 1 | Concurrent expands per tree |
| `num_seeds` | 3 | Seed re-eval per tree after best node |

Override only when the user explicitly asks (e.g. "3 trees, 3 seeds each" →
`num_drafts: 3`, `num_seeds: 3` on `bfts_research` input).

## Post-run verification

- `idea_was_defaulted` must be false for F.4 / seed-child checks.
- One-shot `call workflow get <bfts_run_id>` when the user asks — not a poll loop.

## What not to do

- Do not call `bfts_root` with only knobs and no `idea`.
- Do not omit `bfts_run_input` after ideation — use the envelope field.
- Do not tell users to watch only `#bfts-runs` unless thread delivery failed.
- Do not burn budget on 4×2 parallel LLM burst when defaults are enough.
