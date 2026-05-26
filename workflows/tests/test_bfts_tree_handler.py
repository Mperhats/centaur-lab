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

from bfts_tree import WORKFLOW_NAME, Input, _should_terminate


def test_workflow_name() -> None:
    assert WORKFLOW_NAME == "bfts_tree"


def test_input_defaults() -> None:
    from _bfts_config import resolve_llm_settings

    inp = Input(run_id="r1", parent_run_id=None, idea={"name": "x"})
    # Phase 4c.4: search-policy fields default to None so the resolver
    # chain (Input → DB → env → default) still reaches lower tiers when
    # the parent forwarded the unresolved (or absent) value.
    assert inp.num_drafts is None
    assert inp.num_workers is None
    assert inp.max_debug_depth is None
    assert inp.debug_prob is None
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


class _TreeTools:
    """Stub ``ctx.tools`` surface for the bfts_tree handler.

    Only ``bfts_executor.{pause,resume}_sandbox`` are exercised by the
    handler — the per-iteration pause/resume lifecycle (see the
    ``docs/superpowers/plans/2026-05-26-bfts-pause-resume.md`` plan
    behind the WIP). ``AsyncMock`` lets individual tests assert call
    counts and per-call ``sandbox_id`` arguments when needed.
    """

    def __init__(self) -> None:
        self.bfts_executor = _TreeExecutorStub()


