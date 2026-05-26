"""Test: bfts_root handler input parsing, sandbox_id format, and teardown."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bfts_root import Input, WORKFLOW_NAME, _sandbox_id


def test_workflow_name() -> None:
    assert WORKFLOW_NAME == "bfts_root"


def test_input_required_idea() -> None:
    from _bfts_config import resolve_llm_settings

    inp = Input(idea={"name": "test", "Title": "X"})
    assert inp.idea["name"] == "test"
    assert inp.num_drafts == 3
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

    def __init__(self, *, run_id: str = "run-1") -> None:
        self.run_id = run_id
        self.tools = _Tools()
        self.step_calls: list[str] = []
        self.start_workflow_calls: list[str] = []
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
        self.start_workflow_calls.append(name)
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


def _input(num_drafts: int = 2) -> Input:
    return Input(idea={"name": "toy"}, num_drafts=num_drafts, num_workers=1, max_iters=1)


@pytest.mark.asyncio
async def test_happy_path_runs_all_teardowns_and_returns_results() -> None:
    """Baseline: nothing fails; every create/start/wait/stop step runs once
    and the return shape matches the original handler contract."""
    import bfts_root

    ctx = _RootCtx()
    out = await bfts_root.handler(_input(num_drafts=2), ctx)

    assert [c["tree_index"] for c in out["trees"]] == [0, 1]
    assert len(out["results"]) == 2
    assert ctx.tools.bfts_executor.create_sandbox.await_count == 2
    assert ctx.tools.bfts_executor.stop_sandbox.await_count == 2
    assert {"stop_sandbox_0", "stop_sandbox_1"}.issubset(set(ctx.step_calls))


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
