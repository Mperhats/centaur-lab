"""Property tests for _bfts_select.select_next (Sakana parity)."""
from __future__ import annotations

import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_select import NodeRef, SearchConfig, select_next


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
    # debug_depth at the cap (3) → ineligible for further debugging.
    n = _N("dx", "parent", is_buggy=True, is_buggy_plots=None, debug_depth=4, metric_score=float("inf")).to_ref()
    selected = select_next(nodes=[n], cfg=cfg, rng=rng)
    # No debuggable, no good_nodes → fall back to drafting (None).
    assert selected == [None]


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
