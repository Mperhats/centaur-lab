"""Test: bfts_tree handler input parsing + terminate condition + VLM wiring."""
from __future__ import annotations

import inspect
import json as _json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bfts_tree import Input, WORKFLOW_NAME, _should_terminate


def test_workflow_name() -> None:
    assert WORKFLOW_NAME == "bfts_tree"


def test_input_defaults() -> None:
    from _bfts_config import resolve_llm_settings

    inp = Input(run_id="r1", parent_run_id=None, idea={"name": "x"})
    assert inp.num_drafts == 3
    assert inp.num_workers == 4
    assert inp.max_debug_depth == 3
    assert inp.debug_prob == 0.5
    assert inp.max_iters == 20
    assert inp.seed == 0
    assert inp.llm_api_key_secret is None
    assert inp.draft_model is None
    llm = resolve_llm_settings(
        draft_model=inp.draft_model,
        feedback_model=inp.feedback_model,
        vlm_model=inp.vlm_model,
        llm_api_key_secret=inp.llm_api_key_secret,
    )
    assert llm.llm_api_key_secret == "ANTHROPIC_API_KEY"
    assert llm.draft_model == "claude-sonnet-4-20250514"
    assert llm.feedback_model == "claude-sonnet-4-20250514"
    assert llm.vlm_model == "claude-sonnet-4-20250514"


def test_terminate_on_good_node() -> None:
    nodes = [
        {"is_buggy": False, "is_buggy_plots": False},
        {"is_buggy": True, "is_buggy_plots": None},
    ]
    assert _should_terminate(nodes, iters_used=5, max_iters=20) is True


def test_terminate_on_max_iters_with_no_good_node() -> None:
    nodes = [{"is_buggy": True, "is_buggy_plots": None}]
    assert _should_terminate(nodes, iters_used=20, max_iters=20) is True


def test_no_terminate_yet() -> None:
    nodes = [{"is_buggy": True, "is_buggy_plots": None}]
    assert _should_terminate(nodes, iters_used=5, max_iters=20) is False


def test_parse_metric_json_string_round_trip() -> None:
    from bfts_tree import _parse_metric_json

    assert _parse_metric_json(_json.dumps({"loss": 0.5})) == {"loss": 0.5}


def test_parse_metric_json_dict_passthrough() -> None:
    from bfts_tree import _parse_metric_json

    assert _parse_metric_json({"loss": 0.5}) == {"loss": 0.5}


def test_parse_metric_json_none_returns_worst() -> None:
    from bfts_tree import _parse_metric_json

    assert _parse_metric_json(None) == {"_worst": True}


def test_parse_metric_json_garbage_returns_worst() -> None:
    from bfts_tree import _parse_metric_json

    assert _parse_metric_json("not valid json") == {"_worst": True}
    assert _parse_metric_json("") == {"_worst": True}
    assert _parse_metric_json(42) == {"_worst": True}


def test_to_noderef_computes_is_leaf_from_child_count() -> None:
    """``_to_noderef`` must derive ``is_leaf`` from the row's ``child_count``
    column produced by ``list_nodes_for_run``'s correlated subquery.

    Internal nodes (``child_count >= 1``) must round-trip as
    ``is_leaf=False`` so ``_bfts_select._buggy_leaf_nodes`` skips them.
    Leaves (``child_count == 0``) must round-trip as ``is_leaf=True``.
    """
    from bfts_tree import _to_noderef

    internal_row: dict[str, Any] = {
        "node_id": "n-internal",
        "parent_node_id": None,
        "is_buggy": True,
        "is_buggy_plots": None,
        "debug_depth": 0,
        "metric_json": None,
        "stage_name": "draft",
        "child_count": 2,
    }
    leaf_row: dict[str, Any] = {
        "node_id": "n-leaf",
        "parent_node_id": "n-internal",
        "is_buggy": True,
        "is_buggy_plots": None,
        "debug_depth": 1,
        "metric_json": None,
        "stage_name": "debug",
        "child_count": 0,
    }

    assert _to_noderef(internal_row).is_leaf is False
    assert _to_noderef(leaf_row).is_leaf is True


def test_to_noderef_missing_child_count_defaults_to_leaf() -> None:
    """Backward-compat: rows without ``child_count`` (older test fixtures,
    or pre-migration callers) default to ``is_leaf=True``. The DAO is the
    source of truth for this column; absence means it was not queried."""
    from bfts_tree import _to_noderef

    row: dict[str, Any] = {
        "node_id": "n-x",
        "parent_node_id": None,
        "is_buggy": False,
        "is_buggy_plots": False,
        "debug_depth": 0,
        "metric_json": None,
        "stage_name": "draft",
    }

    assert _to_noderef(row).is_leaf is True


# --- Handler-level VLM wiring tests --------------------------------------
#
# These tests assert that `bfts_tree.handler` invokes the
# `mark_buggy_plots` DAO step iff the expansion result carries an
# ``is_buggy_plots`` key (good path), and propagates the VLM verdict
# unchanged. The dependencies pulled into the handler at runtime
# (`expand_node`, the DAOs in `_bfts_state`, the exporters in
# `_bfts_export`, and `resolve_llm_api_key` from `_bfts_config`) are
# patched at their import sites — no real DB, LLM, sandbox, or
# centaur_sdk required.


