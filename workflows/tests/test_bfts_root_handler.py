"""Test: bfts_root handler input parsing, sandbox_id format, and teardown."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bfts_root import WORKFLOW_NAME, Input, _sandbox_id

from workflows.tests._mocks import MockPool


def test_workflow_name() -> None:
    assert WORKFLOW_NAME == "bfts_root"


def test_input_required_idea() -> None:
    from _bfts_config import resolve_llm_settings

    inp = Input(idea={"name": "test", "Title": "X"})
    assert inp.idea["name"] == "test"
    # Phase 4c.4: search-policy fields default to None so the resolver
    # chain (Input → DB → env → default) actually reaches the lower
    # tiers; the dataclass shouldn't pre-empt with hardcoded defaults.
    assert inp.num_drafts is None
    assert inp.max_iters == 20
    llm = resolve_llm_settings(
        draft_model=inp.draft_model,
        feedback_model=inp.feedback_model,
        vlm_model=inp.vlm_model,
        llm_api_key_secret=inp.llm_api_key_secret,
    )
    assert llm.llm_api_key_secret == "ANTHROPIC_API_KEY"
    assert llm.draft_model == "claude-sonnet-4-20250514"


def test_sandbox_id_is_deterministic_and_run_scoped() -> None:
    assert _sandbox_id(run_id="run-abc", tree_idx=0) == "bfts-run-abc-tree-0"
    assert _sandbox_id(run_id="run-abc", tree_idx=2) == "bfts-run-abc-tree-2"
    # Different run -> different sandbox_id.
    assert _sandbox_id(run_id="run-def", tree_idx=0) == "bfts-run-def-tree-0"


def test_sandbox_id_replaces_underscores_for_rfc1123() -> None:
    """Live workflow runs use ``wfr_<hex>`` ids whose underscore violates
    RFC 1123 (K8s metadata.name). The constructor MUST normalize the
    underscores to dashes so ``create_sandbox`` doesn't get 422'd.
    Regression test for the 17:20 UTC bfts-toy-run failure.
    """
    sid = _sandbox_id(run_id="wfr_4526037fdbfa4c0d", tree_idx=0)
    assert "_" not in sid, f"sandbox name still contains underscore: {sid!r}"
    assert sid == "bfts-wfr-4526037fdbfa4c0d-tree-0"


def test_sandbox_id_is_rfc1123_compliant() -> None:
    """End-to-end shape check: alphanumeric + dash + dot only,
    starts and ends with alphanumeric, length <= 253. Catches future
    changes that introduce other forbidden characters (uppercase,
    underscore, leading dash, etc.).
    """
    import re

    sid = _sandbox_id(run_id="wfr_4526037fdbfa4c0d", tree_idx=99)
    pattern = re.compile(
        r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$"
    )
    assert pattern.match(sid), f"not RFC1123: {sid!r}"
    assert len(sid) <= 253


# --- Teardown contract tests ---------------------------------------------
#
# bfts_root provisions one Sandbox CR per child tree; if anything raises
# between create_sandbox and the end of the body the per-tree Sandbox can
# orphan. The handler must therefore wrap its body in a try/finally and
# run stop_sandbox for every sandbox it successfully created — even when
# child workflows fail, individual stops fail, or a start_workflow call
# raises after its create_sandbox already landed.


class _RootCtx:
    """Stand-in WorkflowContext for bfts_root.

    ``step``/``start_workflow``/``wait_for_workflow`` are minimal recorders
    that consult ``fail_on_step`` / ``fail_on_wait`` / ``fail_on_start``
    to inject targeted exceptions. Tool method calls are routed through
    AsyncMock stubs hung off ``tools.bfts_executor`` so individual
    create/stop_sandbox invocations are observable.
    """

    def __init__(
        self,
        *,
        run_id: str = "run-1",
        pool: Any | None = None,
    ) -> None:
        self.run_id = run_id
        self.tools = _Tools()
        self._pool = pool if pool is not None else MockPool(fetchrow_result=None)
        self.step_calls: list[str] = []
        self.start_workflow_calls: list[dict[str, Any]] = []
        self.wait_for_workflow_calls: list[str] = []
        self.logs: list[tuple[str, dict[str, Any]]] = []
        self.fail_on_step: dict[str, BaseException] = {}
        self.fail_on_start: dict[str, BaseException] = {}
        self.fail_on_wait: dict[str, BaseException] = {}

    async def step(self, name: str, fn: Any) -> Any:
        self.step_calls.append(name)
        if name in self.fail_on_step:
            raise self.fail_on_step[name]
        out = fn() if callable(fn) else fn
        if inspect.iscoroutine(out):
            out = await out
        return out

    async def start_workflow(
        self,
        name: str,
        *,
        workflow_name: str,
        run_input: dict[str, Any],
        trigger_key: str,
        eager_start: bool,
    ) -> dict[str, Any]:
        self.start_workflow_calls.append(
            {
                "name": name,
                "workflow_name": workflow_name,
                "run_input": run_input,
                "trigger_key": trigger_key,
                "eager_start": eager_start,
            }
        )
        if name in self.fail_on_start:
            raise self.fail_on_start[name]
        return {"run_id": run_input["run_id"]}

    async def wait_for_workflow(self, name: str, *, run_id: str) -> dict[str, Any]:
        self.wait_for_workflow_calls.append(run_id)
        if run_id in self.fail_on_wait:
            raise self.fail_on_wait[run_id]
        return {"run_id": run_id, "status": "completed"}

    def log(self, event: str, **kwargs: Any) -> None:
        self.logs.append((event, kwargs))


class _Tools:
    def __init__(self) -> None:
        self.bfts_executor = _BftsExecutorStub()


class _BftsExecutorStub:
    def __init__(self) -> None:
        self.create_sandbox = AsyncMock(return_value={"sandbox_id": "stub"})
        self.stop_sandbox = AsyncMock(return_value=None)


def _input(num_drafts: int = 2, idea: dict[str, Any] | None = None) -> Input:
    # Default to a plan-complete idea so existing tests don't accidentally
    # exercise the F.6 default-idea substitution path. Tests that exercise
    # the substitution explicitly pass ``idea={}`` or omit required keys.
    if idea is None:
        idea = {
            "Name": "toy-test",
            "Title": "Toy test idea",
            "Short Hypothesis": "Tests run with a complete idea by default.",
            "Experiments": ["Run the handler."],
        }
    return Input(
        idea=idea,
        num_drafts=num_drafts,
        num_workers=1,
        max_debug_depth=3,
        debug_prob=0.5,
        max_iters=1,
    )


@pytest.mark.asyncio
async def test_happy_path_runs_all_teardowns_and_returns_results() -> None:
    """Baseline: nothing fails; every create/start/wait/stop step runs once
    and the return shape carries the F.6 verification surface (trees,
    idea_used, idea_was_defaulted, resolved_search_config, sources)."""
    import bfts_root

    ctx = _RootCtx()
    out = await bfts_root.handler(_input(num_drafts=2), ctx)

    assert [c["tree_index"] for c in out["trees"]] == [0, 1]
    assert len(out["trees"]) == 2
    assert ctx.tools.bfts_executor.create_sandbox.await_count == 2
    assert ctx.tools.bfts_executor.stop_sandbox.await_count == 2
    assert {"stop_sandbox_0", "stop_sandbox_1"}.issubset(set(ctx.step_calls))
    # F.6 contract: the return value MUST carry these keys so a Slack
    # agent can ``call workflow get <run_id>`` and read them without DB
    # access (sandbox tokens cannot run direct queries).
    for key in (
        "run_id", "idea_used", "idea_was_defaulted",
        "resolved_search_config", "sources", "trees",
    ):
        assert key in out, f"missing return-value key: {key!r}"


@pytest.mark.asyncio
async def test_teardown_runs_when_child_workflow_fails() -> None:
    """If wait_for_workflow raises mid-loop, every provisioned sandbox
    must still be torn down before the exception propagates."""
    import bfts_root

    ctx = _RootCtx()
    ctx.fail_on_wait = {"run-1:tree:0": RuntimeError("child tree failed")}

    with pytest.raises(RuntimeError, match="child tree failed"):
        await bfts_root.handler(_input(num_drafts=2), ctx)

    assert ctx.tools.bfts_executor.stop_sandbox.await_count == 2
    assert {"stop_sandbox_0", "stop_sandbox_1"}.issubset(set(ctx.step_calls))


@pytest.mark.asyncio
async def test_teardown_continues_when_one_stop_sandbox_fails() -> None:
    """One stuck Sandbox CR must not block the others. On happy-path
    completion the handler still attempts every stop; if any stop raised
    after the body succeeded, an aggregated RuntimeError is surfaced."""
    import bfts_root

    ctx = _RootCtx()
    ctx.fail_on_step = {"stop_sandbox_0": RuntimeError("CR stuck terminating")}

    with pytest.raises(RuntimeError, match="teardown failed"):
        await bfts_root.handler(_input(num_drafts=3), ctx)

    # All three stops attempted, in tree-index order.
    stop_calls = [s for s in ctx.step_calls if s.startswith("stop_sandbox_")]
    assert stop_calls == ["stop_sandbox_0", "stop_sandbox_1", "stop_sandbox_2"]


@pytest.mark.asyncio
async def test_body_error_takes_precedence_over_teardown_error() -> None:
    """When the body already raised, teardown errors are logged but the
    original exception owns propagation — we don't mask the root cause."""
    import bfts_root

    ctx = _RootCtx()
    ctx.fail_on_wait = {"run-1:tree:0": RuntimeError("body boom")}
    ctx.fail_on_step = {"stop_sandbox_1": RuntimeError("teardown boom")}

    with pytest.raises(RuntimeError, match="body boom"):
        await bfts_root.handler(_input(num_drafts=2), ctx)

    # Both stops were still attempted.
    assert {"stop_sandbox_0", "stop_sandbox_1"}.issubset(set(ctx.step_calls))
    # Teardown errors surfaced via structured log so operators can find them.
    teardown_logs = [kw for ev, kw in ctx.logs if ev == "bfts_root_teardown_errors"]
    assert len(teardown_logs) == 1
    assert any("teardown boom" in repr(e["error"]) for e in teardown_logs[0]["errors"])


