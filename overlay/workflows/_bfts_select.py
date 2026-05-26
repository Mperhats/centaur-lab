"""Pure-Python BFTS selector (port of Sakana's _select_parallel_nodes).

Selection policy is best-first with debug retries (research 02 §Best-first
expansion algorithm, §Inner loop). Exploration knob is ``debug_prob``;
diversification knob is one-node-per-tree-per-step. Deterministic given
``rng`` — the workflow seeds rng from durable state for replay safety
(research 02 OQ #9).

Underscore-prefixed: workflow loader skips it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from random import Random
from typing import Optional


@dataclass(frozen=True)
class NodeRef:
    node_id: str
    parent_id: Optional[str]
    root_id: str                       # id of the root (draft) ancestor
    is_buggy: Optional[bool]            # None == not yet executed
    is_buggy_plots: Optional[bool]      # None == VLM not yet run
    debug_depth: int
    metric_score: float                 # _bfts_metric.score; lower is better
    stage_name: str
    is_leaf: bool


@dataclass(frozen=True)
class SearchConfig:
    num_drafts: int
    num_workers: int
    max_debug_depth: int
    debug_prob: float


def _draft_nodes(nodes: list[NodeRef]) -> list[NodeRef]:
    return [n for n in nodes if n.parent_id is None]


def _good_nodes(nodes: list[NodeRef]) -> list[NodeRef]:
    return [n for n in nodes if n.is_buggy is False and n.is_buggy_plots is not True]


def _buggy_leaf_nodes(nodes: list[NodeRef], max_depth: int) -> list[NodeRef]:
    return [
        n for n in nodes
        if n.is_buggy is True and n.is_leaf and n.debug_depth <= max_depth
    ]


def select_next(
    *,
    nodes: list[NodeRef],
    cfg: SearchConfig,
    rng: Random,
) -> list[Optional[NodeRef]]:
    """Return ``cfg.num_workers`` selections.

    Each entry is either:
      - ``None``  → instruct the caller to create a new draft node
      - ``NodeRef`` → expand THIS node next (debug or improve depending on
        the node's ``is_buggy``)
    """
    selected: list[Optional[NodeRef]] = []
    processed_roots: set[str] = set()

    drafts = _draft_nodes(nodes)
    viable_roots = {
        d.root_id for d in drafts
        if any(_node_is_viable_leaf(n, d.root_id) for n in nodes)
    }

    while len(selected) < cfg.num_workers:
        if len(drafts) < cfg.num_drafts:
            selected.append(None)
            drafts = drafts + [_phantom_draft(len(drafts))]
            continue

        buggy_leaves = _buggy_leaf_nodes(nodes, cfg.max_debug_depth)
        if buggy_leaves and rng.random() < cfg.debug_prob:
            candidate = rng.choice(buggy_leaves)
            if (
                candidate.root_id not in processed_roots
                or len(processed_roots) >= len(viable_roots)
            ):
                selected.append(candidate)
                processed_roots.add(candidate.root_id)
                continue

        good = _good_nodes(nodes)
        if not good:
            selected.append(None)
            continue

        good_sorted = sorted(good, key=lambda n: n.metric_score)
        # Try to pick best per untaken root.
        picked = None
        for cand in good_sorted:
            if cand.root_id not in processed_roots or len(processed_roots) >= len(viable_roots):
                picked = cand
                break
        if picked is None:
            # No more viable picks for this scheduling pass; emit a draft
            # to fill the slot (matches Sakana's selector fallback to None).
            selected.append(None)
            continue
        selected.append(picked)
        processed_roots.add(picked.root_id)

    return selected


def _phantom_draft(idx: int) -> NodeRef:
    """A placeholder used only by the selector's internal counting; never
    returned to the caller."""
    return NodeRef(
        node_id=f"__phantom_{idx}",
        parent_id=None,
        root_id=f"__phantom_{idx}",
        is_buggy=None,
        is_buggy_plots=None,
        debug_depth=0,
        metric_score=math.inf,
        stage_name="draft",
        is_leaf=True,
    )


def _node_is_viable_leaf(node: NodeRef, root_id: str) -> bool:
    """A root is viable if at least one leaf in its subtree is not buggy."""
    return node.root_id == root_id and (node.is_buggy is False or node.is_buggy is None)
