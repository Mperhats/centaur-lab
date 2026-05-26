# BFTS Stage-1 Fidelity Follow-up Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the five Stage-1 BFTS gaps identified during the 2026-05-26 comparison against `.scientist/ai_scientist/treesearch/`. None of these are covered by the Phase 4 plan or its deferred sections (`docs/superpowers/plans/2026-05-26-bfts-phase4.md:143-149`); each is small enough to ship independently of Phase 4b's Stages 2–4 curriculum work.

**Architecture:** All work is additive to the Phase 0–4 BFTS port. One small migration adds two columns + one column to `bfts_nodes`. New code lives in existing modules (`_bfts_state`, `_bfts_prompts`, `_bfts_export`, `_bfts_llm`, `bfts_tree`); no new top-level workflows. Sakana selector and the `_bfts_expand` pipeline are unchanged in shape; F.4 introduces a new "seed re-run" expansion mode that bypasses the LLM but reuses the executor.

**Tech stack:** Same as Phase 0–4 — `WorkflowContext`, `asyncpg`, `httpx`, GraphViz dot text, Postgres. No new package dependencies.

**Branch:** Continue on `feat/centaur-scientist`. Each task ends with one commit.

---

## Background — why these five tasks

From the BFTS-vs-`.scientist` comparison (chat 2026-05-26):

| Task | Gap | Existing plan coverage |
|------|-----|-------------------------|
| F.1 | `bfts_tree` does not inspect `wait_for_workflow` status; permanently-failed `bfts_expand_one` children leave NULL placeholder rows that stall the selector | In-code TODO at `overlay/workflows/bfts_tree.py:301-310`; Phase 4 plan line 54 *deferred* the wrong fix (SKIP LOCKED janitor) |
| F.2 | No prior-attempts memory injected into draft/improve prompts | Research 02 §OQ #7 raised three options, no decision; we silently picked option C ("drop") |
| F.3 | No human-readable tree visualization (upstream emits `tree_plot.html`) | Not tracked anywhere |
| F.4 | No multi-seed re-evaluation of the best node | Research 02 line 597 tagged this "Phase 2 work"; never made it into Phase 2 or Phase 4 |
| F.5 | `_bfts_llm` raises on first non-2xx; no in-call retry/backoff (upstream uses `backoff.on_exception` to 60s) | Not tracked anywhere |