@pytest.mark.asyncio
async def test_partially_created_sandbox_is_torn_down() -> None:
    """If start_workflow raises AFTER create_sandbox succeeded, the
    just-created Sandbox must be tracked for teardown even though it
    never made it into the ``children`` list as a fully-started tree."""
    import bfts_root

    ctx = _RootCtx()
    ctx.fail_on_start = {"start_tree_1": RuntimeError("start_workflow failed")}

    with pytest.raises(RuntimeError, match="start_workflow failed"):
        await bfts_root.handler(_input(num_drafts=3), ctx)

    # create_sandbox_0, _1, but NOT _2 (we never reached that iteration).
    assert ctx.tools.bfts_executor.create_sandbox.await_count == 2
    # Both created sandboxes get torn down, including the orphan from
    # tree_index=1 whose start_workflow failed.
    assert ctx.tools.bfts_executor.stop_sandbox.await_count == 2
    assert {"stop_sandbox_0", "stop_sandbox_1"}.issubset(set(ctx.step_calls))


@pytest.mark.asyncio
async def test_failed_create_sandbox_does_not_attempt_stop() -> None:
    """If create_sandbox itself raises, there's no CR to clean up for
    that tree — we must not invoke stop_sandbox for it."""
    import bfts_root

    ctx = _RootCtx()
    ctx.fail_on_step = {"create_sandbox_1": RuntimeError("create failed")}

    with pytest.raises(RuntimeError, match="create failed"):
        await bfts_root.handler(_input(num_drafts=3), ctx)

    # Only tree 0 was created; only tree 0 gets a stop.
    assert ctx.tools.bfts_executor.create_sandbox.await_count == 1
    assert ctx.tools.bfts_executor.stop_sandbox.await_count == 1
    stops = [s for s in ctx.step_calls if s.startswith("stop_sandbox_")]
    assert stops == ["stop_sandbox_0"]


