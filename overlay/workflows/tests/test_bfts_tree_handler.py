"""Test: bfts_tree handler input parsing + terminate condition + fan-out wiring.

Phase 4h.3 retired the inline expansion path: the controller no longer
calls ``expand_node`` / ``update_node_metric`` / ``mark_buggy_plots``
directly. Those are now performed by the ``bfts_expand_one`` child
workflow (see ``test_bfts_expand_one.py`` for that level's coverage).
The handler-level tests below assert the new fan-out shape: per
iteration the controller (re-)queries ``list_nodes_for_run``, inserts
one placeholder row per selection, starts ``bfts_expand_one`` for each
node in parallel (``eager_start=True``), then waits for every child
before the next iteration.
"""
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


# --- Handler-level fan-out tests -----------------------------------------
#
# The controller's per-iteration shape after Phase 4h.3:
#
#   list_nodes → select_next → for each selection: insert_node placeholder,
#   then start_workflow("bfts_expand_one", eager_start=True). Once every
#   child is started, wait_for_workflow on each before the next outer
#   iteration. The DB rows updated by the children are picked up by the
#   next iteration's list_nodes_for_run; the child's return-value envelope
#   is logging-only.
#
# These tests assert the fan-out shape (parallel start → wait), not the
# child workflow's internal pipeline (covered in test_bfts_expand_one.py).


class _TreeCtx:
    """Stand-in WorkflowContext for the bfts_tree handler.

    ``step``/``start_workflow``/``wait_for_workflow`` are minimal recorders
    so the test asserts the call ordering / kwargs. Every entry point
    appends to ``calls`` (a unified ordered list) so per-iteration
    interleaving (insert → start → wait → next-iteration insert) can
    be asserted by index. ``start_workflow_calls`` /
    ``wait_for_workflow_calls`` keep the structured arg captures for
    kwargs assertions. ``run_id`` is the parent run id (workflow engine
    sets this; here we hard-code it for the deterministic
    ``trigger_key``). ``_pool`` is a sentinel — every DAO is patched,
    so the value is never dereferenced.
    """

    def __init__(self, *, run_id: str = "r-tree-1") -> None:
        self.run_id = run_id
        self._pool = object()
        self.calls: list[str] = []
        self.start_workflow_calls: list[dict[str, Any]] = []
        self.wait_for_workflow_calls: list[str] = []

    async def step(self, name: str, fn: Any) -> Any:
        self.calls.append(name)
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
        self.calls.append(name)
        self.start_workflow_calls.append(
            {
                "name": name,
                "workflow_name": workflow_name,
                "run_input": run_input,
                "trigger_key": trigger_key,
                "eager_start": eager_start,
            }
        )
        return {"run_id": trigger_key}

    async def wait_for_workflow(self, name: str, *, run_id: str) -> dict[str, Any]:
        self.calls.append(name)
        self.wait_for_workflow_calls.append(run_id)
        return {"run_id": run_id, "status": "completed"}

    def log(self, *_a: Any, **_kw: Any) -> None:
        return None


def _patch_fanout_deps(
    monkeypatch: pytest.MonkeyPatch,
    *,
    list_nodes_returns: list[list[dict[str, Any]]] | None = None,
) -> None:
    """Patch every DAO + exporter the handler still touches after 4h.3.

    ``list_nodes_returns`` is an iterable of return values for successive
    ``list_nodes_for_run`` calls; defaults to always ``[]`` so the
    selector emits ``num_workers`` ``None`` (draft) selections every
    iteration. Each entry is consumed once; once exhausted the DAO
    returns the last entry on subsequent calls (so the final
    ``list_nodes_final`` step gets the latest state).
    """
    import _bfts_export
    import bfts_tree

    monkeypatch.setattr(bfts_tree, "insert_run", AsyncMock(return_value=None))
    monkeypatch.setattr(bfts_tree, "insert_node", AsyncMock(return_value=None))
    monkeypatch.setattr(bfts_tree, "set_best_node", AsyncMock(return_value=None))

    queue = list(list_nodes_returns or [[]])
    if not queue:
        queue = [[]]

    async def _list_nodes(*_a: Any, **_kw: Any) -> list[dict[str, Any]]:
        if len(queue) > 1:
            return queue.pop(0)
        return queue[0]

    monkeypatch.setattr(bfts_tree, "list_nodes_for_run", _list_nodes)
    monkeypatch.setattr(_bfts_export, "select_best", lambda _nodes, **_kw: None)
    monkeypatch.setattr(_bfts_export, "write_best_artifact", AsyncMock(return_value=None))
    monkeypatch.setattr(
        _bfts_export, "write_best_node_id_artifact", AsyncMock(return_value=None)
    )