class _FakeCtx:
    """Stand-in WorkflowContext.

    The real ``ctx.step(name, fn)`` awaits ``fn()`` durably. Here we just
    call it and await any coroutine it returns so the patched DAO stubs
    actually run and record their args. ``_pool`` is a sentinel object —
    every lambda site in `bfts_tree` passes it as the first positional
    arg to a DAO, but every DAO is itself a stub so the value is never
    used.
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


def _good_expand_result(*, is_buggy_plots: bool, plot_analyses: list[dict[str, Any]], summary: str) -> dict[str, Any]:
    return {
        "plan": "p",
        "code": "print(1)",
        "term_out": ["ok\n"],
        "exec_time_seconds": 0.1,
        "exc_type": None,
        "exc_info": None,
        "exc_stack": None,
        "metric": {"metric_names": []},
        "is_buggy": False,
        "analysis": "ran clean",
        "stage_name": "draft",
        "parse_metrics_code": "print('m')",
        "parse_term_out": ["m\n"],
        "plot_code": "import matplotlib",
        "plot_term_out": [],
        "is_buggy_plots": is_buggy_plots,
        "plot_analyses": plot_analyses,
        "vlm_feedback_summary": summary,
    }


def _buggy_expand_result() -> dict[str, Any]:
    # Mirrors the early-return shape in `_bfts_expand.expand_node` for the
    # buggy branch (lines ~172–184): NO ``is_buggy_plots`` key.
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


def _patch_handler_deps(monkeypatch: pytest.MonkeyPatch, *, expand_result: dict[str, Any]) -> AsyncMock:
    """Wire AsyncMock stubs at every dependency the handler touches and
    return the ``mark_buggy_plots`` recorder for assertions.

    Patches are applied at the import site (``bfts_tree.<name>``) so the
    handler's already-bound references pick them up. ``_bfts_export``
    is imported lazily inside the handler — patching the source module
    is enough because ``from _bfts_export import select_best`` is
    re-resolved on every handler call.
    """
    import bfts_tree
    import _bfts_export

    monkeypatch.setattr(bfts_tree, "resolve_llm_api_key", lambda _secret_name: "fake-key")

    monkeypatch.setattr(bfts_tree, "insert_run", AsyncMock(return_value=None))
    monkeypatch.setattr(bfts_tree, "insert_node", AsyncMock(return_value=None))
    # Empty initial node list — `_should_terminate` stays False on entry,
    # `select_next` then emits one phantom draft (num_drafts=1, num_workers=1).
    monkeypatch.setattr(bfts_tree, "list_nodes_for_run", AsyncMock(return_value=[]))
    monkeypatch.setattr(bfts_tree, "update_node_metric", AsyncMock(return_value=None))
    monkeypatch.setattr(bfts_tree, "set_best_node", AsyncMock(return_value=None))

    mark_stub = AsyncMock(return_value=None)
    monkeypatch.setattr(bfts_tree, "mark_buggy_plots", mark_stub)

    monkeypatch.setattr(_bfts_export, "select_best", lambda _nodes: None)
    monkeypatch.setattr(_bfts_export, "write_best_artifact", AsyncMock(return_value="art"))

    monkeypatch.setattr(bfts_tree, "expand_node", AsyncMock(return_value=expand_result))

    return mark_stub


def _make_input() -> Input:
    return Input(
        run_id="r-vlm-1",
        parent_run_id=None,
        idea={"name": "test", "Title": "X"},
        num_drafts=1,
        num_workers=1,
        max_iters=1,
        sandbox_id="sbx-test",
    )


@pytest.mark.asyncio
async def test_handler_calls_mark_buggy_plots_when_vlm_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Good path: expand_node returns ``is_buggy_plots=False`` + VLM
    fields → handler must call ``mark_buggy_plots`` once with those
    fields propagated unchanged."""
    import bfts_tree

    result = _good_expand_result(
        is_buggy_plots=False,
        plot_analyses=[{"name": "loss.png", "is_valid": True}],
        summary="ok",
    )
    mark_stub = _patch_handler_deps(monkeypatch, expand_result=result)

    ctx = _FakeCtx()
    await bfts_tree.handler(_make_input(), ctx)

    assert mark_stub.call_count == 1, ctx.calls
    assert "mark_buggy_plots" in ctx.calls
    kwargs = mark_stub.call_args.kwargs
    assert kwargs["is_buggy_plots"] is False
    assert kwargs["plot_analyses"] == [{"name": "loss.png", "is_valid": True}]
    assert kwargs["vlm_feedback_summary"] == "ok"
    assert isinstance(kwargs["node_id"], str) and kwargs["node_id"]


@pytest.mark.asyncio
async def test_handler_skips_mark_buggy_plots_on_buggy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Buggy path: expand_node short-circuits without an
    ``is_buggy_plots`` key → handler must NOT call
    ``mark_buggy_plots``."""
    import bfts_tree

    mark_stub = _patch_handler_deps(monkeypatch, expand_result=_buggy_expand_result())

    ctx = _FakeCtx()
    await bfts_tree.handler(_make_input(), ctx)

    assert mark_stub.call_count == 0
    assert "mark_buggy_plots" not in ctx.calls


@pytest.mark.asyncio
async def test_handler_marks_buggy_plots_true_when_vlm_invalidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """VLM-invalidated path: expand_node returns ``is_buggy_plots=True``
    with an empty analyses list → handler must propagate
    ``is_buggy_plots=True`` to the DAO."""
    import bfts_tree

    result = _good_expand_result(
        is_buggy_plots=True,
        plot_analyses=[],
        summary="bad",
    )
    mark_stub = _patch_handler_deps(monkeypatch, expand_result=result)

    ctx = _FakeCtx()
    await bfts_tree.handler(_make_input(), ctx)

    assert mark_stub.call_count == 1
    kwargs = mark_stub.call_args.kwargs
    assert kwargs["is_buggy_plots"] is True
    assert kwargs["plot_analyses"] == []
    assert kwargs["vlm_feedback_summary"] == "bad"