# --- Phase 4c.4: search-config resolution + child-fan-out propagation ---
#
# After 4c.4 ``bfts_root.handler`` resolves search-policy fields once via
# ``resolve_search_config(ctx._pool, ...)`` (Input → ``bfts_hyperparams``
# row → ``BFTS_*`` env → module default) and threads the resolved values
# into every child ``bfts_tree`` ``run_input`` so all siblings share one
# config snapshot. The reflection-tuned DB row therefore takes effect on
# the next run without operator action.


@pytest.mark.asyncio
async def test_handler_resolves_search_config_via_pool(monkeypatch) -> None:
    """The handler must consult bfts_hyperparams via ctx._pool — without
    that read the resolver chain skips the DB layer and reflection-tuned
    values are silently ignored."""
    import bfts_root

    pool = MockPool(fetchrow_result=None)
    ctx = _RootCtx(pool=pool)

    await bfts_root.handler(_input(num_drafts=2), ctx)

    assert len(pool.fetchrow_calls) == 1
    query, _args = pool.fetchrow_calls[0]
    assert "bfts_hyperparams" in query


@pytest.mark.asyncio
async def test_handler_forwards_resolved_search_config_to_each_child(
    monkeypatch,
) -> None:
    """When Input leaves all four search fields None, the
    ``bfts_hyperparams`` row's values flow through to every child's
    run_input — this is the reflection-tuning hand-off."""
    import bfts_root

    monkeypatch.delenv("BFTS_DEBUG_PROB", raising=False)
    monkeypatch.delenv("BFTS_MAX_DEBUG_DEPTH", raising=False)
    monkeypatch.delenv("BFTS_NUM_DRAFTS", raising=False)
    monkeypatch.delenv("BFTS_NUM_WORKERS", raising=False)
    monkeypatch.delenv("BFTS_METRIC_REDUCER", raising=False)

    pool = MockPool(
        fetchrow_result={
            "debug_prob": 0.7,
            "max_debug_depth": 4,
            "num_drafts": 2,
            "num_workers": 5,
            "metric_reducer": "min",
        }
    )
    ctx = _RootCtx(pool=pool)

    inp = Input(idea={"name": "toy"}, max_iters=1)  # all four overrides None
    await bfts_root.handler(inp, ctx)

    # DB row's num_drafts=2 → 2 trees fanned out.
    assert len(ctx.start_workflow_calls) == 2
    for call in ctx.start_workflow_calls:
        ri = call["run_input"]
        assert ri["debug_prob"] == 0.7
        assert ri["max_debug_depth"] == 4
        assert ri["num_workers"] == 5
        assert ri["metric_reducer"] == "min"