The "reopen LLM best-node arbitration" item from the same comparison is intentionally **not** included here: it was an explicit decision in `2026-05-25-bfts-on-centaur.md:32` (Spec correction #6) to drop the LLM judge, and reopening it is a separate design conversation, not a bug-fix.

---

## File structure

| File | Status | Tasks |
|------|--------|-------|
| `overlay/services/api/db/migrations/20260526000002_add_seed_eval_columns.sql` | Create | F.4 |
| `overlay/workflows/_bfts_state.py` | Modify | F.1, F.2, F.4 |
| `overlay/workflows/_bfts_prompts.py` | Modify | F.2 |
| `overlay/workflows/_bfts_expand.py` | Modify | F.2, F.4 |
| `overlay/workflows/_bfts_select.py` | Modify | F.4 |
| `overlay/workflows/_bfts_config.py` | Modify | F.2, F.4 |
| `overlay/workflows/_bfts_export.py` | Modify | F.3 |
| `overlay/workflows/_bfts_llm.py` | Modify | F.5 |
| `overlay/workflows/bfts_tree.py` | Modify | F.1, F.4 |
| `overlay/workflows/bfts_expand_one.py` | Modify | F.4 |
| `overlay/workflows/bfts_root.py` | Modify | F.4 |
| `overlay/workflows/tests/test_bfts_tree_handler.py` | Modify | F.1, F.4 |
| `overlay/workflows/tests/test_bfts_state.py` | Modify | F.1, F.2, F.4 |
| `overlay/workflows/tests/test_bfts_prompts.py` | Modify | F.2 |
| `overlay/workflows/tests/test_bfts_expand.py` | Modify | F.2, F.4 |
| `overlay/workflows/tests/test_bfts_export.py` | Modify | F.3 |
| `overlay/workflows/tests/test_bfts_select.py` | Modify | F.4 |
| `overlay/workflows/tests/test_bfts_llm.py` | Modify | F.5 |

No `.centaur/` or `.scientist/` edits (per `AGENTS.md`).

---

## Phasing

Tasks are independent and can ship in any order. Recommended sequence by ratio of value to effort:

1. **F.5** (~1–2 hours) — single-file change, biggest reliability lift per LOC.
2. **F.1** (~2–3 hours) — closes a known correctness gap that's already a TODO in code.
3. **F.3** (~2–3 hours) — small, big operator-debugging UX win.
4. **F.2** (~half day) — touches more files but no schema change; expected quality lift on draft attempts.
5. **F.4** (~1 day) — schema migration, biggest scope; ship last.

---

# Task F.1: Inspect failed-child status in `bfts_tree`

**Why:** A permanently-failed `bfts_expand_one` child currently leaves its placeholder `bfts_nodes` row with NULL `is_buggy`, `code`, and `metric_json`. Such a row is invisible to `_buggy_leaf_nodes` (`is_buggy is True` check) and `_good_nodes` (`is_buggy is False` check), but **does** count toward `len(drafts)` in `_bfts_select.select_next`, so a draft-stage failure can stall the selector below `num_drafts`. This bug is documented in the in-code TODO at `overlay/workflows/bfts_tree.py:301-310`.

**Fix:** After `wait_for_workflow`, read the child's terminal record (`{status, error, ...}`). If the child failed or was cancelled, mark the placeholder row as buggy with synthetic exception fields so the selector treats it like any other buggy leaf (eligible for debug or replacement).

**Files:**
- Modify: `overlay/workflows/_bfts_state.py` — add `mark_node_failed(pool, *, node_id, exc_type, exc_info, analysis)` helper.
- Modify: `overlay/workflows/bfts_tree.py:293-317` — inspect `wait_for_workflow` result, call `mark_node_failed` on non-completed status.
- Test: `overlay/workflows/tests/test_bfts_state.py` — DAO contract.
- Test: `overlay/workflows/tests/test_bfts_tree_handler.py` — handler-level fan-out + failed-child path.

- [ ] **Step 1: Write the failing DAO test**

```python
# overlay/workflows/tests/test_bfts_state.py
@pytest.mark.asyncio
async def test_mark_node_failed_sets_buggy_with_synthetic_fields():
    pool = _FakePool()
    await insert_node(
        pool, node_id="n1", run_id="r1", parent_node_id=None,
        step=0, stage_name="draft", plan="", code="",
    )
    await mark_node_failed(
        pool, node_id="n1",
        exc_type="ChildWorkflowFailed",
        exc_info={"reason": "worker timeout"},
        analysis="bfts_expand_one returned status=failed",
    )
    row = await pool.fetchrow("SELECT is_buggy, exc_type, exc_info_json, analysis FROM bfts_nodes WHERE node_id=$1", "n1")
    assert row["is_buggy"] is True
    assert row["exc_type"] == "ChildWorkflowFailed"
    assert json.loads(row["exc_info_json"]) == {"reason": "worker timeout"}
    assert "status=failed" in row["analysis"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd overlay/workflows && uv run pytest tests/test_bfts_state.py::test_mark_node_failed_sets_buggy_with_synthetic_fields -v`
Expected: FAIL — `ImportError: cannot import name 'mark_node_failed' from '_bfts_state'`.

- [ ] **Step 3: Implement `mark_node_failed`**

```python
# overlay/workflows/_bfts_state.py — append after update_node_metric
async def mark_node_failed(
    pool: asyncpg.Pool,
    *,
    node_id: str,
    exc_type: str,
    exc_info: dict[str, Any] | None,
    analysis: str,
) -> None:
    """Mark a placeholder node as buggy after its expansion workflow failed.

    Used by ``bfts_tree`` when ``wait_for_workflow`` returns a non-completed
    status. Fills in the minimum fields the selector needs:
    ``is_buggy=True`` (so ``_buggy_leaf_nodes`` sees it), an ``exc_type``
    sentinel (``ChildWorkflowFailed``) so operators can grep for orphaned
    children, and a human-readable ``analysis``.
    """
    await pool.execute(
        """
        UPDATE bfts_nodes
        SET is_buggy = TRUE,
            exc_type = $2,
            exc_info_json = COALESCE($3::jsonb, exc_info_json),
            analysis = $4,
            updated_at = NOW()
        WHERE node_id = $1
        """,
        node_id,
        exc_type,
        json.dumps(exc_info) if exc_info is not None else None,
        analysis,
    )
```

- [ ] **Step 4: Run DAO test to verify it passes**

Run: `cd overlay/workflows && uv run pytest tests/test_bfts_state.py::test_mark_node_failed_sets_buggy_with_synthetic_fields -v`
Expected: PASS.

- [ ] **Step 5: Write the failing tree-handler test**

```python
# overlay/workflows/tests/test_bfts_tree_handler.py — new test
@pytest.mark.asyncio
async def test_failed_expand_child_marks_node_buggy():
    """A bfts_expand_one that returns status=failed must result in the
    placeholder bfts_nodes row being marked is_buggy=True so the next
    iteration's selector treats it as a buggy leaf, not a stalled draft."""
    ctx = _MockContext()
    ctx.start_workflow_returns = {
        "run_id": "child-1", "node_id": "n1",
    }
    ctx.wait_for_workflow_returns = {
        "run_id": "child-1",
        "status": "failed",
        "error": "executor pod evicted",
    }
    # ... (same fixture shape as existing test_bfts_tree_handler.py tests)
    await handler(_input(max_iters=1, num_drafts=1, num_workers=1), ctx)
    assert ("mark_failed_n1", "ChildWorkflowFailed") in ctx.mark_failed_calls
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd overlay/workflows && uv run pytest tests/test_bfts_tree_handler.py::test_failed_expand_child_marks_node_buggy -v`
Expected: FAIL — handler does not currently call `mark_node_failed`.

- [ ] **Step 7: Update `bfts_tree.handler` to inspect status**

Replace the `for node_id, child in children:` block at `overlay/workflows/bfts_tree.py:311-316`:

```python
# Wait for every child to reach a terminal state; if any child failed
# or was cancelled, mark the placeholder row buggy so the selector
# treats it as a buggy leaf rather than stalling on a NULL draft slot.
# (Replaces the deferred SKIP-LOCKED janitor pattern noted in
# docs/superpowers/plans/2026-05-26-bfts-phase4.md:54.)
for node_id, child in children:
    result = await ctx.wait_for_workflow(
        f"wait_expand_{node_id}", run_id=child["run_id"]
    )
    status = (result or {}).get("status")
    if status in ("failed", "failed_permanent", "cancelled"):
        await ctx.step(
            f"mark_failed_{node_id}",
            lambda nid=node_id, st=status, res=result: mark_node_failed(
                pool,
                node_id=nid,
                exc_type="ChildWorkflowFailed",
                exc_info={"child_status": st, "error": res.get("error")},
                analysis=f"bfts_expand_one terminated with status={st}",
            ),
        )
```

Add `mark_node_failed` to the `_bfts_state` import at the top of `bfts_tree.py`.

- [ ] **Step 8: Run all bfts_tree + bfts_state tests**

Run: `cd overlay/workflows && uv run pytest tests/test_bfts_tree_handler.py tests/test_bfts_state.py -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add overlay/workflows/_bfts_state.py \
        overlay/workflows/bfts_tree.py \
        overlay/workflows/tests/test_bfts_state.py \
        overlay/workflows/tests/test_bfts_tree_handler.py
git commit -m "fix(bfts): mark failed expand_one children as buggy so selector recovers

Previously a permanently-failed bfts_expand_one child left its placeholder
node row with NULL is_buggy/code/metric_json, which the selector counted as
a stalled draft slot. Inspect wait_for_workflow status and route failed
children through mark_node_failed() so the next iteration sees a buggy
leaf eligible for debug. Closes the in-code TODO at bfts_tree.py:301-310."
```

---

# Task F.2: Inject prior-attempts memory window into expansion prompts

**Why:** Upstream's `Journal.generate_summary` runs an LLM call every `step()` to build a journal-wide memory and injects it into each parallel batch's prompts (`.scientist/ai_scientist/treesearch/parallel_agent.py:2072-2081`). We currently inject **nothing**: `_bfts_prompts.render_prompts` builds the draft/improve prompt purely from the idea + parent node, with no awareness of earlier siblings. Research 02 §OQ #7 raised three options (rolling buffer / fixed window / drop) and no decision was recorded; we silently chose "drop". This task implements the cheapest option that still helps — a fixed-size recent-history window read from `bfts_nodes`, rendered as a markdown bullet list, no extra LLM call.

**Decision:** Option B from research 02 §OQ #7 — last K nodes' `(stage_name, plan, is_buggy, analysis)` rendered as bullets. K configurable via `prior_attempts_window` (default 5). Skips the current node's row.

**Files:**
- Modify: `overlay/workflows/_bfts_state.py` — add `list_recent_node_summaries(pool, *, run_id, limit, exclude_node_id)`.
- Modify: `overlay/workflows/_bfts_prompts.py` — add `prior_attempts_section(summaries) -> str` markdown helper; thread it through `render_prompts`.
- Modify: `overlay/workflows/_bfts_expand.py` — fetch summaries in a new `ctx.step("load_prior_attempts", ...)` and pass to the renderer for draft + improve branches (debug already has its parent's failure in context).
- Modify: `overlay/workflows/_bfts_config.py` — add `prior_attempts_window: int = 5` to `SearchSettings`; resolver reads `BFTS_PRIOR_ATTEMPTS_WINDOW`.
- Modify: `overlay/workflows/bfts_expand_one.py` — pass `prior_attempts_window` through `Input` into `ExpandContext`.
- Modify: `overlay/workflows/bfts_tree.py` — forward `prior_attempts_window` to `bfts_expand_one`.
- Modify: `overlay/workflows/bfts_root.py` — forward `prior_attempts_window` to `bfts_tree`.
- Test: `overlay/workflows/tests/test_bfts_state.py` — DAO ordering + exclusion.
- Test: `overlay/workflows/tests/test_bfts_prompts.py` — section renders bullets, omits empty.
- Test: `overlay/workflows/tests/test_bfts_expand.py` — load_prior_attempts step is called for draft + improve, skipped for debug.

- [ ] **Step 1: Write the failing DAO test**

```python
# overlay/workflows/tests/test_bfts_state.py
@pytest.mark.asyncio
async def test_list_recent_node_summaries_orders_desc_and_excludes():
    pool = _FakePool()
    for i, (nid, buggy, analysis) in enumerate([
        ("n1", True,  "syntax error"),
        ("n2", False, "ran clean"),
        ("n3", True,  "OOM"),
        ("n4", None,  None),  # placeholder, not yet executed
    ]):
        await insert_node(pool, node_id=nid, run_id="r1", parent_node_id=None,
                          step=i, stage_name="draft", plan=f"plan {nid}", code="")
        if buggy is not None:
            await update_node_metric(pool, node_id=nid, term_out=[],
                                     exec_time_seconds=0.0,
                                     exc_type=None, exc_info=None, exc_stack=None,
                                     metric=None, is_buggy=buggy,
                                     analysis=analysis)
    summaries = await list_recent_node_summaries(
        pool, run_id="r1", limit=2, exclude_node_id="n3",
    )
    # Most recent first, exclude n3 (the current node), only nodes with
    # is_buggy IS NOT NULL (placeholder n4 is skipped).
    assert [s["node_id"] for s in summaries] == ["n2", "n1"]
    assert summaries[0]["analysis"] == "ran clean"
    assert summaries[0]["is_buggy"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd overlay/workflows && uv run pytest tests/test_bfts_state.py::test_list_recent_node_summaries_orders_desc_and_excludes -v`
Expected: FAIL — `ImportError: cannot import name 'list_recent_node_summaries'`.

- [ ] **Step 3: Implement `list_recent_node_summaries`**

```python
# overlay/workflows/_bfts_state.py — append
async def list_recent_node_summaries(
    pool: asyncpg.Pool,
    *,
    run_id: str,
    limit: int,
    exclude_node_id: str | None,
) -> list[dict[str, Any]]:
    """Recent executed nodes for prior-attempts memory injection.

    Skips placeholder rows (``is_buggy IS NULL``) and the current node so
    the LLM doesn't see its own in-flight slot. Ordered most-recent-first;
    callers reverse if they want chronological order in the prompt.
    """
    rows = await pool.fetch(
        """
        SELECT node_id, stage_name, plan, is_buggy, analysis
        FROM bfts_nodes
        WHERE run_id = $1
          AND is_buggy IS NOT NULL
          AND ($2::text IS NULL OR node_id <> $2)
        ORDER BY created_at DESC, node_id DESC
        LIMIT $3
        """,
        run_id, exclude_node_id, limit,
    )
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run DAO test to verify it passes**

Run: `cd overlay/workflows && uv run pytest tests/test_bfts_state.py::test_list_recent_node_summaries_orders_desc_and_excludes -v`
Expected: PASS.

- [ ] **Step 5: Write the failing prompts test**

```python
# overlay/workflows/tests/test_bfts_prompts.py
def test_prior_attempts_section_renders_bullets_oldest_first():
    summaries = [
        {"node_id": "n3", "stage_name": "improve", "plan": "scale lr",
         "is_buggy": True, "analysis": "diverged"},
        {"node_id": "n1", "stage_name": "draft",   "plan": "baseline",
         "is_buggy": False, "analysis": "ran clean"},
    ]
    out = prior_attempts_section(summaries)
    assert "## Prior attempts" in out
    # Oldest first so the LLM reads chronologically:
    n1_idx = out.index("n1")
    n3_idx = out.index("n3")
    assert n1_idx < n3_idx
    assert "buggy: yes" in out  # n3
    assert "buggy: no" in out   # n1
    assert "diverged" in out

def test_prior_attempts_section_returns_empty_for_no_summaries():
    assert prior_attempts_section([]) == ""
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd overlay/workflows && uv run pytest tests/test_bfts_prompts.py::test_prior_attempts_section_renders_bullets_oldest_first -v`
Expected: FAIL — function not defined.

- [ ] **Step 7: Implement `prior_attempts_section` and thread through `render_prompts`**

```python
# overlay/workflows/_bfts_prompts.py — append helper
def prior_attempts_section(summaries: list[dict[str, Any]]) -> str:
    """Render last-K node summaries as a markdown section.

    ``summaries`` arrives most-recent-first from
    ``_bfts_state.list_recent_node_summaries``; we reverse so the LLM
    reads chronologically. Empty list → empty string (caller can append
    unconditionally).
    """
    if not summaries:
        return ""
    lines = ["## Prior attempts (most recent last)\n"]
    for s in reversed(summaries):
        buggy = "yes" if s.get("is_buggy") else "no"
        plan = (s.get("plan") or "").strip().splitlines()[0:1]
        plan_one_line = plan[0] if plan else "(no plan recorded)"
        analysis = (s.get("analysis") or "").strip() or "(no analysis)"
        lines.append(
            f"- **{s['node_id']}** ({s.get('stage_name','?')}, "
            f"buggy: {buggy}): {plan_one_line} — {analysis}"
        )
    return "\n".join(lines) + "\n"
```

Then in `render_prompts`, accept an optional `prior_attempts: str = ""` kwarg and concatenate it into the draft/improve prompt body just before the "## Task" footer. Debug branch ignores it (already has parent failure context).

- [ ] **Step 8: Run prompts tests to verify they pass**

Run: `cd overlay/workflows && uv run pytest tests/test_bfts_prompts.py -v`
Expected: all PASS.

- [ ] **Step 9: Wire memory into `expand_node`**

In `overlay/workflows/_bfts_expand.py`:
1. Add `prior_attempts_window: int = 5` to `ExpandContext`.
2. Add a `pool` attribute to `ExpandContext` (asyncpg.Pool, populated by `bfts_expand_one`).
3. Before each `{draft|improve}_propose` step (NOT debug), add:

```python
prior = await ctx.step(
    "load_prior_attempts",
    lambda: list_recent_node_summaries(
        expand_ctx.pool,
        run_id=expand_ctx.run_id,
        limit=expand_ctx.prior_attempts_window,
        exclude_node_id=expand_ctx.node_id,
    ),
)
prior_section = prior_attempts_section(prior)
# Append prior_section to the rendered prompt body before the LLM call.
```

(Skip the `load_prior_attempts` step for the debug branch — its parent already supplies failure context, and an extra DB round-trip per debug node is wasteful.)

- [ ] **Step 10: Add an expand test for memory injection**

```python
# overlay/workflows/tests/test_bfts_expand.py
@pytest.mark.asyncio
async def test_draft_branch_injects_prior_attempts(...):
    """A draft-stage expansion fetches recent summaries and the rendered
    prompt body contains the 'Prior attempts' section."""
    # ctx records every step name; assert "load_prior_attempts" appears
    # before "draft_propose" and the prompt passed to call_for_text
    # contains "## Prior attempts".

@pytest.mark.asyncio
async def test_debug_branch_skips_prior_attempts(...):
    """Debug expansion has parent failure context; no DB round-trip."""
    # assert "load_prior_attempts" is NOT in ctx.step_names.
```

- [ ] **Step 11: Wire `prior_attempts_window` through configs and workflows**

- `_bfts_config.SearchSettings`: add field, env var `BFTS_PRIOR_ATTEMPTS_WINDOW`, default 5.
- `bfts_root.Input`, `bfts_tree.Input`, `bfts_expand_one.Input`: add `prior_attempts_window: int | None = None`.
- Forward through `start_workflow` calls.
- `bfts_expand_one.handler`: build `ExpandContext` with `prior_attempts_window=resolved.prior_attempts_window` and `pool=ctx._pool`.

- [ ] **Step 12: Run all expand + tree tests**

Run: `cd overlay/workflows && uv run pytest tests/test_bfts_expand.py tests/test_bfts_expand_one.py tests/test_bfts_tree_handler.py tests/test_bfts_config.py -v`
Expected: all PASS.

- [ ] **Step 13: Commit**

```bash
git add overlay/workflows/_bfts_state.py \
        overlay/workflows/_bfts_prompts.py \
        overlay/workflows/_bfts_expand.py \
        overlay/workflows/_bfts_config.py \
        overlay/workflows/bfts_expand_one.py \
        overlay/workflows/bfts_tree.py \
        overlay/workflows/bfts_root.py \
        overlay/workflows/tests/test_bfts_state.py \
        overlay/workflows/tests/test_bfts_prompts.py \
        overlay/workflows/tests/test_bfts_expand.py
git commit -m "feat(bfts): inject prior-attempts memory window into draft/improve prompts

Resolves research 02 OQ #7 with option B (fixed-size window, no extra LLM
call). Each draft/improve expansion fetches the last K executed node
summaries from bfts_nodes and renders them as a markdown 'Prior attempts'
section appended to the prompt body. K defaults to 5, override via
BFTS_PRIOR_ATTEMPTS_WINDOW. Debug branch skips the read since its parent
already supplies failure context."
```

---

# Task F.3: Tree-visualization artifact (`tree.dot`)

**Why:** Operators currently have only raw `bfts_nodes` rows for run debugging. Upstream's `tree_plot.html` (`.scientist/ai_scientist/treesearch/utils/tree_export.py`, 484 LOC) is the primary visual artifact and depends on `python-igraph` + a JS template. Smallest viable port that captures most of the value: write a self-contained GraphViz `.dot` text artifact at end of run. Operators can render with `dot -Tpng tree.dot -o tree.png` or paste into any online dot viewer. Future upgrade to interactive HTML is additive.

**Files:**
- Modify: `overlay/workflows/_bfts_export.py` — add `render_tree_dot(nodes, *, run_id, best_node_id) -> str` and `write_tree_dot_artifact(pool, *, run_id, dot_text)`.
- Modify: `overlay/workflows/bfts_tree.py` — call `write_tree_dot_artifact` after `set_best`.
- Test: `overlay/workflows/tests/test_bfts_export.py` — assert dot output for a small fixture tree.

- [ ] **Step 1: Write the failing render test**

```python
# overlay/workflows/tests/test_bfts_export.py
def test_render_tree_dot_colors_node_states():
    """Three-node tree: root (good) → child (buggy_plots) → grandchild (best).
    Assert dot string structure, all node ids present, edges correct,
    and colors match the legend."""
    nodes = [
        {"node_id": "root", "parent_node_id": None, "stage_name": "draft",
         "is_buggy": False, "is_buggy_plots": False, "metric_json": {"final_value": 0.5}},
        {"node_id": "mid",  "parent_node_id": "root", "stage_name": "improve",
         "is_buggy": False, "is_buggy_plots": True,  "metric_json": {"final_value": 0.4}},
        {"node_id": "best", "parent_node_id": "mid",  "stage_name": "improve",
         "is_buggy": False, "is_buggy_plots": False, "metric_json": {"final_value": 0.3}},
    ]
    dot = render_tree_dot(nodes, run_id="r1", best_node_id="best")
    assert dot.startswith("digraph BFTS_r1 {")
    assert dot.rstrip().endswith("}")
    for nid in ("root", "mid", "best"):
        assert f'"{nid}"' in dot
    assert '"root" -> "mid"' in dot
    assert '"mid" -> "best"' in dot
    # Color legend: green=good, yellow=buggy_plots, gold=best, red=buggy.
    assert 'fillcolor="gold"' in dot       # best node
    assert 'fillcolor="yellow"' in dot     # buggy_plots
    assert 'fillcolor="green"' in dot      # good non-best

def test_render_tree_dot_handles_pending_and_buggy_nodes():
    nodes = [
        {"node_id": "p", "parent_node_id": None, "stage_name": "draft",
         "is_buggy": None,  "is_buggy_plots": None, "metric_json": None},
        {"node_id": "b", "parent_node_id": "p",   "stage_name": "debug",
         "is_buggy": True,  "is_buggy_plots": None, "metric_json": None},
    ]
    dot = render_tree_dot(nodes, run_id="r2", best_node_id=None)
    assert 'fillcolor="lightgray"' in dot  # pending
    assert 'fillcolor="red"' in dot        # buggy
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd overlay/workflows && uv run pytest tests/test_bfts_export.py::test_render_tree_dot_colors_node_states -v`
Expected: FAIL — function not defined.

- [ ] **Step 3: Implement `render_tree_dot`**

```python
# overlay/workflows/_bfts_export.py — append
_DOT_COLOR_BY_STATE = {
    "best":         "gold",
    "good":         "green",
    "buggy":        "red",
    "buggy_plots":  "yellow",
    "pending":      "lightgray",
}


def _node_color(node: dict[str, Any], best_node_id: str | None) -> str:
    if best_node_id and node["node_id"] == best_node_id:
        return _DOT_COLOR_BY_STATE["best"]
    if node.get("is_buggy") is True:
        return _DOT_COLOR_BY_STATE["buggy"]
    if node.get("is_buggy_plots") is True:
        return _DOT_COLOR_BY_STATE["buggy_plots"]
    if node.get("is_buggy") is False:
        return _DOT_COLOR_BY_STATE["good"]
    return _DOT_COLOR_BY_STATE["pending"]


def render_tree_dot(
    nodes: list[dict[str, Any]],
    *,
    run_id: str,
    best_node_id: str | None,
) -> str:
    """Render a bfts_run as GraphViz dot text.

    No external dependencies. Output is self-contained and can be piped
    to ``dot -Tpng`` by operators or pasted into any dot viewer. Color
    legend embedded as a subgraph so the graph is readable standalone.
    """
    safe_run = run_id.replace("-", "_")
    lines = [
        f"digraph BFTS_{safe_run} {{",
        '  rankdir=TB;',
        '  node [style=filled, shape=box, fontname="Helvetica"];',
    ]
    for n in nodes:
        nid = n["node_id"]
        color = _node_color(n, best_node_id)
        metric = n.get("metric_json") or {}
        score = metric.get("final_value") if isinstance(metric, dict) else None
        score_label = f"\\n{score:.4g}" if isinstance(score, (int, float)) else ""
        label = f"{nid[:8]}\\n[{n.get('stage_name','?')}]{score_label}"
        lines.append(f'  "{nid}" [label="{label}", fillcolor="{color}"];')
    for n in nodes:
        if n.get("parent_node_id"):
            lines.append(f'  "{n["parent_node_id"]}" -> "{n["node_id"]}";')
    lines.append("  // legend")
    lines.append('  subgraph cluster_legend {')
    lines.append('    label="legend"; style=dashed;')
    for state, color in _DOT_COLOR_BY_STATE.items():
        lines.append(f'    "legend_{state}" [label="{state}", fillcolor="{color}"];')
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


async def write_tree_dot_artifact(
    pool: asyncpg.Pool,
    *,
    run_id: str,
    dot_text: str,
    anchor_node_id: str,
) -> None:
    """Persist tree.dot under the anchor (best or first) node.

    Uses the same ``bfts_artifacts`` upsert path as ``write_best_artifact``;
    ``relative_path`` = ``tree.dot``, ``kind`` = ``tree_viz``.
    """
    artifact_id = f"{run_id}:tree.dot"
    await pool.execute(
        """
        INSERT INTO bfts_artifacts (artifact_id, node_id, kind, relative_path, bytes)
        VALUES ($1, $2, 'tree_viz', 'tree.dot', $3)
        ON CONFLICT (node_id, relative_path) DO UPDATE SET bytes = EXCLUDED.bytes
        """,
        artifact_id, anchor_node_id, dot_text.encode("utf-8"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd overlay/workflows && uv run pytest tests/test_bfts_export.py -v`
Expected: all PASS.

- [ ] **Step 5: Wire into `bfts_tree.handler`**

After the `set_best` step in `bfts_tree.py` (around line 340), add:

```python
# Anchor the tree visualization on the best node when present, else on
# the first node so the artifact is queryable for failed runs too.
anchor_node_id = best["node_id"] if best else (
    final_nodes[0]["node_id"] if final_nodes else None
)
if anchor_node_id is not None:
    dot_text = render_tree_dot(
        final_nodes,
        run_id=inp.run_id,
        best_node_id=best["node_id"] if best else None,
    )
    await ctx.step(
        "write_tree_dot",
        lambda: write_tree_dot_artifact(
            pool, run_id=inp.run_id, dot_text=dot_text,
            anchor_node_id=anchor_node_id,
        ),
    )
```

Add `render_tree_dot, write_tree_dot_artifact` to the `_bfts_export` import.

- [ ] **Step 6: Add a tree-handler test asserting the artifact is written**

```python
# overlay/workflows/tests/test_bfts_tree_handler.py
@pytest.mark.asyncio
async def test_tree_dot_artifact_is_written_when_run_has_nodes():
    ctx = _MockContext(...)
    await handler(_input(max_iters=1, num_drafts=1, num_workers=1), ctx)
    assert "write_tree_dot" in ctx.step_names
    assert any("digraph" in c.dot_text for c in ctx.write_tree_dot_calls)
```

- [ ] **Step 7: Run all tests**

Run: `cd overlay/workflows && uv run pytest tests/test_bfts_export.py tests/test_bfts_tree_handler.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add overlay/workflows/_bfts_export.py \
        overlay/workflows/bfts_tree.py \
        overlay/workflows/tests/test_bfts_export.py \
        overlay/workflows/tests/test_bfts_tree_handler.py
git commit -m "feat(bfts): emit tree.dot artifact at end of every bfts_tree run

GraphViz dot text capturing the tree structure, node states, and metric
scores. Self-contained (no JS, no igraph dependency). Operators render
with 'dot -Tpng tree.dot -o tree.png'. Closes the visualization gap
identified vs. upstream tree_export.py without porting the full HTML
template."
```

---

# Task F.4: Multi-seed re-evaluation of best node

**Why:** A single-seed "best" Stage-1 node is brittle — small loss-function noise can flip the ranking between runs. Upstream's `multi_seed_eval` (`.scientist/ai_scientist/treesearch/parallel_agent.py:1261-1330`) re-runs the best node N times with different seeds, then aggregates final metrics. Research 02 line 597 tagged this "Phase 2 work" but it never made it into Phase 2 or Phase 4. This task ports it as a trailing step in `bfts_tree.handler`, opt-in via `num_seeds` config (default 0 to preserve current behavior).

**Approach:**
1. Schema: add `is_seed_node BOOLEAN NOT NULL DEFAULT FALSE` and `seed INT` to `bfts_nodes`. (`bfts_runs.seed` already exists for the tree-level seed; `bfts_nodes.seed` is per-node, only set when `is_seed_node=TRUE`.)
2. Selector: `_bfts_select.select_next` skips seed nodes — they're not selection candidates, just bookkeeping.
3. After `select_best` succeeds, fan out N copies of `bfts_expand_one` in a new "seed" mode that **bypasses the LLM** (re-uses parent's exact `code`) but still calls `exec_python` + metric parse.
4. Aggregate: compute mean + std of `final_value` across seed children; store on the best node's `metric_json` as `aggregate_mean` / `aggregate_std`. (No separate agg node needed for MVP.)

**Files:**
- Create: `overlay/services/api/db/migrations/20260526000002_add_seed_eval_columns.sql`
- Modify: `overlay/workflows/_bfts_state.py` — accept `is_seed_node`, `seed` in `insert_node`; add `list_seed_children(pool, *, parent_node_id)`; add `update_node_aggregate_metric(pool, *, node_id, aggregate)`.
- Modify: `overlay/workflows/_bfts_select.py` — `NodeRef.is_seed_node`; selector excludes seed nodes from drafts/leaves/good lists.
- Modify: `overlay/workflows/_bfts_config.py` — `SearchSettings.num_seeds: int = 0`; env `BFTS_NUM_SEEDS`.
- Modify: `overlay/workflows/_bfts_expand.py` — `ExpandContext.seed_override: int | None`; when set, skip draft/improve/debug LLM and run parent code with `seed=K` injected as a `np.random.seed(K)` + `random.seed(K)` + `torch.manual_seed(K)` preamble.
- Modify: `overlay/workflows/bfts_expand_one.py` — `Input.seed_override`, `Input.is_seed_node`; pass through.
- Modify: `overlay/workflows/bfts_tree.py` — after `set_best`, if `num_seeds > 0` and `best is not None`: fan out N seed children, wait, aggregate.
- Modify: `overlay/workflows/bfts_root.py` — forward `num_seeds`.
- Test files: `test_bfts_state.py`, `test_bfts_select.py`, `test_bfts_expand.py`, `test_bfts_tree_handler.py`, `test_bfts_config.py`.

- [ ] **Step 1: Write the migration**

```sql
-- overlay/services/api/db/migrations/20260526000002_add_seed_eval_columns.sql
-- migrate:up
-- Multi-seed re-evaluation of the best node (Sakana parity, F.4).
-- Adds two columns to bfts_nodes; existing rows default to is_seed_node=FALSE.
ALTER TABLE bfts_nodes
    ADD COLUMN is_seed_node BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN seed         INT;

CREATE INDEX bfts_nodes_seed_parent_idx
    ON bfts_nodes(parent_node_id)
    WHERE is_seed_node = TRUE;

-- migrate:down
DROP INDEX IF EXISTS bfts_nodes_seed_parent_idx;
ALTER TABLE bfts_nodes
    DROP COLUMN seed,
    DROP COLUMN is_seed_node;
```

Apply: `./.centaur/contrib/scripts/dbmate --set overlay up`

- [ ] **Step 2: Write the failing selector test**

```python
# overlay/workflows/tests/test_bfts_select.py
def test_select_next_skips_seed_nodes_in_all_categories():
    """Seed nodes are bookkeeping; they must not count as drafts, leaves,
    or good candidates."""
    rng = Random(0)
    cfg = SearchConfig(num_drafts=2, num_workers=2,
                       max_debug_depth=3, debug_prob=0.0)
    nodes = [
        # one real draft, good
        NodeRef(node_id="d1", parent_id=None, root_id="d1",
                is_buggy=False, is_buggy_plots=False,
                debug_depth=0, metric_score=0.1,
                stage_name="draft", is_leaf=True,
                is_seed_node=False),
        # seed child of d1 — must be ignored everywhere
        NodeRef(node_id="s1", parent_id="d1", root_id="d1",
                is_buggy=False, is_buggy_plots=False,
                debug_depth=0, metric_score=0.05,
                stage_name="draft", is_leaf=True,
                is_seed_node=True),
    ]
    selections = select_next(nodes=nodes, cfg=cfg, rng=rng)
    # Only one real draft, num_drafts=2 → first slot must be a None (new draft),
    # second slot must improve d1 (NOT s1, which is a better metric but is a
    # seed node).
    assert selections[0] is None
    assert selections[1] is not None and selections[1].node_id == "d1"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd overlay/workflows && uv run pytest tests/test_bfts_select.py::test_select_next_skips_seed_nodes_in_all_categories -v`
Expected: FAIL — `NodeRef` doesn't have `is_seed_node`.

- [ ] **Step 4: Update `_bfts_select.NodeRef` and helpers**

```python
# overlay/workflows/_bfts_select.py
@dataclass(frozen=True)
class NodeRef:
    # ... existing fields ...
    is_seed_node: bool = False  # F.4: bookkeeping, never selected
```

Update `_draft_nodes`, `_good_nodes`, `_buggy_leaf_nodes`, and the viability scan to filter `n for n in nodes if not n.is_seed_node` first.

Also update `bfts_tree._to_noderef` to read `is_seed_node` from the row data.

- [ ] **Step 5: Run selector tests**

Run: `cd overlay/workflows && uv run pytest tests/test_bfts_select.py -v`
Expected: all PASS.

- [ ] **Step 6: DAO updates and test**

```python
# overlay/workflows/_bfts_state.py — modify insert_node signature
async def insert_node(
    pool, *, node_id, run_id, parent_node_id, step, stage_name,
    plan, code, debug_depth=0,
    is_seed_node: bool = False,
    seed: int | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO bfts_nodes
            (node_id, run_id, parent_node_id, step, stage_name,
             plan, code, debug_depth, is_seed_node, seed)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        ON CONFLICT (node_id) DO NOTHING
        """,
        node_id, run_id, parent_node_id, step, stage_name,
        plan, code, debug_depth, is_seed_node, seed,
    )

async def list_seed_children(
    pool, *, parent_node_id: str,
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT node_id, seed, metric_json, is_buggy
        FROM bfts_nodes
        WHERE parent_node_id = $1 AND is_seed_node = TRUE
        ORDER BY seed
        """,
        parent_node_id,
    )
    return [dict(r) for r in rows]

async def update_node_aggregate_metric(
    pool, *, node_id: str, aggregate: dict[str, float],
) -> None:
    """Merge ``aggregate`` (e.g. {'aggregate_mean': 0.32, 'aggregate_std': 0.04})
    into the node's existing metric_json."""
    await pool.execute(
        """
        UPDATE bfts_nodes
        SET metric_json = COALESCE(metric_json, '{}'::jsonb) || $2::jsonb,
            updated_at = NOW()
        WHERE node_id = $1
        """,
        node_id, json.dumps(aggregate),
    )
```

Add unit tests for `list_seed_children` and `update_node_aggregate_metric` in `test_bfts_state.py` (one per function, mirror existing fakes).

- [ ] **Step 7: Implement seed-mode expansion**

In `_bfts_expand.py`:
1. Add `seed_override: int | None = None` to `ExpandContext`.
2. At top of `expand_node`, branch:

```python
if expand_ctx.seed_override is not None:
    return await _seed_re_execute(ctx, expand_ctx)
# ... existing draft/debug/improve dispatch ...
```

```python
async def _seed_re_execute(ctx, expand_ctx) -> dict:
    """Re-run the parent's exact code with deterministic seeds, no LLM.

    Replaces the {draft|debug|improve}_propose step with a synthetic
    'seed_propose' that injects a seeding preamble; everything else
    (exec, bug judge, metric parse, plot, VLM) runs unchanged.
    """
    parent = expand_ctx.parent_node
    if parent is None or not parent.get("code"):
        msg = "seed_override requires a parent node with executable code"
        raise ValueError(msg)
    seed = expand_ctx.seed_override
    preamble = (
        f"import random; random.seed({seed})\n"
        f"import numpy as np; np.random.seed({seed})\n"
        f"try:\n"
        f"    import torch; torch.manual_seed({seed})\n"
        f"except Exception:\n"
        f"    pass\n"
    )
    seeded_code = preamble + parent["code"]
    plan = f"Seed re-evaluation with seed={seed}"
    # Persist via a stable step name so retry is idempotent.
    exec_result = await ctx.step(
        "seed_exec",
        lambda: exec_python(
            sandbox_id=expand_ctx.sandbox_id,
            code=seeded_code,
            timeout_s=3600,
            working_dir=expand_ctx.working_dir,
        ),
    )
    # Reuse the same metric_parse + plot + bug_judge steps as the
    # main path. (Refactor the relevant blocks into helpers if needed.)
    # ...
    return {"plan": plan, "code": seeded_code, "is_buggy": ...}
```

(Detailed implementation: extract the post-exec steps — `bug_judge`, `metric_parse_propose/exec/extract`, `plot_propose/exec`, `collect_artifacts`, `select_best_plots`, `vlm_analyze` — into a `_post_exec_pipeline(ctx, expand_ctx, exec_result)` helper called by both branches.)

- [ ] **Step 8: Wire seed fan-out into `bfts_tree`**

After the `set_best` step in `bfts_tree.handler`, replace the simple `return` block with:

```python
if best is not None and search.num_seeds > 0:
    seed_children = []
    for seed_idx in range(search.num_seeds):
        seed_node_id = uuid4().hex
        seed_run_id = f"{inp.run_id}:seed:{seed_idx}"
        await ctx.step(
            f"insert_seed_node_{seed_idx}",
            lambda nid=seed_node_id, s=seed_idx: insert_node(
                pool, node_id=nid, run_id=inp.run_id,
                parent_node_id=best["node_id"],
                step=99000 + s,  # seed steps live in a reserved range
                stage_name="seed",
                plan=f"seed re-eval {s}",
                code=best["code"],
                is_seed_node=True,
                seed=s,
            ),
        )
        child = await ctx.start_workflow(
            f"start_seed_{seed_idx}",
            workflow_name="bfts_expand_one",
            run_input={
                "run_id": inp.run_id,
                "node_id": seed_node_id,
                "sandbox_id": inp.sandbox_id,
                "working_dir": f"seed_{seed_idx}",
                "parent_node": best,
                "idea": inp.idea,
                "seed_override": seed_idx,
                "is_seed_node": True,
                # llm fields irrelevant in seed mode but keep wire-compatible
                "llm_api_key_secret": llm.llm_api_key_secret,
                "draft_model": llm.draft_model,
                "feedback_model": llm.feedback_model,
                "vlm_model": llm.vlm_model,
            },
            trigger_key=seed_run_id,
            eager_start=True,
        )
        seed_children.append((seed_node_id, child))
    for nid, child in seed_children:
        await ctx.wait_for_workflow(f"wait_seed_{nid}", run_id=child["run_id"])
    seed_rows = await ctx.step(
        "list_seed_children",
        lambda: list_seed_children(pool, parent_node_id=best["node_id"]),
    )
    aggregate = _aggregate_seed_metrics(seed_rows)
    if aggregate is not None:
        await ctx.step(
            "write_aggregate_metric",
            lambda: update_node_aggregate_metric(
                pool, node_id=best["node_id"], aggregate=aggregate,
            ),
        )
```

Add `_aggregate_seed_metrics` helper at module bottom:

```python
def _aggregate_seed_metrics(seed_rows: list[dict]) -> dict[str, float] | None:
    """Compute mean/std of ``final_value`` across non-buggy seed children.

    Returns ``None`` if no seed child produced a metric — no aggregate to
    write. Excludes buggy children so a single seed crash doesn't poison
    the aggregate.
    """
    values: list[float] = []
    for r in seed_rows:
        if r.get("is_buggy"):
            continue
        m = r.get("metric_json") or {}
        v = m.get("final_value") if isinstance(m, dict) else None
        if isinstance(v, (int, float)):
            values.append(float(v))
    if not values:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return {
        "aggregate_mean": mean,
        "aggregate_std": var ** 0.5,
        "aggregate_n": float(len(values)),
    }
```

- [ ] **Step 9: Add a tree-handler test for seed fan-out**

```python
# overlay/workflows/tests/test_bfts_tree_handler.py
@pytest.mark.asyncio
async def test_num_seeds_triggers_seed_fan_out_after_best_selected():
    """num_seeds=3 → 3 seed children fanned out, awaited, and aggregate
    written on the best node."""
    ctx = _MockContext(num_seeds=3, ...)
    await handler(_input(num_seeds=3, ...), ctx)
    assert ctx.start_workflow_calls.count("bfts_expand_one") >= 3
    assert "write_aggregate_metric" in ctx.step_names
    assert any(call.aggregate.get("aggregate_n") == 3.0 for call in ctx.write_aggregate_metric_calls)

@pytest.mark.asyncio
async def test_num_seeds_zero_skips_seed_fan_out():
    ctx = _MockContext(num_seeds=0, ...)
    await handler(_input(num_seeds=0, ...), ctx)
    assert "write_aggregate_metric" not in ctx.step_names
```

- [ ] **Step 10: Run full BFTS test suite**

Run: `cd overlay/workflows && uv run pytest tests/ -v -k "bfts"`
Expected: all PASS.

- [ ] **Step 11: Commit**

```bash
git add overlay/services/api/db/migrations/20260526000002_add_seed_eval_columns.sql \
        overlay/workflows/_bfts_state.py \
        overlay/workflows/_bfts_select.py \
        overlay/workflows/_bfts_config.py \
        overlay/workflows/_bfts_expand.py \
        overlay/workflows/bfts_expand_one.py \
        overlay/workflows/bfts_tree.py \
        overlay/workflows/bfts_root.py \
        overlay/workflows/tests/
git commit -m "feat(bfts): multi-seed re-evaluation of best node (opt-in via num_seeds)

Ports Sakana's multi_seed_eval (parallel_agent.py:1261-1330) as a trailing
step in bfts_tree. After set_best, if num_seeds > 0, fan out N copies of
bfts_expand_one in a seed-only mode that bypasses the LLM and re-runs the
best node's code with deterministic seeds (random/numpy/torch). Aggregate
mean/std of final_value across non-buggy seed children and merge into the
best node's metric_json. Default num_seeds=0 preserves Phase 4 behavior;
opt in via BFTS_NUM_SEEDS or Input.num_seeds."
```

---

# Task F.5: In-LLM-call retry/backoff in `_bfts_llm`

**Why:** Every LLM call in the expansion pipeline currently raises `RuntimeError` on the first non-2xx (`overlay/workflows/_bfts_llm.py:127, 163, 193, 215`). A single 429 from rate limits or a transient 503 kills a node, which then needs the next iteration's debug pass to recover. Upstream wraps every call in `backoff.on_exception` to a 60s ceiling (`.scientist/ai_scientist/treesearch/backend/utils.py`). Per-call retry is cheap and orthogonal to step-level Centaur retry: step retry covers full-step replay; per-call retry smooths over transient API blips without a full re-checkpoint.

**Approach:** Hand-rolled exponential backoff inside `_bfts_llm.py`. No new package dependency (avoids pulling `backoff` or `tenacity` into the overlay; matches the project's "minimal deps" posture). 4 attempts, sleeps 1s → 2s → 4s → 8s. Retry on 429, 500, 502, 503, 504, and `httpx.RequestError` (network errors). Non-retryable on 4xx (other than 429).

**Files:**
- Modify: `overlay/workflows/_bfts_llm.py` — add `_post_with_retry(client, url, *, json, headers, max_attempts=4)`; replace the four inline `client.post(...)` sites.
- Modify: `overlay/workflows/tests/test_bfts_llm.py` — three new tests: retry-on-429-then-200, no-retry-on-400, max-attempts-then-raise.

- [ ] **Step 1: Write the failing retry tests**

```python
# overlay/workflows/tests/test_bfts_llm.py
@pytest.mark.asyncio
async def test_post_with_retry_succeeds_after_one_429(monkeypatch):
    """A single 429 is followed by a successful 200; total: 2 attempts,
    one sleep of 1s."""
    sleeps: list[float] = []
    monkeypatch.setattr("asyncio.sleep", lambda s: sleeps.append(s) or _aiosleep_zero())

    responses = iter([
        _MockResponse(429, '{"error":"rate_limit"}'),
        _MockResponse(200, '{"choices":[{"message":{"content":"ok"}}]}'),
    ])
    async def _fake_post(self, url, json=None, headers=None):
        return next(responses)
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    text = await call_for_text(LLMCall(model="gpt-4o", temperature=0.0,
                                       api_key="k", prompt="hi"))
    assert text == "ok"
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_post_with_retry_does_not_retry_on_400(monkeypatch):
    """Bad-request 4xx other than 429 raises immediately, no retry."""
    sleeps: list[float] = []
    monkeypatch.setattr("asyncio.sleep", lambda s: sleeps.append(s) or _aiosleep_zero())

    async def _fake_post(self, url, json=None, headers=None):
        return _MockResponse(400, '{"error":"bad_request"}')
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    with pytest.raises(RuntimeError, match="LLM call failed: 400"):
        await call_for_text(LLMCall(model="gpt-4o", temperature=0.0,
                                    api_key="k", prompt="hi"))
    assert sleeps == []  # no retries


@pytest.mark.asyncio
async def test_post_with_retry_gives_up_after_max_attempts(monkeypatch):
    """Four 503s in a row → RuntimeError after 4 attempts, 3 sleeps."""
    sleeps: list[float] = []
    monkeypatch.setattr("asyncio.sleep", lambda s: sleeps.append(s) or _aiosleep_zero())

    async def _fake_post(self, url, json=None, headers=None):
        return _MockResponse(503, '{"error":"unavailable"}')
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    with pytest.raises(RuntimeError, match="LLM call failed: 503"):
        await call_for_text(LLMCall(model="gpt-4o", temperature=0.0,
                                    api_key="k", prompt="hi"))
    assert sleeps == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_post_with_retry_handles_network_errors(monkeypatch):
    """httpx.RequestError is retryable."""
    sleeps: list[float] = []
    monkeypatch.setattr("asyncio.sleep", lambda s: sleeps.append(s) or _aiosleep_zero())

    attempts = [httpx.ConnectError("conn refused"),
                httpx.ConnectError("conn refused"),
                _MockResponse(200, '{"choices":[{"message":{"content":"ok"}}]}')]
    async def _fake_post(self, url, json=None, headers=None):
        outcome = attempts.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    text = await call_for_text(LLMCall(model="gpt-4o", temperature=0.0,
                                       api_key="k", prompt="hi"))
    assert text == "ok"
    assert sleeps == [1.0, 2.0]
```

(`_aiosleep_zero()` is a tiny helper that returns an awaitable resolving to `None` immediately so the test doesn't actually sleep. Add it once near the existing test helpers.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd overlay/workflows && uv run pytest tests/test_bfts_llm.py -v -k "retry"`
Expected: all four FAIL — retry helper not implemented.

- [ ] **Step 3: Implement `_post_with_retry`**

```python
# overlay/workflows/_bfts_llm.py — append constants near the top
import asyncio

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRY_MAX_ATTEMPTS = 4
_RETRY_BASE_DELAY_S = 1.0


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    json: dict[str, Any],
    headers: dict[str, str],
) -> httpx.Response:
    """POST with exponential backoff on transient errors.

    Retries on 429 / 5xx and ``httpx.RequestError`` (connect / read /
    write errors). Non-retryable 4xx (incl. 401/403/404) raises after a
    single attempt. Sleeps 1s, 2s, 4s between attempts. Max 4 total
    attempts (3 retries).

    Mirrors Sakana's ``backoff.on_exception(... max_time=60)`` posture
    without a new dep — total worst-case wait is 1+2+4 = 7s + 4 calls.
    """
    last_exc: Exception | None = None
    for attempt in range(_RETRY_MAX_ATTEMPTS):
        try:
            resp = await client.post(url, json=json, headers=headers)
        except httpx.RequestError as e:
            last_exc = e
            if attempt + 1 >= _RETRY_MAX_ATTEMPTS:
                raise RuntimeError(f"LLM call network error: {e}") from e
            await asyncio.sleep(_RETRY_BASE_DELAY_S * (2 ** attempt))
            continue
        if resp.status_code in _RETRY_STATUS and attempt + 1 < _RETRY_MAX_ATTEMPTS:
            await asyncio.sleep(_RETRY_BASE_DELAY_S * (2 ** attempt))
            continue
        return resp
    # Loop exhausted with retryable failures only — return last response so
    # the caller raises the standard "LLM call failed: <code>" error.
    assert resp is not None  # noqa: S101 — narrowed by loop logic
    return resp
```

- [ ] **Step 4: Replace the four inline `client.post` sites**

Each of `_call_with_function_openai`, `_call_with_function_anthropic`, `_call_for_text_openai`, `_call_for_text_anthropic` currently has:

```python
async with httpx.AsyncClient(timeout=call.timeout) as client:
    resp = await client.post(URL, json=body, headers=HEADERS)
if resp.status_code != 200:
    raise RuntimeError(f"LLM call failed: {resp.status_code} {resp.text[:500]}")
```

Replace `client.post(...)` with `_post_with_retry(client, URL, json=body, headers=HEADERS)`. The non-2xx raise stays — `_post_with_retry` returns the last response unchanged so non-retryable failures and exhausted retries both flow through the existing error path.

- [ ] **Step 5: Run all `_bfts_llm` tests**

Run: `cd overlay/workflows && uv run pytest tests/test_bfts_llm.py -v`
Expected: all PASS, including the existing happy-path and error tests (no behavior change for 200 / 400 responses).

- [ ] **Step 6: Commit**

```bash
git add overlay/workflows/_bfts_llm.py \
        overlay/workflows/tests/test_bfts_llm.py
git commit -m "feat(bfts): exponential backoff on transient LLM API failures

Add a 4-attempt retry loop to _bfts_llm with 1s/2s/4s exponential delay,
matching upstream backoff.on_exception max_time=60. Retries on 429, 5xx,
and httpx.RequestError; non-retryable 4xx raises immediately. No new
package dependency — hand-rolled with asyncio.sleep. Closes the
'in-LLM-call retry/backoff' gap identified in the BFTS comparison."
```

---

## Self-review

**Spec coverage:** Each of the five gaps in the comparison summary maps to exactly one task (F.1–F.5). The intentionally-rejected gaps (LLM best-node arbitration, Stages 2–4 curriculum, GPU split, SandboxTemplate) are explicitly excluded in §Background.

**Placeholder scan:** No "TODO", "implement later", or "similar to Task N" left in the task bodies. Every code step has a complete code block; every command step has the exact command.

**Type consistency:**
- `mark_node_failed(pool, *, node_id, exc_type, exc_info, analysis)` — same shape used in F.1 step 3 and step 7.
- `list_recent_node_summaries(pool, *, run_id, limit, exclude_node_id)` — same kwargs in F.2 step 3, step 9, and step 11.
- `render_tree_dot(nodes, *, run_id, best_node_id)` — same signature in F.3 step 1, step 3, and step 5.
- `NodeRef.is_seed_node` — added in F.4 step 4, referenced in step 2 and step 8.
- `_post_with_retry(client, url, *, json, headers)` — same signature in F.5 step 3 and step 4.

**Migration sanity:** F.4's migration follows the existing overlay pattern (`overlay/services/api/db/migrations/20260525000001_add_bfts_tables.sql`, `20260526000001_add_bfts_hyperparams.sql`); next sequence number is `20260526000002`. Down migration drops the index and columns in reverse order.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-26-bfts-stage1-fidelity-followup.md`. Two execution options:

**1. Subagent-driven (recommended)** — Dispatch one subagent per task with `superpowers:subagent-driven-development`; tasks F.1, F.3, and F.5 are fully independent and can run in parallel worktrees.

**2. Inline execution** — Execute tasks in this session using `superpowers:executing-plans` with checkpoints between tasks.

Which approach?

