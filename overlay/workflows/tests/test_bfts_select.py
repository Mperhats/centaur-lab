"""Property tests for _bfts_select.select_next (Sakana parity)."""
from __future__ import annotations

import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_select import NodeRef, SearchConfig, select_next
from bfts_tree import _to_noderef


@dataclass
class _N:
    """Minimal node shape mirroring the bfts_nodes row columns the
    selector reads. Keeps the test isolated from the real DAO."""

    node_id: str
    parent_id: Optional[str]
    is_buggy: Optional[bool]
    is_buggy_plots: Optional[bool]
    debug_depth: int
    metric_score: float                                # _bfts_metric.score(...)
    stage_name: str = "draft"
    is_leaf: bool = True

    def to_ref(self) -> NodeRef:
        return NodeRef(
            node_id=self.node_id,
            parent_id=self.parent_id,
            root_id=self.node_id if self.parent_id is None else "ROOT",
            is_buggy=self.is_buggy,
            is_buggy_plots=self.is_buggy_plots,
            debug_depth=self.debug_depth,
            metric_score=self.metric_score,
            stage_name=self.stage_name,
            is_leaf=self.is_leaf,
        )


def test_drafts_until_num_drafts_reached() -> None:
    cfg = SearchConfig(num_drafts=3, num_workers=4, max_debug_depth=3, debug_prob=0.0)
    rng = random.Random(0)
    # No nodes yet: selector must produce 3 None entries (each = "new draft"),
    # plus a 4th entry that's also None (still drafting until num_drafts).
    selected = select_next(nodes=[], cfg=cfg, rng=rng)
    assert selected == [None, None, None, None]


def test_no_debug_when_prob_is_zero_and_good_node_exists() -> None:
    cfg = SearchConfig(num_drafts=2, num_workers=2, max_debug_depth=3, debug_prob=0.0)
    rng = random.Random(0)
    nodes = [
        _N("d1", None, is_buggy=False, is_buggy_plots=False, debug_depth=0, metric_score=0.5).to_ref(),
        _N("d2", None, is_buggy=False, is_buggy_plots=False, debug_depth=0, metric_score=0.7).to_ref(),
    ]
    selected = select_next(nodes=nodes, cfg=cfg, rng=rng)
    # Expectation: improve the best of each tree (one slot per root, per
    # Sakana's "one node per tree per step").
    ids = [n.node_id if n else None for n in selected]
    assert set(ids) == {"d1", "d2"}


def test_debug_chosen_when_prob_is_one_and_buggy_leaf_exists() -> None:
    cfg = SearchConfig(num_drafts=1, num_workers=1, max_debug_depth=3, debug_prob=1.0)
    rng = random.Random(0)
    nodes = [
        _N("d1", None, is_buggy=True, is_buggy_plots=None, debug_depth=0, metric_score=float("inf")).to_ref(),
    ]
    selected = select_next(nodes=nodes, cfg=cfg, rng=rng)
    assert [n.node_id for n in selected if n] == ["d1"]


def test_max_debug_depth_excludes_node() -> None:
    cfg = SearchConfig(num_drafts=1, num_workers=1, max_debug_depth=3, debug_prob=1.0)
    rng = random.Random(0)
    # debug_depth above the cap (4 > 3) → ineligible for further debugging.
    n = _N("dx", "parent", is_buggy=True, is_buggy_plots=None, debug_depth=4, metric_score=float("inf")).to_ref()
    selected = select_next(nodes=[n], cfg=cfg, rng=rng)
    # No debuggable, no good_nodes → fall back to drafting (None).
    assert selected == [None]


def test_buggy_internal_node_not_selected_for_debug() -> None:
    """Sakana's selector debugs only buggy LEAVES — a buggy internal node
    whose subtree has already been expanded (one good child) must not
    appear in ``_buggy_leaf_nodes`` and must not be picked for re-debugging.

    Fixture: parent is buggy but has a good (non-buggy) child. Pre-fix,
    ``_to_noderef`` hardcoded ``is_leaf=True`` for every row so
    ``_buggy_leaf_nodes`` returned ``[parent]`` and the selector — with
    ``debug_prob=1.0`` and only one buggy candidate — deterministically
    picked the parent for re-debug. Post-fix, ``child_count=1`` flips
    ``is_leaf`` to ``False`` on the parent, leaving ``_buggy_leaf_nodes``
    empty and forcing the selector to fall through to improving the good
    child. The DAO-row → ``_to_noderef`` → ``select_next`` round-trip is
    intentional so both halves of the fix (DAO column + ref construction)
    are covered.
    """
    parent_row = {
        "node_id": "aaaa-parent",
        "parent_node_id": None,
        "is_buggy": True,
        "is_buggy_plots": None,
        "debug_depth": 0,
        "metric_json": None,
        "stage_name": "draft",
        "child_count": 1,
    }
    child_row = {
        "node_id": "bbbb-child",
        "parent_node_id": "aaaa-parent",
        "is_buggy": False,
        "is_buggy_plots": False,
        "debug_depth": 1,
        "metric_json": None,
        "stage_name": "debug",
        "child_count": 0,
    }

    parent_ref = _to_noderef(parent_row)
    child_ref = _to_noderef(child_row)

    assert parent_ref.is_leaf is False, "internal node (has a child) must not be is_leaf"
    assert child_ref.is_leaf is True, "leaf node (no children) must be is_leaf"

    cfg = SearchConfig(num_drafts=1, num_workers=1, max_debug_depth=3, debug_prob=1.0)
    selected = select_next(nodes=[parent_ref, child_ref], cfg=cfg, rng=random.Random(0))

    # The internal parent must NEVER be picked for debug. With the child
    # flipped to good, pre-fix this assertion failed deterministically:
    # ``_buggy_leaf_nodes`` saw the parent as a leaf (the hardcoded bug)
    # and ``debug_prob=1.0`` plus a single candidate forced the selector
    # to return [parent].
    assert all(
        sel is None or sel.node_id != parent_ref.node_id for sel in selected
    ), f"internal buggy node was selected for debug: {selected}"