@pytest.mark.asyncio
async def test_handler_input_override_propagates_to_children(
    monkeypatch,
) -> None:
    """Explicit Input override beats the bfts_hyperparams DB row;
    operator-supplied values always win."""
    import bfts_root

    monkeypatch.delenv("BFTS_DEBUG_PROB", raising=False)
    monkeypatch.delenv("BFTS_NUM_DRAFTS", raising=False)
    monkeypatch.delenv("BFTS_NUM_WORKERS", raising=False)

    pool = MockPool(
        fetchrow_result={
            "debug_prob": 0.7,
            "max_debug_depth": 4,
            "num_drafts": 2,
            "num_workers": 5,
            "metric_reducer": "min",
        }
    )
    ctx = _RootCtx(pool=pool)

    inp = Input(
        idea={"name": "toy"},
        num_drafts=3,  # override → 3 trees, not the DB row's 2
        debug_prob=0.1,  # override → 0.1, not 0.7
        max_iters=1,
    )
    await bfts_root.handler(inp, ctx)

    assert len(ctx.start_workflow_calls) == 3
    for call in ctx.start_workflow_calls:
        ri = call["run_input"]
        assert ri["debug_prob"] == 0.1
        # Non-overridden fields fall through to the DB row.
        assert ri["max_debug_depth"] == 4
        assert ri["num_workers"] == 5
        assert ri["metric_reducer"] == "min"


