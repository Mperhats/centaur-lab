# Porting AI Scientist-v2 BFTS onto Centaur: The Simplest Reliable Pattern

## TL;DR
- **Put the tree in a dedicated Postgres table (adjacency list), not inside the Centaur workflow's checkpointed state.** The Centaur workflow only *orchestrates* — it loops, calling steps that atomically claim, expand, and score nodes. Domain data lives in app tables you own; workflow tables are checkpoints, not a database.
- **Claim the next node to expand with one statement: an `UPDATE ... FROM (SELECT ... ORDER BY score DESC ... FOR UPDATE SKIP LOCKED LIMIT 1) ... RETURNING *`.** That single CTE is your scheduler, your lock, and your best-first policy. Parallelism comes for free from Centaur's worker pool — multiple `ctx.step` calls (or sibling workflows) hit the same query and each gets a different row.
- **Stay with raw SQL on psycopg3 (async). No ORM, no job-queue library.** The entire scheduler is ~80 lines: a `bfts_node` table, a `claim_best_expandable()` query, an `expand_node()` step that runs the sandboxed agent turn and inserts children, and a Centaur workflow that loops until a terminal condition.

## Key Findings

**1. Durable-execution consensus: workflow orchestrates, app tables own domain state.** Every mature durable-execution doc converges on this split. Temporal's own self-hosted defaults page is explicit about why you can't hide a search tree inside Event History: "Temporal errors at 2 MB: ErrBlobSizeExceedsLimit"; "History total size limit… Temporal errors at 50 MB"; "History total count limit… Temporal errors after 51,200 Events" — and Temporal's mitigation is the Claim Check pattern (keep payloads in your own store, pass references). DBOS is the most relevant comparison because it is Postgres-native: workflow checkpoints live in a *system* database, but `@DBOS.transaction` functions run against an *application* database that you design. The DBOS Python "Transactions & Datasources" tutorial says verbatim: "Transactions should run in the database in which your application stores data… The application database (the database in which transactions run) does not need to be the same database (or even on the same server) as your system database." Centaur follows the same model — its workflow engine (described by Paradigm as "heavily inspired by Absurd") checkpoints `ctx.step(...)` results into Postgres so that "when a worker restarts, the handler runs again, but `ctx.step(...)` returns cached results for completed work" (centaur.run/architecture). That is a checkpoint store, not a place to put a 21-node search tree with mutating scores. 