def _input(
    *,
    num_drafts: int = 4,
    num_workers: int = 4,
    max_iters: int = 1,
    debug_prob: float = 0.5,
    draft_model: str = "claude-draft-test",
    feedback_model: str = "claude-feedback-test",
    vlm_model: str = "claude-vision-test",
    llm_api_key_secret: str = "TEST_API_KEY",
) -> Input:
    return Input(
        run_id="r-tree-1",
        parent_run_id=None,
        idea={"name": "toy", "Title": "T"},
        num_drafts=num_drafts,
        num_workers=num_workers,
        max_iters=max_iters,
        debug_prob=debug_prob,
        sandbox_id="bfts-r-tree-1-tree-0",
        draft_model=draft_model,
        feedback_model=feedback_model,
        vlm_model=vlm_model,
        llm_api_key_secret=llm_api_key_secret,
    )


@pytest.mark.asyncio
async def test_handler_fans_out_via_bfts_expand_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One iteration with ``num_workers=4`` against an empty tree must
    start exactly 4 ``bfts_expand_one`` child workflows (one per draft
    selection). Each call must use ``eager_start=True`` so the engine
    schedules the children in parallel rather than waiting for the next
    worker poll."""
    import bfts_tree

    _patch_fanout_deps(monkeypatch)

    ctx = _TreeCtx()
    await bfts_tree.handler(_input(num_drafts=4, num_workers=4, max_iters=1), ctx)

    assert len(ctx.start_workflow_calls) == 4
    for call in ctx.start_workflow_calls:
        assert call["workflow_name"] == "bfts_expand_one"
        assert call["eager_start"] is True


@pytest.mark.asyncio
async def test_handler_inserts_placeholder_row_before_fan_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DB row must exist BEFORE ``start_workflow`` is called for that
    node — the child workflow's ``update_node_metric`` updates an
    existing row, it does not insert. Replay safety: if the controller
    crashes between insert and start_workflow, the next replay reuses the
    same node_id (``ctx.step`` cache) and either re-issues start_workflow
    or finds it already running."""
    import bfts_tree

    _patch_fanout_deps(monkeypatch)

    ctx = _TreeCtx()
    await bfts_tree.handler(_input(num_drafts=4, num_workers=4, max_iters=1), ctx)

    insert_idxs = [i for i, c in enumerate(ctx.calls) if c == "insert_node"]
    start_idxs = [
        i for i, c in enumerate(ctx.calls) if c == "start_expand_child"
    ]
    assert len(insert_idxs) == 4
    assert len(start_idxs) == 4
    assert max(insert_idxs) < min(start_idxs), ctx.calls