@pytest.mark.asyncio
async def test_handler_uses_resolved_num_drafts_for_fan_out_count(
    monkeypatch,
) -> None:
    """``num_drafts`` is the fan-out count: N trees, each with one root.
    The handler must use the resolved value (not the Input default) so a
    None Input + DB-tuned value still controls the fan-out width."""
    import bfts_root

    monkeypatch.delenv("BFTS_NUM_DRAFTS", raising=False)
    pool = MockPool(
        fetchrow_result={
            "debug_prob": 0.5,
            "max_debug_depth": 3,
            "num_drafts": 4,  # → 4 trees
            "num_workers": 1,
            "metric_reducer": "mean",
        }
    )
    ctx = _RootCtx(pool=pool)

    inp = Input(idea={"name": "toy"}, max_iters=1)  # num_drafts=None
    await bfts_root.handler(inp, ctx)

    assert len(ctx.start_workflow_calls) == 4
    assert ctx.tools.bfts_executor.create_sandbox.await_count == 4


@pytest.mark.asyncio
async def test_handler_logs_resolved_search_config(monkeypatch) -> None:
    """Observability: the resolved search-config snapshot must be logged
    so operators can see what knobs each run actually used."""
    import bfts_root

    pool = MockPool(
        fetchrow_result={
            "debug_prob": 0.4,
            "max_debug_depth": 2,
            "num_drafts": 2,
            "num_workers": 3,
            "metric_reducer": "mean",
            "created_by": "reflection",
        }
    )
    ctx = _RootCtx(pool=pool)

    await bfts_root.handler(Input(idea={"name": "toy"}, max_iters=1), ctx)

    resolved_logs = [
        kw for ev, kw in ctx.logs if ev == "bfts_root_resolved_search_config"
    ]
    assert len(resolved_logs) == 1
    log_kw = resolved_logs[0]
    assert log_kw["debug_prob"] == 0.4
    assert log_kw["num_drafts"] == 2
    assert log_kw["num_workers"] == 3
    assert log_kw["metric_reducer"] == "mean"


@pytest.mark.asyncio
async def test_handler_logs_resolved_sources(monkeypatch) -> None:
    """The structured log must include a ``sources`` dict alongside the
    resolved values so an operator postmortem can see which tier won
    each field — the same provenance that lands in ``bfts_runs.config_json``."""
    import bfts_root

    monkeypatch.delenv("BFTS_DEBUG_PROB", raising=False)
    monkeypatch.delenv("BFTS_MAX_DEBUG_DEPTH", raising=False)
    monkeypatch.delenv("BFTS_NUM_DRAFTS", raising=False)
    monkeypatch.setenv("BFTS_NUM_WORKERS", "8")
    monkeypatch.delenv("BFTS_METRIC_REDUCER", raising=False)

    pool = MockPool(
        fetchrow_result={
            "debug_prob": 0.4,
            "max_debug_depth": 2,
            "num_drafts": 2,
            "num_workers": None,
            "metric_reducer": "mean",
        }
    )
    ctx = _RootCtx(pool=pool)

    inp = Input(
        idea={"name": "toy"},
        debug_prob=0.9,  # Input override
        max_iters=1,
    )
    await bfts_root.handler(inp, ctx)

    resolved_logs = [
        kw for ev, kw in ctx.logs if ev == "bfts_root_resolved_search_config"
    ]
    assert len(resolved_logs) == 1
    sources = resolved_logs[0]["sources"]
    assert sources["debug_prob"] == "input"  # operator override
    assert sources["max_debug_depth"] == "hyperparams"  # DB row
    assert sources["num_drafts"] == "hyperparams"  # DB row
    assert sources["num_workers"] == "env"  # DB null → env
    assert sources["metric_reducer"] == "hyperparams"  # DB row