class _TreeExecutorStub:
    def __init__(self) -> None:
        self.pause_sandbox = AsyncMock(return_value=None)
        self.resume_sandbox = AsyncMock(return_value=None)


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

    def __init__(
        self,
        *,
        run_id: str = "r-tree-1",
        wait_results: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.run_id = run_id
        self._pool = object()
        self.calls: list[str] = []
        self.start_workflow_calls: list[dict[str, Any]] = []
        self.wait_for_workflow_calls: list[str] = []
        # Optional override map: {child_run_id: {"status": "failed", ...}}.
        # When set, ``wait_for_workflow`` returns the matched entry instead
        # of the default ``status="completed"``. Lets F.1 tests simulate
        # ``bfts_expand_one`` permafails without rewiring the whole stub.
        self.wait_results = wait_results or {}
        # ``ctx.tools.bfts_executor.{pause,resume}_sandbox`` are called by
        # the pause/resume WIP in ``bfts_tree.handler`` at iteration
        # boundaries (resume just-in-time before fan-out, pause after
        # every child reaches terminal, resume once more before the F.4
        # seed fan-out). The handler exclusively goes through ``ctx.step``
        # so the AsyncMocks below get awaited via the same recording
        # path as every other tool call; no separate counter needed.
        self.tools = _TreeTools()

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
        if run_id in self.wait_results:
            return {"run_id": run_id, **self.wait_results[run_id]}
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
    # F.6 follow-up: ``mark_run_completed`` is invoked unconditionally
    # at end-of-handler. Patch it for the same reason as the other DAOs
    # so the ``object()`` pool sentinel never reaches asyncpg.
    monkeypatch.setattr(
        bfts_tree, "mark_run_completed", AsyncMock(return_value=None)
    )
    # F.1: mark_node_failed is only invoked on a failed-child path; tests
    # without ``wait_results`` overrides never reach it but we patch it
    # for the same reason as the other DAOs — keep the ``object()`` pool
    # sentinel from leaking into a real asyncpg call.
    monkeypatch.setattr(bfts_tree, "mark_node_failed", AsyncMock(return_value=None))
    # F.4 seed fan-out DAOs. ``select_best`` is patched to ``None`` below
    # so the seed branch never fires in the existing tests; but the
    # patches must exist for the imports to resolve when seed tests
    # opt-in by overriding ``select_best``.
    monkeypatch.setattr(bfts_tree, "list_seed_children", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        bfts_tree, "update_node_aggregate_metric", AsyncMock(return_value=None)
    )

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
    monkeypatch.setattr(
        _bfts_export, "write_tree_dot_artifact", AsyncMock(return_value=None)
    )


def _input(
    *,
    num_drafts: int | None = 4,
    num_workers: int | None = 4,
    max_iters: int = 1,
    debug_prob: float | None = 0.5,
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

    insert_idxs = [i for i, c in enumerate(ctx.calls) if c.startswith("insert_node_")]
    start_idxs = [
        i for i, c in enumerate(ctx.calls) if c.startswith("start_expand_")
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

    starts = [i for i, c in enumerate(ctx.calls) if c.startswith("start_expand_")]
    waits = [i for i, c in enumerate(ctx.calls) if c.startswith("wait_expand_")]

    # Two iterations * num_workers=2 = 4 starts + 4 waits.
    assert len(starts) == 4
    assert len(waits) == 4

    # All iter-1 starts (first two) precede all iter-1 waits (next two);
    # all iter-1 waits precede iter-2 starts (last two), which precede
    # iter-2 waits (last two).
    assert starts[0] < starts[1] < waits[0] < waits[1]
    assert waits[1] < starts[2] < starts[3] < waits[2] < waits[3]


@pytest.mark.asyncio
async def test_handler_uses_node_id_in_fan_out_step_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay-debugging contract: step names embed the per-iteration index
    (``insert_node_{i}``) or the child ``node_id``
    (``start_expand_{node_id}`` / ``wait_expand_{node_id}``) so each step
    row in the checkpoint table can be correlated back to a specific
    selection / child without relying on the engine's auto-suffix
    (``#2``, ``#3``, ...). Matches the ``f"start_tree_{i}"`` /
    ``f"wait_tree_{child['tree_index']}"`` pattern in ``bfts_root``."""
    import bfts_tree

    _patch_fanout_deps(monkeypatch)

    ctx = _TreeCtx()
    await bfts_tree.handler(_input(num_drafts=3, num_workers=3, max_iters=1), ctx)

    inserts = [c for c in ctx.calls if c.startswith("insert_node_")]
    starts = [c for c in ctx.calls if c.startswith("start_expand_")]
    waits = [c for c in ctx.calls if c.startswith("wait_expand_")]

    assert len(inserts) == 3
    assert len(starts) == 3
    assert len(waits) == 3

    # Each step name must be unique within its phase — no reliance on the
    # engine's auto-suffix.
    assert len(set(inserts)) == 3
    assert len(set(starts)) == 3
    assert len(set(waits)) == 3

    # insert_node_{i} must use the per-iteration index 0..2.
    assert set(inserts) == {"insert_node_0", "insert_node_1", "insert_node_2"}

    # start_expand_{node_id} and wait_expand_{node_id} share the same
    # suffix set — every started child is awaited exactly once.
    start_suffixes = {c[len("start_expand_"):] for c in starts}
    wait_suffixes = {c[len("wait_expand_"):] for c in waits}
    assert start_suffixes == wait_suffixes

    # The suffix in each name matches the node_id carried in the child's
    # run_input.
    started_node_ids = {
        call["run_input"]["node_id"] for call in ctx.start_workflow_calls
    }
    assert start_suffixes == started_node_ids


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


# --- Phase 4c.4 follow-up: replay-determinism + sources persistence -----


@pytest.mark.asyncio
async def test_handler_persists_resolved_search_config_to_insert_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bfts_runs.config_json snapshot must hold RESOLVED values, not
    raw Input — otherwise replay diverges from the original run when the
    bfts_hyperparams row is rewritten between runs.

    Lock the env-tier win path here so a future "let's just store inp"
    refactor regresses loudly."""
    import bfts_tree

    monkeypatch.setenv("BFTS_NUM_WORKERS", "7")
    _patch_fanout_deps(monkeypatch)
    # Override insert_run AFTER _patch_fanout_deps so the recording
    # mock is the one the handler invokes (the helper installs a
    # discard-only stub).
    insert_run_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(bfts_tree, "insert_run", insert_run_mock)

    ctx = _TreeCtx()
    await bfts_tree.handler(
        _input(num_workers=None, num_drafts=1, max_iters=1), ctx
    )

    config = insert_run_mock.await_args.kwargs["config"]
    # Resolved from env, not raw None — replay must reproduce 7.
    assert config["num_workers"] == 7


@pytest.mark.asyncio
async def test_handler_persists_sources_in_config_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``config_json["sources"]`` must record which tier won each
    field so the postmortem ``why-did-this-run-use-X`` query is one
    SELECT against ``bfts_runs``."""
    import bfts_tree

    monkeypatch.setenv("BFTS_NUM_WORKERS", "7")
    monkeypatch.delenv("BFTS_DEBUG_PROB", raising=False)
    monkeypatch.delenv("BFTS_MAX_DEBUG_DEPTH", raising=False)
    monkeypatch.delenv("BFTS_NUM_DRAFTS", raising=False)
    monkeypatch.delenv("BFTS_METRIC_REDUCER", raising=False)

    _patch_fanout_deps(monkeypatch)
    insert_run_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(bfts_tree, "insert_run", insert_run_mock)

    ctx = _TreeCtx()
    # Input override on debug_prob; everything else falls through to env
    # (num_workers) or default (max_debug_depth, num_drafts, metric_reducer).
    await bfts_tree.handler(
        _input(
            num_drafts=1,
            num_workers=None,
            max_iters=1,
            debug_prob=0.42,
        ),
        ctx,
    )

    config = insert_run_mock.await_args.kwargs["config"]
    sources = config["sources"]
    assert sources["debug_prob"] == "input"
    assert sources["num_workers"] == "env"
    assert sources["num_drafts"] == "input"  # _input passes num_drafts=1
    assert sources["max_debug_depth"] == "default"
    assert sources["metric_reducer"] == "default"


@pytest.mark.asyncio
async def test_handler_persists_num_seeds_and_prior_attempts_in_config_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``config_json`` must include the RESOLVED ``num_seeds`` and
    ``prior_attempts_window`` so an operator can post-hoc answer
    "did this run use seed re-eval / memory injection?" from
    ``bfts_runs`` alone — without re-resolving env / DB tiers.

    Before the F.6 follow-up these two fields were tracked in
    ``sources`` but the resolved values themselves never made it into
    the persisted snapshot, so a Slack-driven smoke run looked
    indistinguishable from a Phase-0 run in the database. Lock the
    presence of both fields here so a future "trim the config dict"
    refactor regresses loudly.
    """
    import bfts_tree

    monkeypatch.setenv("BFTS_NUM_SEEDS", "3")
    monkeypatch.setenv("BFTS_PRIOR_ATTEMPTS_WINDOW", "5")
    _patch_fanout_deps(monkeypatch)
    insert_run_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(bfts_tree, "insert_run", insert_run_mock)

    ctx = _TreeCtx()
    await bfts_tree.handler(_input(num_drafts=1, max_iters=1), ctx)

    config = insert_run_mock.await_args.kwargs["config"]
    assert config["num_seeds"] == 3
    assert config["prior_attempts_window"] == 5
    # And sources still record where each value came from.
    assert config["sources"]["num_seeds"] == "env"
    assert config["sources"]["prior_attempts_window"] == "env"


# ---------------------------------------------------------------------------
# F.6 follow-up: every bfts_tree.handler run must flip
# bfts_runs.status to 'completed' — including runs that produced no
# good leaf (all-buggy tree). Previously status only moved when
# set_best_node was called, leaving all-buggy runs stuck in 'running'
# forever and producing the orphan rows observed in the 2026-05-26
# Slack smoke.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_marks_run_completed_when_no_best_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All-buggy tree path: ``select_best`` returns ``None`` so
    ``set_best_node`` is never called. The handler must still call
    ``mark_run_completed`` so ``bfts_runs.status`` leaves ``running``.
    """
    import bfts_tree

    _patch_fanout_deps(monkeypatch)
    mark_completed_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        bfts_tree, "mark_run_completed", mark_completed_mock
    )

    ctx = _TreeCtx()
    await bfts_tree.handler(_input(num_drafts=1, max_iters=1), ctx)

    mark_completed_mock.assert_awaited_once()
    assert mark_completed_mock.await_args.kwargs["run_id"] == "r-tree-1"


@pytest.mark.asyncio
async def test_handler_marks_run_completed_when_best_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: a good leaf is selected so ``set_best_node`` runs.
    ``mark_run_completed`` MUST also run — it's the single writer for
    ``bfts_runs.status`` and is idempotent on the success path."""
    import _bfts_export
    import bfts_tree

    _patch_fanout_deps(monkeypatch)
    # Promote a synthetic best node so the export branch fires.
    monkeypatch.setattr(
        _bfts_export,
        "select_best",
        lambda _nodes, **_kw: {
            "node_id": "best-1",
            "code": "print('hi')",
            "stage_name": "draft",
            "metric_json": {"final_value": 0.42},
        },
    )
    mark_completed_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        bfts_tree, "mark_run_completed", mark_completed_mock
    )

    ctx = _TreeCtx()
    await bfts_tree.handler(_input(num_drafts=1, max_iters=1), ctx)

    mark_completed_mock.assert_awaited_once()
    assert mark_completed_mock.await_args.kwargs["run_id"] == "r-tree-1"


@pytest.mark.asyncio
async def test_handler_mark_run_completed_step_runs_after_seed_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``mark_run_completed`` step name must appear in ``ctx.calls``
    AFTER the F.4 seed aggregate write so a workflow replay that
    short-circuits earlier checkpoints still reaches the status
    transition. Locks the call ordering so a future refactor that
    moves the call back above the seed block regresses loudly.
    """
    import _bfts_export
    import bfts_tree

    _patch_fanout_deps(monkeypatch)
    monkeypatch.setattr(
        _bfts_export,
        "select_best",
        lambda _nodes, **_kw: {
            "node_id": "best-1",
            "code": "print('hi')",
            "stage_name": "draft",
            "metric_json": {"final_value": 0.42},
        },
    )
    monkeypatch.setenv("BFTS_NUM_SEEDS", "1")

    ctx = _TreeCtx()
    await bfts_tree.handler(_input(num_drafts=1, max_iters=1), ctx)

    assert "mark_run_completed" in ctx.calls
    # Must come AFTER list_seed_children + any seed waits / writes.
    mark_idx = ctx.calls.index("mark_run_completed")
    seed_idx = ctx.calls.index("list_seed_children")
    assert mark_idx > seed_idx


# ---------------------------------------------------------------------------
# F.1: a failed bfts_expand_one child must result in mark_node_failed
# being called on its placeholder row so the next iteration's selector
# treats it as a buggy leaf rather than a stalled draft slot.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_expand_child_marks_node_buggy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One ``start_workflow`` per draft selection; ``wait_for_workflow``
    returns ``status="failed"`` for one child; the handler must dispatch
    a ``mark_failed_<node_id>`` step that calls ``mark_node_failed`` with
    ``exc_type='ChildWorkflowFailed'`` + the child's error excerpt."""
    import bfts_tree

    _patch_fanout_deps(monkeypatch)
    mark_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(bfts_tree, "mark_node_failed", mark_mock)

    ctx = _TreeCtx()
    # First child fails permanently; second completes normally. The
    # handler runs both starts before issuing any wait, so we don't know
    # the child trigger_keys up front — patch them after the fact by
    # mutating ``ctx.wait_results`` once the first ``start_workflow``
    # records its trigger_key.

    original_start = ctx.start_workflow

    async def _start(*args, **kwargs):
        child = await original_start(*args, **kwargs)
        if len(ctx.start_workflow_calls) == 1:
            ctx.wait_results[child["run_id"]] = {
                "status": "failed",
                "error": "executor pod evicted",
            }
        return child

    ctx.start_workflow = _start

    await bfts_tree.handler(
        _input(num_drafts=2, num_workers=2, max_iters=1), ctx
    )

    # Exactly one ``mark_failed_<node_id>`` step recorded on ctx.calls,
    # AFTER the matching ``wait_expand_<node_id>``.
    mark_steps = [c for c in ctx.calls if c.startswith("mark_failed_")]
    assert len(mark_steps) == 1, ctx.calls
    mark_idx = ctx.calls.index(mark_steps[0])
    failed_node_id = mark_steps[0][len("mark_failed_") :]
    wait_idx = ctx.calls.index(f"wait_expand_{failed_node_id}")
    assert wait_idx < mark_idx

    # The DAO was called with the synthetic exc_type + the child error.
    assert mark_mock.await_count == 1
    kw = mark_mock.await_args.kwargs
    assert kw["node_id"] == failed_node_id
    assert kw["exc_type"] == "ChildWorkflowFailed"
    assert kw["exc_info"]["child_status"] == "failed"
    assert kw["exc_info"]["error"] == "executor pod evicted"
    assert "status=failed" in kw["analysis"]


@pytest.mark.asyncio
async def test_completed_children_do_not_call_mark_node_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default path: every child returns ``status="completed"`` →
    ``mark_node_failed`` must not be called. Locks in that the F.1
    branch only fires on real failures (no false positives)."""
    import bfts_tree

    _patch_fanout_deps(monkeypatch)
    mark_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(bfts_tree, "mark_node_failed", mark_mock)

    ctx = _TreeCtx()
    await bfts_tree.handler(
        _input(num_drafts=3, num_workers=3, max_iters=1), ctx
    )

    assert mark_mock.await_count == 0
    assert not any(c.startswith("mark_failed_") for c in ctx.calls)


# ---------------------------------------------------------------------------
# F.3: tree.dot artifact is written at the end of every run.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tree_dot_artifact_is_written_when_run_has_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even with no best node selected, ``write_tree_dot_artifact`` must
    fire with the final ``list_nodes_for_run`` snapshot anchored on the
    first node (so an operator can debug a failed run too)."""
    import _bfts_export
    import bfts_tree

    final_nodes = [
        {
            "node_id": "n-only", "parent_node_id": None,
            "stage_name": "draft", "is_buggy": True, "is_buggy_plots": None,
            "metric_json": None, "debug_depth": 0,
        }
    ]
    _patch_fanout_deps(monkeypatch, list_nodes_returns=[final_nodes, final_nodes])
    write_dot_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(_bfts_export, "write_tree_dot_artifact", write_dot_mock)

    ctx = _TreeCtx()
    await bfts_tree.handler(
        _input(num_drafts=1, num_workers=1, max_iters=1), ctx
    )

    assert "write_tree_dot" in ctx.calls
    write_dot_mock.assert_awaited_once()
    kw = write_dot_mock.await_args.kwargs
    assert kw["run_id"] == "r-tree-1"
    assert kw["anchor_node_id"] == "n-only"
    assert "digraph" in kw["dot_text"]


@pytest.mark.asyncio
async def test_tree_dot_skipped_when_run_has_zero_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero rows → no anchor → ``write_tree_dot_artifact`` skipped
    silently (the FK on bfts_artifacts.node_id would otherwise crash)."""
    import _bfts_export
    import bfts_tree

    _patch_fanout_deps(monkeypatch, list_nodes_returns=[[]])
    write_dot_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(_bfts_export, "write_tree_dot_artifact", write_dot_mock)

    ctx = _TreeCtx()
    # max_iters=0 short-circuits the outer loop so we exit with zero
    # placeholders. (max_iters=1 + num_drafts=1 would insert 1
    # placeholder and we'd take the anchor branch.)
    inp = Input(
        run_id="r-tree-1",
        parent_run_id=None,
        idea={"name": "x"},
        num_drafts=1,
        num_workers=1,
        max_iters=0,
        debug_prob=0.5,
        sandbox_id="bfts-r-tree-1-tree-0",
        draft_model="claude-draft-test",
        feedback_model="claude-feedback-test",
        vlm_model="claude-vision-test",
        llm_api_key_secret="TEST_API_KEY",
    )
    await bfts_tree.handler(inp, ctx)

    write_dot_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# F.4: multi-seed re-eval fan-out + aggregate write.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_num_seeds_triggers_seed_fan_out_after_best(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``num_seeds=3`` + a best node selected → 3 seed children fanned
    out (deterministic IDs, ``seed_override`` set per index), waited on,
    then ``write_aggregate_metric`` step writes the aggregate."""
    import _bfts_export
    import bfts_tree

    best_row = {
        "node_id": "best-1",
        "code": "print('hi')",
        "is_buggy": False,
        "is_buggy_plots": False,
    }
    _patch_fanout_deps(monkeypatch, list_nodes_returns=[[], [best_row]])
    # Override the patched ``select_best`` to actually pick our best row.
    monkeypatch.setattr(_bfts_export, "select_best", lambda _nodes, **_kw: best_row)
    # Provide synthetic seed children with concrete final_value scalars
    # so the aggregator computes a non-None result.
    seed_rows = [
        {"node_id": "s-0", "seed": 0,
         "metric_json": {"final_value": 0.4}, "is_buggy": False},
        {"node_id": "s-1", "seed": 1,
         "metric_json": {"final_value": 0.6}, "is_buggy": False},
        {"node_id": "s-2", "seed": 2,
         "metric_json": {"final_value": 0.5}, "is_buggy": False},
    ]
    monkeypatch.setattr(
        bfts_tree, "list_seed_children", AsyncMock(return_value=seed_rows)
    )
    update_agg = AsyncMock(return_value=None)
    monkeypatch.setattr(bfts_tree, "update_node_aggregate_metric", update_agg)

    ctx = _TreeCtx()
    inp = Input(
        run_id="r-tree-1",
        parent_run_id=None,
        idea={"name": "x"},
        num_drafts=1,
        num_workers=1,
        max_iters=1,
        debug_prob=0.5,
        num_seeds=3,
        sandbox_id="bfts-r-tree-1-tree-0",
        draft_model="claude-draft-test",
        feedback_model="claude-feedback-test",
        vlm_model="claude-vision-test",
        llm_api_key_secret="TEST_API_KEY",
    )
    await bfts_tree.handler(inp, ctx)

    seed_starts = [
        c for c in ctx.start_workflow_calls
        if c["name"].startswith("start_seed_")
    ]
    assert len(seed_starts) == 3
    seed_indices = []
    for call in seed_starts:
        ri = call["run_input"]
        assert ri["is_seed_node"] is True
        assert ri["seed_override"] == int(call["name"].split("_")[-1])
        assert ri["parent_node"] == best_row
        seed_indices.append(ri["seed_override"])
    assert sorted(seed_indices) == [0, 1, 2]

    # write_aggregate_metric fires with the computed mean/std/n.
    update_agg.assert_awaited_once()
    kw = update_agg.await_args.kwargs
    assert kw["node_id"] == "best-1"
    agg = kw["aggregate"]
    assert agg["aggregate_n"] == 3.0
    assert agg["aggregate_mean"] == pytest.approx(0.5)
    assert agg["aggregate_std"] > 0


@pytest.mark.asyncio
async def test_num_seeds_zero_skips_seed_fan_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default ``num_seeds=0`` → no ``start_seed_*`` calls, no aggregate
    write. Preserves the Phase 0-4 contract on the happy path."""
    import _bfts_export
    import bfts_tree

    best_row = {
        "node_id": "best-1",
        "code": "print('hi')",
        "is_buggy": False,
        "is_buggy_plots": False,
    }
    _patch_fanout_deps(monkeypatch, list_nodes_returns=[[], [best_row]])
    monkeypatch.setattr(_bfts_export, "select_best", lambda _nodes, **_kw: best_row)
    update_agg = AsyncMock(return_value=None)
    monkeypatch.setattr(bfts_tree, "update_node_aggregate_metric", update_agg)

    ctx = _TreeCtx()
    await bfts_tree.handler(
        _input(num_drafts=1, num_workers=1, max_iters=1), ctx
    )

    assert not any(c["name"].startswith("start_seed_") for c in ctx.start_workflow_calls)
    update_agg.assert_not_awaited()


@pytest.mark.asyncio
async def test_num_seeds_with_no_best_skips_seed_fan_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``num_seeds=3`` but ``select_best`` returns None → no seed
    fan-out (no parent code to re-execute)."""
    import bfts_tree

    _patch_fanout_deps(monkeypatch)
    update_agg = AsyncMock(return_value=None)
    monkeypatch.setattr(bfts_tree, "update_node_aggregate_metric", update_agg)

    ctx = _TreeCtx()
    await bfts_tree.handler(
        Input(
            run_id="r-tree-1",
            parent_run_id=None,
            idea={"name": "x"},
            num_drafts=1,
            num_workers=1,
            max_iters=1,
            debug_prob=0.5,
            num_seeds=3,
            sandbox_id="bfts-r-tree-1-tree-0",
            draft_model="claude-draft-test",
            feedback_model="claude-feedback-test",
            vlm_model="claude-vision-test",
            llm_api_key_secret="TEST_API_KEY",
        ),
        ctx,
    )

    assert not any(c["name"].startswith("start_seed_") for c in ctx.start_workflow_calls)
    update_agg.assert_not_awaited()


# ---------------------------------------------------------------------------
# F.4: ``_aggregate_seed_metrics`` aggregator helper unit tests.
# ---------------------------------------------------------------------------


def test_aggregate_seed_metrics_skips_buggy_children() -> None:
    """A buggy seed child is excluded so a single crash doesn't poison
    the mean; one healthy child is enough to produce an aggregate.

    With ``n=1`` ``aggregate_std`` is ``None`` rather than ``0.0`` —
    the previous ``std=0.0`` was misleading because it looked like
    "we observed zero variance" instead of "we don't have enough
    samples to estimate variance" (which is what really happened
    when one seed silently failed and only the other made it through).
    """
    from bfts_tree import _aggregate_seed_metrics

    out = _aggregate_seed_metrics(
        [
            {"is_buggy": True, "metric_json": {"final_value": 0.0}},
            {"is_buggy": False, "metric_json": {"final_value": 0.5}},
        ]
    )

    assert out is not None
    assert out["aggregate_n"] == 1.0
    assert out["aggregate_mean"] == pytest.approx(0.5)
    assert out["aggregate_std"] is None


def test_aggregate_seed_metrics_handles_nested_schema() -> None:
    """The newer ``metric_names[*].data[*].final_value`` shape is read
    correctly so seed nodes whose parse step emitted the multi-metric
    payload still contribute to the aggregate."""
    from bfts_tree import _aggregate_seed_metrics

    nested = {
        "metric_names": [
            {"data": [{"final_value": 0.3}, {"final_value": 0.9}]}
        ]
    }
    out = _aggregate_seed_metrics(
        [
            {"is_buggy": False, "metric_json": nested},
            {"is_buggy": False, "metric_json": nested},
        ]
    )

    assert out is not None
    assert out["aggregate_n"] == 2.0
    assert out["aggregate_mean"] == pytest.approx(0.3)


def test_aggregate_seed_metrics_returns_none_when_no_usable_value() -> None:
    """All children buggy → no usable scalar → ``None`` so the caller
    skips the aggregate write."""
    from bfts_tree import _aggregate_seed_metrics

    out = _aggregate_seed_metrics(
        [
            {"is_buggy": True, "metric_json": {"final_value": 0.0}},
            {"is_buggy": True, "metric_json": None},
        ]
    )

    assert out is None


# ---------------------------------------------------------------------------
# F.6: rich handler return value carries best_metric_json, artifact ids,
# seed aggregate + seed children, and the resolved config snapshot.
# Slack-driven runs read these via ``call workflow get <run_id>`` →
# ``output_json``; without this surface the agent has no DB access path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_return_carries_best_and_artifact_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful run: return value carries ``best_node_id``,
    ``best_metric_json``, ``best_solution_artifact_id``,
    ``best_node_id_artifact_id``, and ``tree_dot_artifact_id``."""
    import _bfts_export
    import bfts_tree

    best_row = {
        "node_id": "best-1",
        "code": "print('hi')",
        "stage_name": "improve",
        "is_buggy": False,
        "is_buggy_plots": False,
        "metric_json": {"final_value": 0.123},
    }
    _patch_fanout_deps(monkeypatch, list_nodes_returns=[[best_row], [best_row]])
    monkeypatch.setattr(_bfts_export, "select_best", lambda _nodes, **_kw: best_row)
    monkeypatch.setattr(
        _bfts_export, "write_best_artifact",
        AsyncMock(return_value="art-best-uuid"),
    )
    monkeypatch.setattr(
        _bfts_export, "write_best_node_id_artifact",
        AsyncMock(return_value="art-bestid-uuid"),
    )
    monkeypatch.setattr(
        _bfts_export, "write_tree_dot_artifact",
        AsyncMock(return_value="r-tree-1:tree.dot"),
    )

    ctx = _TreeCtx()
    out = await bfts_tree.handler(
        _input(num_drafts=1, num_workers=1, max_iters=1), ctx
    )

    assert out["best_node_id"] == "best-1"
    assert out["best_metric_json"] == {"final_value": 0.123}
    assert out["best_stage_name"] == "improve"
    assert out["best_solution_artifact_id"] == "art-best-uuid"
    assert out["best_node_id_artifact_id"] == "art-bestid-uuid"
    assert out["tree_dot_artifact_id"] == "r-tree-1:tree.dot"


@pytest.mark.asyncio
async def test_handler_return_has_null_best_fields_when_no_good_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run with no good nodes returns ``best_*=None`` but still emits
    a ``tree_dot_artifact_id`` (anchored on the first row of final_nodes)
    so postmortem rendering works on failed runs too."""
    import _bfts_export
    import bfts_tree

    only_buggy = [{
        "node_id": "n-only", "parent_node_id": None,
        "stage_name": "draft", "is_buggy": True, "is_buggy_plots": None,
        "metric_json": None,
    }]
    _patch_fanout_deps(monkeypatch, list_nodes_returns=[only_buggy, only_buggy])
    monkeypatch.setattr(_bfts_export, "select_best", lambda _nodes, **_kw: None)
    monkeypatch.setattr(
        _bfts_export, "write_tree_dot_artifact",
        AsyncMock(return_value="r-tree-1:tree.dot"),
    )

    ctx = _TreeCtx()
    out = await bfts_tree.handler(
        _input(num_drafts=1, num_workers=1, max_iters=1), ctx
    )

    assert out["best_node_id"] is None
    assert out["best_metric_json"] is None
    assert out["best_solution_artifact_id"] is None
    assert out["best_node_id_artifact_id"] is None
    # tree.dot is still written so the operator can debug the failed run.
    assert out["tree_dot_artifact_id"] == "r-tree-1:tree.dot"
    # Seed aggregate empty because there's no best to re-evaluate.
    assert out["seed_aggregate"] is None
    assert out["seed_children"] == []


@pytest.mark.asyncio
async def test_handler_return_carries_seed_aggregate_and_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F.4 fan-out: ``seed_aggregate`` and ``seed_children`` are populated
    from the seed DAO result and the aggregator output."""
    import _bfts_export
    import bfts_tree

    best_row = {
        "node_id": "best-1",
        "code": "print('hi')",
        "stage_name": "improve",
        "is_buggy": False,
        "is_buggy_plots": False,
        "metric_json": {"final_value": 0.5},
    }
    _patch_fanout_deps(monkeypatch, list_nodes_returns=[[best_row], [best_row]])
    monkeypatch.setattr(_bfts_export, "select_best", lambda _nodes, **_kw: best_row)
    seed_rows = [
        {"node_id": "s0", "seed": 0,
         "metric_json": {"final_value": 0.4}, "is_buggy": False},
        {"node_id": "s1", "seed": 1,
         "metric_json": {"final_value": 0.6}, "is_buggy": False},
    ]
    monkeypatch.setattr(
        bfts_tree, "list_seed_children", AsyncMock(return_value=seed_rows)
    )
    monkeypatch.setattr(
        bfts_tree, "update_node_aggregate_metric", AsyncMock(return_value=None)
    )

    ctx = _TreeCtx()
    inp = Input(
        run_id="r-tree-1",
        parent_run_id=None,
        idea={"name": "x"},
        num_drafts=1,
        num_workers=1,
        max_iters=1,
        debug_prob=0.5,
        num_seeds=2,
        sandbox_id="bfts-r-tree-1-tree-0",
        draft_model="claude-draft-test",
        feedback_model="claude-feedback-test",
        vlm_model="claude-vision-test",
        llm_api_key_secret="TEST_API_KEY",
    )
    out = await bfts_tree.handler(inp, ctx)

    assert out["seed_aggregate"] is not None
    assert out["seed_aggregate"]["aggregate_n"] == 2.0
    assert out["seed_aggregate"]["aggregate_mean"] == pytest.approx(0.5)
    # Projected to (seed, node_id, is_buggy, final_value) only.
    assert out["seed_children"] == [
        {"node_id": "s0", "seed": 0, "is_buggy": False, "final_value": 0.4},
        {"node_id": "s1", "seed": 1, "is_buggy": False, "final_value": 0.6},
    ]


def test_coerce_metric_json_handles_dict_str_and_none() -> None:
    """``_coerce_metric_json`` accepts the three asyncpg jsonb return
    shapes (parsed dict, raw JSON string, NULL) and surfaces None for
    malformed input rather than crashing the whole return."""
    import json as _json

    from bfts_tree import _coerce_metric_json

    assert _coerce_metric_json({"a": 1}) == {"a": 1}
    assert _coerce_metric_json(_json.dumps({"b": 2})) == {"b": 2}
    assert _coerce_metric_json(None) is None
    assert _coerce_metric_json("not json") is None
    # Non-dict JSON (e.g. a literal number) is also rejected.
    assert _coerce_metric_json("42") is None


def test_project_seed_children_reads_nested_metric_schema() -> None:
    """``_project_seed_children`` falls back to the nested
    ``metric_names[*].data[*].final_value`` shape when ``final_value``
    isn't a top-level key."""
    from bfts_tree import _project_seed_children

    out = _project_seed_children([
        {
            "node_id": "s0", "seed": 0, "is_buggy": False,
            "metric_json": {
                "metric_names": [{"data": [{"final_value": 0.42}]}]
            },
        }
    ])

    assert out == [
        {"node_id": "s0", "seed": 0, "is_buggy": False, "final_value": 0.42}
    ]


def test_project_seed_children_emits_none_final_value_for_buggy_or_missing() -> None:
    """Buggy seeds and seeds with unreadable metric_json get
    ``final_value=None`` rather than being dropped — operators need to
    see every seed child to count failures."""
    from bfts_tree import _project_seed_children

    out = _project_seed_children([
        {"node_id": "s-bug", "seed": 0, "is_buggy": True, "metric_json": None},
        {"node_id": "s-bad", "seed": 1, "is_buggy": False,
         "metric_json": "not json"},
    ])

    assert out == [
        {"node_id": "s-bug", "seed": 0, "is_buggy": True, "final_value": None},
        {"node_id": "s-bad", "seed": 1, "is_buggy": False, "final_value": None},
    ]