@pytest.mark.asyncio
async def test_handler_propagates_llm_settings_to_each_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolved LLM settings from ``bfts_tree.Input`` must reach every
    child via ``run_input``: the child re-runs ``resolve_llm_settings``
    against the same per-Input override → same models, no env drift
    between siblings."""
    import bfts_tree

    _patch_fanout_deps(monkeypatch)

    ctx = _TreeCtx()
    await bfts_tree.handler(
        _input(
            num_drafts=4,
            num_workers=4,
            max_iters=1,
            draft_model="claude-draft-X",
            feedback_model="claude-feedback-X",
            vlm_model="claude-vision-X",
            llm_api_key_secret="MY_API_KEY",
        ),
        ctx,
    )

    assert len(ctx.start_workflow_calls) == 4
    for call in ctx.start_workflow_calls:
        ri = call["run_input"]
        assert ri["draft_model"] == "claude-draft-X"
        assert ri["feedback_model"] == "claude-feedback-X"
        assert ri["vlm_model"] == "claude-vision-X"
        assert ri["llm_api_key_secret"] == "MY_API_KEY"


@pytest.mark.asyncio
async def test_handler_propagates_working_dir_per_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each child gets ``working_dir = "node_<8-hex>"`` derived from its
    assigned ``node_id`` — required by Phase 4h.1 so concurrent children
    in one sandbox don't race on shared workspace files. The 8-hex
    prefix matches the executor's allowlist (``^[A-Za-z0-9_-]+$``)."""
    import bfts_tree

    _patch_fanout_deps(monkeypatch)

    ctx = _TreeCtx()
    await bfts_tree.handler(_input(num_drafts=3, num_workers=3, max_iters=1), ctx)

    assert len(ctx.start_workflow_calls) == 3
    seen_working_dirs: set[str] = set()
    for call in ctx.start_workflow_calls:
        ri = call["run_input"]
        node_id = ri["node_id"]
        assert isinstance(node_id, str) and len(node_id) >= 8
        assert ri["working_dir"] == f"node_{node_id[:8]}"
        seen_working_dirs.add(ri["working_dir"])
    # Distinct node_ids → distinct working dirs (no shared workspace).
    assert len(seen_working_dirs) == 3


@pytest.mark.asyncio
async def test_handler_propagates_sandbox_idea_to_each_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``sandbox_id`` and ``idea`` from the tree Input flow unchanged to
    every child — siblings share one sandbox (per-tree) but each child
    isolates work via ``working_dir``."""
    import bfts_tree

    _patch_fanout_deps(monkeypatch)

    ctx = _TreeCtx()
    await bfts_tree.handler(_input(num_drafts=2, num_workers=2, max_iters=1), ctx)

    for call in ctx.start_workflow_calls:
        ri = call["run_input"]
        assert ri["sandbox_id"] == "bfts-r-tree-1-tree-0"
        assert ri["idea"] == {"name": "toy", "Title": "T"}
        assert ri["run_id"] == "r-tree-1"


@pytest.mark.asyncio
async def test_handler_waits_for_all_children_before_next_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-iteration ordering invariant: every ``start_workflow`` for
    iteration N runs before any ``wait_for_workflow`` for iteration N,
    and every ``wait_for_workflow`` for iteration N runs before any
    ``start_workflow`` for iteration N+1. Without this barrier the next
    iteration's ``list_nodes_for_run`` could see partial child results."""
    import bfts_tree

    _patch_fanout_deps(monkeypatch)

    ctx = _TreeCtx()
    await bfts_tree.handler(_input(num_drafts=2, num_workers=2, max_iters=2), ctx)

    starts = [i for i, c in enumerate(ctx.calls) if c == "start_expand_child"]
    waits = [i for i, c in enumerate(ctx.calls) if c == "wait_expand_child"]

    # Two iterations * num_workers=2 = 4 starts + 4 waits.
    assert len(starts) == 4
    assert len(waits) == 4

    # All iter-1 starts (first two) precede all iter-1 waits (next two);
    # all iter-1 waits precede iter-2 starts (last two), which precede
    # iter-2 waits (last two).
    assert starts[0] < starts[1] < waits[0] < waits[1]
    assert waits[1] < starts[2] < starts[3] < waits[2] < waits[3]