# ---------------------------------------------------------------------------
# F.6.1: default-idea substitution.
#
# Slack-driven smoke runs typically ship ``idea={}`` (or a tiny partial
# dict) because the Slack agent doesn't synthesize an idea up-front. The
# resulting ``## Idea`` markdown block in ``_propose_prompt`` would be
# empty → degenerate drafts that burn LLM budget. The handler must
# substitute the baked-in toy idea (``_DEFAULT_SMOKE_IDEA``) and log the
# substitution so it's auditable from workflow logs.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_substitutes_default_idea_when_empty() -> None:
    """``idea={}`` → handler swaps in ``_DEFAULT_SMOKE_IDEA`` and routes
    that idea into every child tree, sets ``idea_was_defaulted=True`` in
    the return value, and emits a structured ``bfts_root_using_default_idea``
    log with the list of missing fields."""
    import bfts_root

    ctx = _RootCtx()
    out = await bfts_root.handler(_input(num_drafts=2, idea={}), ctx)

    # Every child tree receives the substituted idea verbatim.
    assert len(ctx.start_workflow_calls) == 2
    for call in ctx.start_workflow_calls:
        ri = call["run_input"]
        assert ri["idea"] == bfts_root._DEFAULT_SMOKE_IDEA

    # Return value reflects the substitution so a Slack agent can see it
    # by ``call workflow get <run_id>`` without DB access.
    assert out["idea_was_defaulted"] is True
    assert out["idea_used"] == bfts_root._DEFAULT_SMOKE_IDEA

    # Structured log enumerates which required fields were missing.
    default_logs = [
        kw for ev, kw in ctx.logs if ev == "bfts_root_using_default_idea"
    ]
    assert len(default_logs) == 1
    assert set(default_logs[0]["missing_fields"]) == set(
        bfts_root._REQUIRED_IDEA_FIELDS
    )


@pytest.mark.asyncio
async def test_handler_substitutes_default_idea_when_partial() -> None:
    """A partial idea (only ``Name`` + ``Title``, no ``Short Hypothesis``
    nor ``Experiments``) is still substituted — empty-string and absent
    fields are equally degenerate to the draft prompt."""
    import bfts_root

    partial = {"Name": "x", "Title": "Y", "Short Hypothesis": "", "Experiments": []}
    ctx = _RootCtx()
    out = await bfts_root.handler(_input(num_drafts=1, idea=partial), ctx)

    assert out["idea_was_defaulted"] is True
    assert out["idea_used"] == bfts_root._DEFAULT_SMOKE_IDEA
    default_logs = [
        kw for ev, kw in ctx.logs if ev == "bfts_root_using_default_idea"
    ]
    assert len(default_logs) == 1
    # The two truly-empty fields (Short Hypothesis "", Experiments []) are
    # reported; the populated ones (Name, Title) are not.
    assert set(default_logs[0]["missing_fields"]) == {
        "Short Hypothesis", "Experiments"
    }


@pytest.mark.asyncio
async def test_handler_passes_through_valid_idea_unchanged() -> None:
    """A plan-complete idea must be forwarded verbatim — the default
    fixture is ONLY a backstop. ``idea_was_defaulted`` is False so
    operator postmortems can distinguish operator-driven runs from
    smoke/default runs."""
    import bfts_root

    real_idea = {
        "Name": "operator-experiment",
        "Title": "An actual research idea",
        "Short Hypothesis": "Method X improves metric Y by Z.",
        "Experiments": ["Step 1", "Step 2"],
    }
    ctx = _RootCtx()
    out = await bfts_root.handler(_input(num_drafts=1, idea=real_idea), ctx)

    assert out["idea_was_defaulted"] is False
    assert out["idea_used"] == real_idea
    # No substitution log.
    default_logs = [
        kw for ev, kw in ctx.logs if ev == "bfts_root_using_default_idea"
    ]
    assert default_logs == []
    # Every child receives the operator-supplied idea, not the default.
    for call in ctx.start_workflow_calls:
        assert call["run_input"]["idea"] == real_idea


def test_default_smoke_idea_has_every_required_field() -> None:
    """Lock-in: the baked-in default must itself satisfy
    ``_REQUIRED_IDEA_FIELDS``. A future field rename in one constant
    without updating the other would silently revert smoke runs to the
    substitution branch (and hit infinite recursion if the default were
    a fallback of itself)."""
    import bfts_root

    for f in bfts_root._REQUIRED_IDEA_FIELDS:
        assert bfts_root._DEFAULT_SMOKE_IDEA.get(f), (
            f"default smoke idea missing required field: {f!r}"
        )


