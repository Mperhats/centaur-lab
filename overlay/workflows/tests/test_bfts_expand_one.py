"""Tests for bfts_expand_one — one-shot expansion child workflow.

The child workflow wraps ``_bfts_expand.expand_node`` (per-node, structured
LLM + executor + VLM pipeline) so the tree controller (``bfts_tree.handler``)
can fan out N expansions per iteration via ``ctx.start_workflow``.

These tests pin the handler-level contract:

- ``expand_node`` is called once with an ``ExpandContext`` built from
  ``Input`` (sandbox, parent_node, idea, working_dir, model overrides).
- The resulting node row is persisted via ``update_node_metric``; the
  optional VLM verdict is persisted via ``mark_buggy_plots`` iff the
  result carries an ``is_buggy_plots`` key (good path only).
- The handler returns a minimal summary dict
  (``node_id``, ``is_buggy``, ``stage_name``) — the parent tree uses the
  child's persisted DB row, not the return value, for subsequent
  selection. The summary exists for logging / debugging only.
- ``expand_node`` failures propagate; no workflow-level retry or swallow
  (the expand pipeline already has internal retry semantics; double
  retry would burn LLM budget without diagnostic value).
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bfts_expand_one import Input, WORKFLOW_NAME


def test_workflow_name() -> None:
    assert WORKFLOW_NAME == "bfts_expand_one"


def test_schedule_is_empty() -> None:
    """bfts_expand_one is invoked via ctx.start_workflow, not on a timer.

    Module-level ``SCHEDULE`` must be an empty dict so the workflow
    engine's scheduler tick skips it. A populated ``SCHEDULE`` would
    cause the engine to fire orphan runs (no parent tree, no sandbox).
    """
    import bfts_expand_one

    assert bfts_expand_one.SCHEDULE == {}


def test_input_required_fields() -> None:
    """The minimal Input carries the controller-assigned identifiers and
    the per-tree sandbox. Model overrides are optional (resolve_llm_settings
    falls back to BFTS_* env / module defaults)."""
    inp = Input(
        run_id="r-1",
        node_id="n-deadbeef0001",
        sandbox_id="bfts-r-1-tree-0",
        working_dir="node_deadbeef",
    )
    assert inp.run_id == "r-1"
    assert inp.node_id == "n-deadbeef0001"
    assert inp.sandbox_id == "bfts-r-1-tree-0"
    assert inp.working_dir == "node_deadbeef"
    assert inp.parent_node is None
    assert inp.draft_model is None
    assert inp.feedback_model is None
    assert inp.vlm_model is None


# --- Handler-level tests --------------------------------------------------


class _FakeCtx:
    """Stand-in WorkflowContext.

    ``step`` calls ``fn`` and awaits any coroutine so the patched DAOs
    actually run and record their args. ``_pool`` is a sentinel — every
    DAO is patched, so the value is never dereferenced.
    """

    def __init__(self) -> None:
        self._pool = object()
        self.calls: list[str] = []

    async def step(self, name: str, fn: Any) -> Any:
        self.calls.append(name)
        out = fn() if callable(fn) else fn
        if inspect.iscoroutine(out):
            out = await out
        return out

    def log(self, *_a: Any, **_kw: Any) -> None:
        return None


def _good_expand_result() -> dict[str, Any]:
    return {
        "plan": "p",
        "code": "print(1)",
        "term_out": ["ok\n"],
        "exec_time_seconds": 0.1,
        "exc_type": None,
        "exc_info": None,
        "exc_stack": None,
        "metric": {"metric_names": ["acc"]},
        "is_buggy": False,
        "analysis": "ran clean",
        "stage_name": "draft",
        "parse_metrics_code": "print('m')",
        "parse_term_out": ["m\n"],
        "plot_code": "import matplotlib",
        "plot_term_out": [],
        "is_buggy_plots": False,
        "plot_analyses": [{"name": "loss.png", "is_valid": True}],
        "vlm_feedback_summary": "ok",
    }


def _buggy_expand_result() -> dict[str, Any]:
    # Mirrors _bfts_expand.expand_node's buggy short-circuit: no
    # parse_*/plot_* keys, no is_buggy_plots key.
    return {
        "plan": "p",
        "code": "raise RuntimeError()",
        "term_out": ["err\n"],
        "exec_time_seconds": 0.1,
        "exc_type": "SubprocessError",
        "exc_info": {"exit_code": 1},
        "exc_stack": None,
        "metric": None,
        "is_buggy": True,
        "analysis": "raised",
        "stage_name": "draft",
    }


def _patch_handler_deps(
    monkeypatch: pytest.MonkeyPatch,
    *,
    expand_result: dict[str, Any] | Exception,
) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    """Patch every dep the handler touches and return
    (expand_stub, update_stub, mark_stub) for assertion.

    Patches at the import site (``bfts_expand_one.<name>``) so the
    handler's already-bound references resolve to the stubs.
    """
    import bfts_expand_one

    monkeypatch.setattr(
        bfts_expand_one, "resolve_llm_api_key", lambda _secret: "fake-key"
    )

    if isinstance(expand_result, Exception):
        expand_stub = AsyncMock(side_effect=expand_result)
    else:
        expand_stub = AsyncMock(return_value=expand_result)
    monkeypatch.setattr(bfts_expand_one, "expand_node", expand_stub)

    update_stub = AsyncMock(return_value=None)
    monkeypatch.setattr(bfts_expand_one, "update_node_metric", update_stub)

    mark_stub = AsyncMock(return_value=None)
    monkeypatch.setattr(bfts_expand_one, "mark_buggy_plots", mark_stub)

    return expand_stub, update_stub, mark_stub


def _make_input(**overrides: Any) -> Input:
    base: dict[str, Any] = {
        "run_id": "r-1",
        "node_id": "n-deadbeef0001",
        "sandbox_id": "bfts-r-1-tree-0",
        "working_dir": "node_deadbeef",
        "parent_node": None,
        "idea": {"name": "toy", "Title": "T"},
    }
    base.update(overrides)
    return Input(**base)


@pytest.mark.asyncio
async def test_handler_calls_expand_node_with_working_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Input.working_dir must reach ExpandContext so the executor
    runs in the per-node subdir (Phase 4h.1 contract). A wrong working_dir
    would race with sibling expansions on ``runfile.py`` / ``experiment_data.npy``.
    """
    import bfts_expand_one

    expand_stub, _update_stub, _mark_stub = _patch_handler_deps(
        monkeypatch, expand_result=_good_expand_result()
    )

    ctx = _FakeCtx()
    await bfts_expand_one.handler(_make_input(), ctx)

    assert expand_stub.await_count == 1
    expand_ctx = expand_stub.await_args.kwargs["expand_ctx"]
    assert expand_ctx.working_dir == "node_deadbeef"
    assert expand_ctx.sandbox_id == "bfts-r-1-tree-0"
    assert expand_ctx.node_id == "n-deadbeef0001"