@pytest.mark.asyncio
async def test_handler_stops_when_select_next_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive guard: if the selector ever returns an empty list (no
    viable selections) the controller breaks out of the iteration loop
    rather than spinning. The current selector always emits at least
    ``num_workers`` items by falling back to draft creates, but the
    controller must not depend on that invariant."""
    import bfts_tree

    _patch_fanout_deps(monkeypatch)
    monkeypatch.setattr(bfts_tree, "select_next", lambda **_kw: [])

    ctx = _TreeCtx()
    out = await bfts_tree.handler(
        _input(num_drafts=4, num_workers=4, max_iters=5), ctx
    )

    assert ctx.start_workflow_calls == []
    assert out["iters_used"] == 0


@pytest.mark.asyncio
async def test_handler_respects_max_iters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the selector keeps emitting selections and no good node ever
    appears, the outer loop must stop after ``max_iters`` iterations.
    Each iteration starts ``num_workers`` children → total
    ``max_iters * num_workers`` start_workflow calls."""
    import bfts_tree

    _patch_fanout_deps(monkeypatch)

    ctx = _TreeCtx()
    out = await bfts_tree.handler(
        _input(num_drafts=2, num_workers=2, max_iters=3), ctx
    )

    assert out["iters_used"] == 3
    assert len(ctx.start_workflow_calls) == 6
    assert len(ctx.wait_for_workflow_calls) == 6


@pytest.mark.asyncio
async def test_handler_passes_parent_node_for_non_draft_selections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Debug/improve selections (``sel is not None``) must forward the
    parent node row dict so the child's prompt has the upstream code +
    stderr. Draft selections (``sel is None``) pass ``parent_node=None``.
    """
    import bfts_tree

    parent_row = {
        "node_id": "n-buggy-leaf",
        "parent_node_id": None,
        "is_buggy": True,
        "is_buggy_plots": None,
        "debug_depth": 0,
        "metric_json": None,
        "stage_name": "draft",
        "child_count": 0,
        "code": "raise ValueError()",
        "term_out_json": "[\"err\\n\"]",
    }
    # ``debug_prob=1.0`` forces the selector into the buggy-leaf branch
    # whenever ``buggy_leaves`` is non-empty (rng.random() < 1.0 is
    # always true), making this test deterministic regardless of seed.
    # ``num_drafts=1`` with the existing buggy root keeps the selector
    # off the phantom-draft fallback.
    _patch_fanout_deps(monkeypatch, list_nodes_returns=[[parent_row]])

    ctx = _TreeCtx()
    await bfts_tree.handler(
        _input(num_drafts=1, num_workers=1, max_iters=1, debug_prob=1.0), ctx
    )

    # The single fan-out call must carry the parent row dict for the
    # buggy leaf — the only node in the tree.
    assert len(ctx.start_workflow_calls) == 1
    ri = ctx.start_workflow_calls[0]["run_input"]
    assert ri["parent_node"] == parent_row


@pytest.mark.asyncio
async def test_handler_uses_deterministic_trigger_key_per_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``trigger_key`` must be derived from ``run_id`` + ``node_id`` so
    replays of the parent reuse the same child run rather than creating
    duplicates. Each child's returned ``run_id`` must match the
    ``trigger_key`` so ``wait_for_workflow`` can address the right run."""
    import bfts_tree

    _patch_fanout_deps(monkeypatch)

    ctx = _TreeCtx()
    await bfts_tree.handler(_input(num_drafts=2, num_workers=2, max_iters=1), ctx)

    assert len(ctx.start_workflow_calls) == 2
    triggers: set[str] = set()
    for call in ctx.start_workflow_calls:
        ri = call["run_input"]
        node_id = ri["node_id"]
        assert call["trigger_key"].startswith("r-tree-1:")
        assert node_id in call["trigger_key"]
        triggers.add(call["trigger_key"])
    assert len(triggers) == 2  # distinct per node
    # wait_for_workflow runs with the same run_id the start returned.
    assert set(ctx.wait_for_workflow_calls) == triggers