# ---------------------------------------------------------------------------
# F.6.2: verification surface in the handler return value.
#
# The Slack agent cannot query the DB directly. The workflow return value
# (persisted to ``workflow_runs.output_json`` and read by
# ``call workflow get <run_id>``) must carry enough postmortem data that
# a sandbox-token agent can verify F.1–F.5 behaviors without psql.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_return_carries_per_tree_summaries() -> None:
    """Each tree summary must include ``tree_index``, ``run_id``,
    ``sandbox_id``, and the merged ``bfts_tree`` handler return (read
    from ``output_json`` on the wait-for-workflow envelope)."""
    import bfts_root

    # Configure the wait response to carry an ``output_json`` payload that
    # mimics what ``bfts_tree.handler`` would actually return.
    fake_tree_output = {
        "iters_used": 3,
        "node_count": 12,
        "best_node_id": "node-best",
        "best_metric_json": {"final_value": 0.42},
        "best_stage_name": "improve",
        "best_solution_artifact_id": "art-best-1",
        "tree_dot_artifact_id": "r-1:tree:0:tree.dot",
        "seed_aggregate": {
            "aggregate_mean": 0.41,
            "aggregate_std": 0.02,
            "aggregate_n": 3.0,
        },
        "seed_children": [
            {"node_id": "s0", "seed": 0, "is_buggy": False, "final_value": 0.4},
            {"node_id": "s1", "seed": 1, "is_buggy": False, "final_value": 0.43},
            {"node_id": "s2", "seed": 2, "is_buggy": False, "final_value": 0.40},
        ],
    }
    ctx = _RootCtx()
    # Inject the output by overriding wait_for_workflow's return shape.
    original_wait = ctx.wait_for_workflow

    async def _wait(name, *, run_id):
        env = await original_wait(name, run_id=run_id)
        return {**env, "output_json": fake_tree_output}

    ctx.wait_for_workflow = _wait

    out = await bfts_root.handler(_input(num_drafts=2), ctx)

    assert len(out["trees"]) == 2
    for tree in out["trees"]:
        # Controller-side bookkeeping kept alongside the merged child return.
        assert {"tree_index", "run_id", "sandbox_id", "status"}.issubset(tree)
        # Child return fields merged in.
        assert tree["best_node_id"] == "node-best"
        assert tree["best_metric_json"] == {"final_value": 0.42}
        assert tree["tree_dot_artifact_id"] == "r-1:tree:0:tree.dot"
        assert tree["seed_aggregate"]["aggregate_n"] == 3.0
        assert [s["seed"] for s in tree["seed_children"]] == [0, 1, 2]


@pytest.mark.asyncio
async def test_handler_return_carries_resolved_config_and_sources() -> None:
    """``resolved_search_config`` and ``sources`` must be present so the
    Slack agent can answer "which tier won this field?" without psql."""
    import bfts_root

    ctx = _RootCtx()
    out = await bfts_root.handler(_input(num_drafts=1), ctx)

    for key in ("debug_prob", "max_debug_depth", "num_drafts",
                "num_workers", "metric_reducer",
                "prior_attempts_window", "num_seeds"):
        assert key in out["resolved_search_config"], (
            f"resolved_search_config missing {key!r}"
        )
        assert key in out["sources"], f"sources missing {key!r}"


@pytest.mark.asyncio
async def test_handler_return_tolerates_failed_child_with_no_output() -> None:
    """Failed children have ``output_json=None`` from the engine. The
    handler MUST still emit a per-tree summary row (so the operator can
    see which tree died), with only the controller-side keys populated."""
    import bfts_root

    ctx = _RootCtx()
    # ``ctx.fail_on_wait`` makes the wait RAISE, not return a failed
    # envelope. So instead, override wait to return a failed envelope
    # with output_json=None for tree 0.
    original_wait = ctx.wait_for_workflow

    async def _wait(name, *, run_id):
        env = await original_wait(name, run_id=run_id)
        if run_id.endswith(":tree:0"):
            return {**env, "status": "failed", "output_json": None}
        return env

    ctx.wait_for_workflow = _wait
    out = await bfts_root.handler(_input(num_drafts=2), ctx)

    assert len(out["trees"]) == 2
    failed = next(t for t in out["trees"] if t["tree_index"] == 0)
    assert failed["status"] == "failed"
    # No child output → no merged fields, just controller bookkeeping.
    assert failed["sandbox_id"].startswith("bfts-run-1-tree-0")
    assert "best_node_id" not in failed