@pytest.mark.asyncio
async def test_handler_propagates_parent_node_and_idea(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parent-node row + idea dict must be forwarded verbatim so the
    debug/improve prompts in ``_bfts_expand._propose_prompt`` see the
    upstream code + stderr."""
    import bfts_expand_one

    parent = {
        "node_id": "n-parent",
        "code": "print('parent')",
        "term_out_json": "err",
        "is_buggy": True,
    }
    expand_stub, _u, _m = _patch_handler_deps(
        monkeypatch, expand_result=_good_expand_result()
    )

    ctx = _FakeCtx()
    await bfts_expand_one.handler(
        _make_input(parent_node=parent, idea={"name": "x", "Title": "Y"}), ctx
    )

    expand_ctx = expand_stub.await_args.kwargs["expand_ctx"]
    assert expand_ctx.parent_node == parent
    assert expand_ctx.idea == {"name": "x", "Title": "Y"}


@pytest.mark.asyncio
async def test_handler_propagates_llm_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit per-Input model overrides must reach ExpandContext (and
    thus every LLM call), unaffected by env defaults."""
    import bfts_expand_one

    expand_stub, _u, _m = _patch_handler_deps(
        monkeypatch, expand_result=_good_expand_result()
    )

    ctx = _FakeCtx()
    await bfts_expand_one.handler(
        _make_input(
            draft_model="claude-draft-test",
            feedback_model="claude-feedback-test",
            vlm_model="claude-vision-test",
        ),
        ctx,
    )

    expand_ctx = expand_stub.await_args.kwargs["expand_ctx"]
    assert expand_ctx.draft_model == "claude-draft-test"
    assert expand_ctx.feedback_model == "claude-feedback-test"
    assert expand_ctx.vlm_model == "claude-vision-test"
    assert expand_ctx.llm_api_key == "fake-key"


@pytest.mark.asyncio
async def test_handler_persists_node_and_marks_plots_on_good_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Good path: update_node_metric is called once with the full result;
    mark_buggy_plots is called with the VLM verdict (since the good-path
    result includes ``is_buggy_plots``)."""
    import bfts_expand_one

    result = _good_expand_result()
    _expand_stub, update_stub, mark_stub = _patch_handler_deps(
        monkeypatch, expand_result=result
    )

    ctx = _FakeCtx()
    out = await bfts_expand_one.handler(_make_input(), ctx)

    assert update_stub.await_count == 1
    update_kwargs = update_stub.call_args.kwargs
    assert update_kwargs["node_id"] == "n-deadbeef0001"
    assert update_kwargs["is_buggy"] is False
    assert update_kwargs["metric"] == {"metric_names": ["acc"]}
    assert update_kwargs["plan"] == "p"
    assert update_kwargs["code"] == "print(1)"
    assert update_kwargs["parse_metrics_code"] == "print('m')"
    assert update_kwargs["parse_term_out"] == ["m\n"]
    assert update_kwargs["plot_code"] == "import matplotlib"
    assert update_kwargs["plot_term_out"] == []

    assert mark_stub.await_count == 1
    mark_kwargs = mark_stub.call_args.kwargs
    assert mark_kwargs["node_id"] == "n-deadbeef0001"
    assert mark_kwargs["is_buggy_plots"] is False
    assert mark_kwargs["plot_analyses"] == [
        {"name": "loss.png", "is_valid": True}
    ]
    assert mark_kwargs["vlm_feedback_summary"] == "ok"

    assert out["node_id"] == "n-deadbeef0001"
    assert out["is_buggy"] is False
    assert out["stage_name"] == "draft"
    assert "update_node" in ctx.calls
    assert "mark_buggy_plots" in ctx.calls


@pytest.mark.asyncio
async def test_handler_marks_buggy_plots_true_when_vlm_invalidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Good-path execution but VLM invalidates the plots:
    ``is_buggy_plots=True`` with empty ``plot_analyses`` must propagate to
    ``mark_buggy_plots`` so the controller's ``_good_nodes`` filter
    (``is_buggy is False AND is_buggy_plots is not True``) excludes this
    node from the stage-1 completion check. Pinning the True direction
    fences a regression where the handler always passed
    ``is_buggy_plots=False`` regardless of the VLM verdict.

    The expansion result mirrors ``_good_expand_result`` but flips the
    VLM verdict and empties ``plot_analyses`` (the shape produced by
    ``_bfts_expand`` when the VLM rejects every plot).
    """
    import bfts_expand_one

    invalid_result = _good_expand_result()
    invalid_result["is_buggy_plots"] = True
    invalid_result["plot_analyses"] = []
    invalid_result["vlm_feedback_summary"] = "all plots invalid"

    _expand_stub, update_stub, mark_stub = _patch_handler_deps(
        monkeypatch, expand_result=invalid_result
    )

    ctx = _FakeCtx()
    out = await bfts_expand_one.handler(_make_input(), ctx)

    assert update_stub.await_count == 1
    assert update_stub.call_args.kwargs["is_buggy"] is False

    assert mark_stub.await_count == 1
    mark_kwargs = mark_stub.call_args.kwargs
    assert mark_kwargs["node_id"] == "n-deadbeef0001"
    assert mark_kwargs["is_buggy_plots"] is True
    assert mark_kwargs["plot_analyses"] == []
    assert mark_kwargs["vlm_feedback_summary"] == "all plots invalid"

    assert out["node_id"] == "n-deadbeef0001"
    assert out["is_buggy"] is False
    assert out["stage_name"] == "draft"
    assert "mark_buggy_plots" in ctx.calls


@pytest.mark.asyncio
async def test_handler_persists_node_without_mark_on_buggy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Buggy path: update_node_metric still records the failure
    (is_buggy=True), but mark_buggy_plots is skipped (no ``is_buggy_plots``
    key in the result). The COALESCE contract on update_node_metric
    preserves the empty plan/code rows from insert_node — we pass through
    whatever the expand_node returned."""
    import bfts_expand_one

    _expand_stub, update_stub, mark_stub = _patch_handler_deps(
        monkeypatch, expand_result=_buggy_expand_result()
    )

    ctx = _FakeCtx()
    out = await bfts_expand_one.handler(_make_input(), ctx)

    assert update_stub.await_count == 1
    update_kwargs = update_stub.call_args.kwargs
    assert update_kwargs["is_buggy"] is True
    assert update_kwargs["metric"] is None
    assert update_kwargs["exc_type"] == "SubprocessError"
    # parse_*/plot_* default to None on the buggy short-circuit so
    # update_node_metric's COALESCE leaves those columns unchanged.
    assert update_kwargs["parse_metrics_code"] is None
    assert update_kwargs["plot_code"] is None
    assert update_kwargs["parse_term_out"] is None
    assert update_kwargs["plot_term_out"] is None

    assert mark_stub.await_count == 0
    assert "mark_buggy_plots" not in ctx.calls

    assert out["node_id"] == "n-deadbeef0001"
    assert out["is_buggy"] is True
    assert out["stage_name"] == "draft"


@pytest.mark.asyncio
async def test_handler_propagates_expand_node_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure: if expand_node raises, the workflow engine must see the
    exception so the workflow is marked failed and the parent
    ``bfts_tree.handler`` can react via ``wait_for_workflow``.

    No try/except wrapper, no retry, no swallow. The expand pipeline
    already has internal retry semantics for transient LLM errors;
    double retry here would burn LLM budget without diagnostic value.
    """
    import bfts_expand_one

    _expand_stub, update_stub, mark_stub = _patch_handler_deps(
        monkeypatch, expand_result=RuntimeError("LLM exhausted")
    )

    ctx = _FakeCtx()
    with pytest.raises(RuntimeError, match="LLM exhausted"):
        await bfts_expand_one.handler(_make_input(), ctx)

    # Neither persistence step ran because the failure was upstream.
    assert update_stub.await_count == 0
    assert mark_stub.await_count == 0