Centaur workflows were based on [Absurd](https://github.com/earendil-works/absurd), blog post [here](https://lucumr.pocoo.org/2025/11/3/absurd-workflows/)

**2. The search tree is a plain adjacency list.** Per Yamada et al., *The AI Scientist-v2: Workshop-Level Automated Scientific Discovery via Agentic Tree Search* (arxiv:2504.08066, April 2025), BFTS produces ≤21 expanded nodes per stage; the official SakanaAI/AI-Scientist-v2 README states verbatim: "if num_workers=3 and steps=21, the tree search will explore up to 21 nodes, expanding 3 nodes concurrently at each step." Each node is either buggy or non-buggy, and refinement vs. debug is chosen from the parent's status. That is a small, low-throughput tree — pessimistic locking is cheap and adjacency-list query overhead is irrelevant. Adding `ltree`, nested sets, or closure tables is over-engineering at this scale; adjacency list + a recursive CTE for the rare "give me the ancestors" query is the simplest reliable schema.

**3. `SELECT … FOR UPDATE SKIP LOCKED` composes cleanly with `ORDER BY score DESC LIMIT 1`.** This is the canonical Postgres claim-a-row pattern used by Solid Queue, pg-boss, Oban, Que, Graphile Worker, and DBOS's own queue implementation. The subquery-with-`SKIP LOCKED`-then-`UPDATE…FROM…RETURNING` shape is important: putting the lock inside a CTE and stamping the row in the same statement makes the claim atomic and race-free, and the `ORDER BY` inside the locked subquery is what gives you best-first selection. The one caveat: as the official Postgres docs note (and the Inferable post quotes), `SKIP LOCKED` "provides an inconsistent view of the data by design" — under heavy contention a worker may not always claim the *globally* highest-scoring expandable node if that row is locked at the instant of the query. For BFTS with 3 concurrent workers and tens of nodes this is a non-issue; for thousands of concurrent workers it would be.

**4. Avoid reinventing a queue — but you aren't, because this isn't a queue.** The "don't reinvent a job queue with SKIP LOCKED" warnings, most clearly articulated by Richard Yen in "Potential Consequences of Using Postgres as a Job Queue" (richyen.com, 4 May 2026), apply at very high concurrency: "When you've got thousands of concurrent workers hammering a jobs table with SELECT ... FOR UPDATE SKIP LOCKED, things start to behave in ways that aren't obvious from the application layer. CPU usage creeps up. Also vacuum sometimes can't keep up. Finally, in the wait event stats, you start seeing ominous entries like LWLock:MultiXactSLRU stacking up across many backends." BFTS runs on the order of tens of expansions over hours. The `bfts_node` table is your *domain* (a tree of experiment attempts you actually want to query, visualize, and resume), not an ephemeral job spool. Best-first selection over a scored tree is not job dispatch — it's the algorithm. Implementing it with one SKIP LOCKED query is the right minimal tool; pulling in pgmq/Celery/RQ would add an external queueing concept on top of state you already need to persist for the algorithm.

**5. psycopg3 async + raw SQL is the right Python posture in 2026.** Centaur is async (FastAPI + asyncio); asyncpg is faster on synthetic benchmarks (MagicStack's own README claims ~5× over psycopg3 on simple-query throughput), but psycopg3's `AsyncConnectionPool`, native row factories, server-side parameter binding, and unified sync/async API mean roughly half the maintenance burden when you only need a handful of statements. SQLAlchemy Core would be defensible but adds a dependency and an abstraction layer for ~5 queries you'll write once and never touch. The "elegance = low LOC" criterion clearly points at psycopg3 + a `queries.sql` file (or inline `text(...)`).

## Details

### Recommended schema (one table)

```sql
CREATE TABLE bfts_node (
    id           BIGSERIAL PRIMARY KEY,
    run_id       UUID NOT NULL,                  -- ties a tree to a workflow run
    parent_id    BIGINT REFERENCES bfts_node(id) ON DELETE CASCADE,
    stage        SMALLINT NOT NULL,              -- BFTS stage 1..4
    status       TEXT NOT NULL,                  -- 'pending' | 'in_flight' | 'good' | 'buggy' | 'terminal'
    score        DOUBLE PRECISION,               -- LLM-judged score; NULL until evaluated
    depth        SMALLINT NOT NULL,
    payload      JSONB NOT NULL,                 -- code, plan, results, stderr, plot refs, etc.
    claimed_by   TEXT,                           -- worker id, for observability + lease recovery
    claimed_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The only index you need for the hot path:
CREATE INDEX bfts_node_expandable
    ON bfts_node (run_id, stage, score DESC NULLS LAST, id)
    WHERE status IN ('good', 'pending');
```

Notes:
- One table covers the tree, the work queue, and the result store. Don't split.
- A *partial* index on the expandable predicate is what keeps the claim query O(log n) regardless of how many completed nodes accumulate.
- `payload JSONB` holds artifacts inline if they're small; for big plots/code dumps, store a sandbox path or S3 URI here (claim-check pattern, but for your own table, not for the workflow engine).
- `claimed_by` + `claimed_at` exist only for lease recovery and dashboards. They are not the locking mechanism — `FOR UPDATE` is. They let a janitor query reset `status='in_flight' AND claimed_at < now() - interval '15 min'` back to `'pending'` if a worker crashed before Centaur's lease expiry caught it.

### The single hot-path query: claim the best expandable node

```sql
WITH next AS (
    SELECT id
    FROM   bfts_node
    WHERE  run_id = $1
      AND  stage  = $2
      AND  status IN ('good', 'pending')          -- "expandable"
      AND  ( $3::int IS NULL OR depth < $3 )      -- max-depth guard
    ORDER  BY score DESC NULLS LAST, id           -- best-first; id tiebreak for determinism
    LIMIT  1
    FOR    UPDATE SKIP LOCKED
)
UPDATE bfts_node n
SET    status     = 'in_flight',
       claimed_by = $4,
       claimed_at = now(),
       updated_at = now()
FROM   next
WHERE  n.id = next.id
RETURNING n.*;
```

This is the entire scheduler. It:
- selects the highest-scoring expandable node not currently being expanded by another worker,
- atomically marks it `in_flight` so no peer can re-pick it,
- returns the row (with payload) for the worker to operate on,
- returns zero rows when the frontier is empty *or* fully in flight — the workflow uses that to decide between "sleep and retry" vs. "tree is done."

The `NULLS LAST` clause is what makes brand-new `pending` children (no score yet) explorable after every `good` node has been refined to a worse score — a small but important best-first nuance.

### The Centaur workflow (≈30 LOC)

Centaur's documented workflow API (centaur.run/extend/workflows) uses module-level `WORKFLOW_NAME` + `async def handler(inp, ctx)`, with durable primitives `ctx.step(name, fn)`, `ctx.sleep(name, duration)`, `ctx.wait_for_event(...)`, `ctx.start_workflow / wait_for_workflow / run_workflow`, and `ctx.start_agent / run_agent`. The shape below is a direct port of that:

```python
# workflows/bfts.py
from dataclasses import dataclass
from api.workflow_engine import WorkflowContext
from centaur_app.db import claim_best_expandable, finalize_node

WORKFLOW_NAME = "bfts"

@dataclass
class Input:
    run_id: str
    stage: int
    max_nodes: int = 21
    max_depth: int = 5
    fanout: int = 3              # mirrors AI Scientist-v2's num_workers

async def handler(inp: Input, ctx: WorkflowContext) -> dict:
    expanded = 0
    while expanded < inp.max_nodes:
        # Fan out up to `fanout` child workflows; each one claims a node and expands it.
        handles = []
        for _ in range(inp.fanout):
            h = await ctx.start_workflow("bfts_expand_one", {
                "run_id": inp.run_id, "stage": inp.stage, "max_depth": inp.max_depth})
            handles.append(h)
        results = [await ctx.wait_for_workflow(h) for h in handles]
        expanded += sum(1 for r in results if r.get("expanded"))
        if all(not r.get("expanded") for r in results):
            break        # frontier empty -> done
    return {"expanded": expanded}
```

```python
# workflows/bfts_expand_one.py
WORKFLOW_NAME = "bfts_expand_one"

async def handler(inp, ctx):
    node = await ctx.step("claim", lambda: claim_best_expandable(
        run_id=inp["run_id"], stage=inp["stage"],
        max_depth=inp["max_depth"], worker=ctx.run_id))
    if node is None:
        return {"expanded": False}

    # The agent turn runs in an isolated Centaur sandbox.
    result = await ctx.run_agent(
        "agent_turn",
        text=build_prompt(node),               # refine if good, debug if buggy
        thread_key=f"bfts:{inp['run_id']}:{node['id']}",
    )

    await ctx.step("finalize", lambda: finalize_node(
        parent_id=node["id"], result=result))   # writes children + parent status/score
    return {"expanded": True, "node_id": node["id"]}
```

That's the whole orchestration. Things to notice:
- `ctx.step("claim", ...)` and `ctx.step("finalize", ...)` are the only places that touch the database. They are pure-enough functions: on workflow retry, Centaur returns the cached `node` dict from the first attempt — so if the agent turn crashes mid-expansion, recovery doesn't re-claim a *different* node.
- The agent turn itself is a `ctx.run_agent` — it gets its own checkpoint, runs in an isolated sandbox pod, can take hours, survives worker restarts.
- Parallelism is governed by `fanout` + Centaur's `WORKFLOW_WORKER_CONCURRENCY` env var. No queue config, no concurrency primitive — `SKIP LOCKED` *is* the concurrency primitive.
- The outer workflow loops; if everyone returns `expanded=False` in a fan-out batch, the frontier is empty and we exit. This is the natural BFTS termination condition.

### The DB layer (psycopg3 async, ~40 LOC)

```python
# centaur_app/db.py
import os
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

pool = AsyncConnectionPool(conninfo=os.environ["BFTS_DB_URL"], open=False,
                           kwargs={"row_factory": dict_row})

_CLAIM_SQL = """
WITH next AS (
  SELECT id FROM bfts_node
  WHERE run_id=%(run_id)s AND stage=%(stage)s
    AND status IN ('good','pending')
    AND (%(max_depth)s::int IS NULL OR depth < %(max_depth)s)
  ORDER BY score DESC NULLS LAST, id
  LIMIT 1 FOR UPDATE SKIP LOCKED
)
UPDATE bfts_node n
SET status='in_flight', claimed_by=%(worker)s,
    claimed_at=now(), updated_at=now()
FROM next WHERE n.id=next.id
RETURNING n.*;
"""

async def claim_best_expandable(*, run_id, stage, max_depth, worker):
    async with pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
        await cur.execute(_CLAIM_SQL, dict(run_id=run_id, stage=stage,
                                           max_depth=max_depth, worker=worker))
        return await cur.fetchone()

async def finalize_node(*, parent_id, result):
    # result = {"status": "good"|"buggy", "score": float|None,
    #           "children": [payload, ...], "meta": {...}}
    async with pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
        await cur.execute(
            "UPDATE bfts_node SET status=%s, score=%s, "
            "payload = payload || %s::jsonb, "
            "claimed_by=NULL, claimed_at=NULL, updated_at=now() WHERE id=%s",
            (result["status"], result.get("score"),
             Jsonb(result.get("meta", {})), parent_id))
        if result.get("children"):
            await cur.executemany(
                "INSERT INTO bfts_node (run_id, parent_id, stage, status, depth, payload) "
                "SELECT run_id, %s, stage, 'pending', depth+1, %s::jsonb "
                "FROM bfts_node WHERE id=%s",
                [(parent_id, Jsonb(c), parent_id) for c in result["children"]])
```

That is the entire data layer. Two functions. Roughly 200 total LOC for the BFTS port including schema, queries, both workflow files, and a janitor.

### Why not store the tree in Centaur's `ctx.step` checkpoints?

Three reasons that all matter:
1. **Mutation.** A BFTS node's `status` and `score` change after it is created (its children's outcomes affect refinement decisions). `ctx.step` is *append-only*; each call's output is frozen at first completion. You'd be modeling mutable state on an immutable log, which is the same anti-pattern Temporal's own docs warn against under "Managing very long-running Workflows" — Event History grows and Continue-As-New becomes mandatory.
2. **Visibility.** You want to query the tree for dashboards, debugging, idea-level analytics, and to *resume* runs across workflow versions. A normal table is queryable with SQL; checkpoint blobs are not.
3. **Size.** Each node carries code, stdout, plot metadata. 21 nodes × multi-KB payloads is fine in a table; in a workflow event log it bloats replay and bumps against per-step blob limits in the general durable-execution patterns Centaur is modeled on.

The rule of thumb from across the durable-execution world: **workflow tables checkpoint *decisions* (which step ran, what it returned); application tables hold *facts* (what's in the world).** A BFTS tree is facts.

### Concurrency pitfalls worth knowing

- **`SKIP LOCKED` returns 0 rows when contended.** Your worker must treat that as "frontier exhausted *for now*" — either exit (if the outer workflow says we've spawned enough siblings) or sleep briefly. The example workflow handles this by treating an all-`expanded=False` batch as termination.
- **Hold the transaction only as long as the claim itself.** Do not keep the row locked while the agent turn runs (which is hours). The pattern above commits immediately after the `UPDATE … RETURNING`; the row's lease lives in `claimed_at`, not in a Postgres lock.
- **Janitor for crashed workers.** Run a one-line scheduled workflow every few minutes: `UPDATE bfts_node SET status='pending', claimed_by=NULL WHERE status='in_flight' AND claimed_at < now() - interval '15 min'`. Centaur's `WORKFLOW_WORKER_LEASE_S` recovers the *workflow*, but the *domain row's* lease is your responsibility.
- **`ORDER BY score DESC NULLS LAST, id` not just `score DESC`.** Without the `id` tiebreak two workers running the same query at the same nanosecond can produce nondeterministic plans on equal scores; the `id` tiebreak makes the claim reproducible and replay-friendly.
- **Don't put `LIMIT` outside the locking subquery.** Some authors write `SELECT … FOR UPDATE SKIP LOCKED LIMIT 1` at the outer level; that's fine in Postgres, but if you ever batch-claim N rows, the canonical, portable form is the CTE shape above. Stick to one shape.

## Recommendations

**Do this, in order.**

1. **Create the `bfts_node` table and the partial index exactly as above.** Don't add `ltree`, `closure_table`, or any "tree library" — adjacency list is correct here.
2. **Write `claim_best_expandable` and `finalize_node` as plain async psycopg3 functions** against a separate Postgres schema (or instance) from Centaur's own workflow checkpoint DB. Use `AsyncConnectionPool`. No SQLAlchemy.
3. **Write two Centaur workflows:** an outer `bfts` loop and an inner `bfts_expand_one`. The inner one is the smallest possible unit: claim → run agent → finalize. Everything else (stage advancement, ablations, hyperparam tuning per Yamada et al. §3) is another outer workflow that *invokes* `bfts` on a fresh `run_id`.
4. **Tune two knobs only:** `fanout` in the workflow input (defaults to 3, matching AI Scientist-v2's `num_workers=3`) and `WORKFLOW_WORKER_CONCURRENCY` at the Centaur worker level (must be ≥ `fanout` × number of concurrent BFTS runs). Nothing else.
5. **Add a scheduled janitor workflow** that resets stale `in_flight` rows every 5 minutes. Five lines.

**When to revisit this design** (i.e., what would make you change it):
- If you ever need more than ~50 concurrent expansions per run, profile lock contention; per Richard Yen's analysis, the SLRU/MultiXact issues start to bite at "thousands of concurrent workers." At that point introduce a partitioned `bfts_node` table or move the queue to a dedicated tool.
- If node payloads consistently exceed a few hundred KB, move the heavy artifacts to S3/object storage and keep references in `payload`. Don't bloat the row.
- If you find yourself adding more than three or four mutable status values, the algorithm has grown beyond BFTS; consider a more explicit state machine — but still in the table, not in the workflow.
- If you need cross-run analytics (e.g., "across the last 100 ideas, what fraction of refinements improved score?"), the adjacency list + SQL handles it. If you start writing recursive CTEs more than a couple of times, *then* add a `path ltree` column maintained by a trigger — but not before.

## Caveats

- The Centaur API details (`ctx.step(name, fn)`, `ctx.run_agent`, `ctx.start_workflow`/`wait_for_workflow`, the `WORKFLOW_NAME` + `async def handler(inp, ctx)` registration shape) are documented at centaur.run/extend/workflows and the paradigmxyz/centaur README, both very recent — Centaur was open-sourced on May 21, 2026 per the Paradigm/Tempo blog post "Open Sourcing Centaur: Multiplayer, self-hosted, secure agents" (paradigm.xyz/2026/05/open-sourcing-centaur-multiplayer-self-hosted-secure-agents). The exact SQL schema of Centaur's internal workflow checkpoint tables is not publicly documented; the recommendation here treats Centaur's workflow store as a black box and only relies on its documented `ctx.step` checkpointing semantics — which the Paradigm announcement explicitly states are "heavily inspired by Absurd," referring to Armin Ronacher's "Absurd Workflows: Durable Execution With Just Postgres" (lucumr.pocoo.org/2025/11/3/absurd-workflows/, published Nov 3, 2025).
- `SKIP LOCKED`'s "inconsistent view" property is a feature here, not a bug — but it does mean BFTS is, strictly speaking, *approximately* best-first under contention. For 3 concurrent workers on ≤21 nodes, the probability of materially suboptimal selection is negligible. If your evaluation function is highly peaked (one obvious best node), you may want `fanout=1` for the first few expansions of each stage.
- This design assumes one Postgres instance backs both Centaur's workflow store and your `bfts_node` table (different schemas are fine). Splitting them across two Postgres instances is supported but loses the option to do a single transaction across "workflow decided X" + "tree row mutated." For BFTS that single-transaction option isn't needed, but it's a nice escape hatch.
- The "don't use Postgres as a queue at scale" objections (MultiXactSLRU contention, WAL pressure documented by Richard Yen) are real but kick in at job rates orders of magnitude higher than BFTS produces. Re-evaluate only if you're running many thousands of concurrent expansions.