# ---------------------------------------------------------------------------
# F.4: ``is_seed_node`` rows must be invisible to every selector pathway —
# drafts, debug-leaves, and good-candidates for improve. A seed re-eval
# carries no new information (same code + different seed), so re-running
# it would waste a worker slot.
# ---------------------------------------------------------------------------


def test_seed_node_excluded_from_draft_candidates() -> None:
    """A seed-marked row at the root layer must NOT count toward
    ``num_drafts`` (no parent_id, but flagged seed). Tree appears empty,
    selector emits ``num_workers`` Nones."""
    cfg = SearchConfig(num_drafts=2, num_workers=2, max_debug_depth=3, debug_prob=0.0)
    seed_root = _N(
        "seed-root", None, is_buggy=False, is_buggy_plots=False,
        debug_depth=0, metric_score=0.5, stage_name="seed", is_leaf=True,
    ).to_ref()
    # Patch in the flag via dataclasses.replace.
    import dataclasses
    seed_root = dataclasses.replace(seed_root, is_seed_node=True)

    selected = select_next(nodes=[seed_root], cfg=cfg, rng=random.Random(0))

    assert selected == [None, None]


def test_seed_node_excluded_from_good_improve_candidates() -> None:
    """A seed-flagged row with a clean metric must NOT be picked for the
    improve branch — only the parent that produced it should be picked.
    """
    cfg = SearchConfig(num_drafts=1, num_workers=1, max_debug_depth=3, debug_prob=0.0)
    parent = _N(
        "parent", None, is_buggy=False, is_buggy_plots=False,
        debug_depth=0, metric_score=0.5,
    ).to_ref()
    seed_child = _N(
        "seed-0", "parent", is_buggy=False, is_buggy_plots=False,
        debug_depth=1, metric_score=0.4, stage_name="seed", is_leaf=True,
    ).to_ref()
    import dataclasses
    seed_child = dataclasses.replace(seed_child, is_seed_node=True)

    selected = select_next(
        nodes=[parent, seed_child], cfg=cfg, rng=random.Random(0)
    )

    assert len(selected) == 1
    assert selected[0] is not None
    assert selected[0].node_id == "parent"


def test_seed_node_excluded_from_buggy_debug_candidates() -> None:
    """A seed re-eval that crashed (``is_buggy=True``) must NOT enter
    the debug pool — debugging it is a futile retry of the same code
    with the same seed."""
    cfg = SearchConfig(num_drafts=1, num_workers=1, max_debug_depth=3, debug_prob=1.0)
    seed_buggy = _N(
        "seed-bug", "parent", is_buggy=True, is_buggy_plots=None,
        debug_depth=0, metric_score=float("inf"),
        stage_name="seed", is_leaf=True,
    ).to_ref()
    import dataclasses
    seed_buggy = dataclasses.replace(seed_buggy, is_seed_node=True)

    selected = select_next(
        nodes=[seed_buggy], cfg=cfg, rng=random.Random(0)
    )

    # No draftable / debuggable / improvable nodes → emit a single None
    # (fall back to drafting a fresh root).
    assert selected == [None]


def test_to_noderef_propagates_is_seed_node_column() -> None:
    """The DAO row → NodeRef conversion must carry the ``is_seed_node``
    column through unchanged so the selector can apply its filter."""
    seed_row: dict = {
        "node_id": "s0",
        "parent_node_id": "parent",
        "is_buggy": False,
        "is_buggy_plots": False,
        "debug_depth": 0,
        "metric_json": None,
        "stage_name": "seed",
        "child_count": 0,
        "is_seed_node": True,
    }
    non_seed_row: dict = {**seed_row, "node_id": "n0", "is_seed_node": False}

    assert _to_noderef(seed_row).is_seed_node is True
    assert _to_noderef(non_seed_row).is_seed_node is False


def test_to_noderef_defaults_is_seed_node_false_when_column_missing() -> None:
    """Pre-F.4 rows that predate the migration return without the
    ``is_seed_node`` column. The ref constructor must default to
    ``False`` so replays of historical runs keep working."""
    legacy_row = {
        "node_id": "n-legacy",
        "parent_node_id": None,
        "is_buggy": False,
        "is_buggy_plots": False,
        "debug_depth": 0,
        "metric_json": None,
        "stage_name": "draft",
        "child_count": 0,
    }

    assert _to_noderef(legacy_row).is_seed_node is False


def test_seed_determinism() -> None:
    """Same seed + same nodes => same selection."""
    cfg = SearchConfig(num_drafts=2, num_workers=3, max_debug_depth=3, debug_prob=0.5)
    nodes = [
        _N("d1", None, is_buggy=False, is_buggy_plots=False, debug_depth=0, metric_score=0.5).to_ref(),
        _N("d2", None, is_buggy=True, is_buggy_plots=None, debug_depth=0, metric_score=float("inf")).to_ref(),
    ]
    a = select_next(nodes=nodes, cfg=cfg, rng=random.Random(42))
    b = select_next(nodes=nodes, cfg=cfg, rng=random.Random(42))
    assert [n.node_id if n else None for n in a] == [n.node_id if n else None for n in b]
