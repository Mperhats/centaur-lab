"""Test: _bfts_export.select_best picks deterministic argmin over good nodes."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_export import (
    render_tree_dot,
    select_best,
    write_best_node_id_artifact,
    write_references_artifact,
    write_tree_dot_artifact,
)

from workflows.tests._mocks import MockPool as FakePool


def _node(node_id: str, is_buggy: bool, is_buggy_plots: bool | None, final_value: float) -> dict:
    return {
        "node_id": node_id,
        "is_buggy": is_buggy,
        "is_buggy_plots": is_buggy_plots,
        "metric_json": {
            "metric_names": [{
                "metric_name": "loss",
                "lower_is_better": True,
                "description": "",
                "data": [{"dataset_name": "d", "final_value": final_value, "best_value": final_value}],
            }]
        },
        "code": f"# code for {node_id}",
    }


def test_select_best_argmin_lower_is_better() -> None:
    nodes = [
        _node("a", is_buggy=False, is_buggy_plots=False, final_value=0.5),
        _node("b", is_buggy=False, is_buggy_plots=False, final_value=0.3),
        _node("c", is_buggy=False, is_buggy_plots=False, final_value=0.4),
    ]
    assert select_best(nodes)["node_id"] == "b"


def test_select_best_excludes_buggy_plots() -> None:
    nodes = [
        _node("a", is_buggy=False, is_buggy_plots=True, final_value=0.1),  # buggy plots: excluded
        _node("b", is_buggy=False, is_buggy_plots=False, final_value=0.3),
    ]
    assert select_best(nodes)["node_id"] == "b"


def test_select_best_returns_none_when_no_good() -> None:
    nodes = [_node("a", is_buggy=True, is_buggy_plots=None, final_value=0.1)]
    assert select_best(nodes) is None


@pytest.mark.asyncio
async def test_write_best_node_id_artifact_inserts_row() -> None:
    """Helper writes a `best_node_id.txt` artifact whose bytes are the node_id."""
    pool = FakePool()

    artifact_id = await write_best_node_id_artifact(pool, node_id="node-abc")

    assert isinstance(artifact_id, str) and artifact_id
    assert len(pool.execute_calls) == 1
    query, args = pool.execute_calls[0]

    # Positional args mirror write_best_artifact: (artifact_id, node_id, bytes).
    assert args[0] == artifact_id
    assert args[1] == "node-abc"
    assert args[2] == b"node-abc"

    # Targets bfts_artifacts with the expected relative_path.
    assert "bfts_artifacts" in query
    assert "best_node_id.txt" in query


@pytest.mark.asyncio
async def test_write_best_node_id_artifact_is_idempotent() -> None:
    """Insert uses ON CONFLICT so repeated calls overwrite, matching write_best_artifact."""
    pool = FakePool()

    await write_best_node_id_artifact(pool, node_id="node-xyz")

    query, _ = pool.execute_calls[0]
    upper = query.upper()
    assert "ON CONFLICT" in upper
    assert "DO UPDATE" in upper


@pytest.mark.asyncio
async def test_write_best_node_id_artifact_returns_unique_ids() -> None:
    """Each call mints a fresh artifact_id so repeated writes don't clash on PK."""
    pool = FakePool()

    first = await write_best_node_id_artifact(pool, node_id="same-node")
    second = await write_best_node_id_artifact(pool, node_id="same-node")

    assert first != second


# --- Phase 4d.3: write_references_artifact ------------------------------
#
# Mirror surface of ``write_best_artifact``: idempotent UPSERT keyed on
# ``(node_id, relative_path)`` so a re-run of ``gather_citations`` for the
# same best node overwrites the previous BibTeX rather than orphaning the
# old row + colliding on the unique constraint. The artifact is the input
# to a future writeup workflow; the contract those tests pin is what the
# writeup workflow will read.


@pytest.mark.asyncio
async def test_write_references_artifact_inserts_row() -> None:
    """The helper writes a ``references.bib`` artifact whose bytes are
    the supplied BibTeX string (UTF-8 encoded). Mirrors
    ``write_best_artifact``'s positional contract: ``(artifact_id,
    node_id, bytes)`` so tests can assert the column ordering directly."""
    pool = FakePool()

    bibtex = "@article{Author2024, title={X}, author={A}, year={2024}}"
    artifact_id = await write_references_artifact(
        pool, node_id="node-abc", bibtex=bibtex
    )

    assert isinstance(artifact_id, str) and artifact_id
    assert len(pool.execute_calls) == 1
    query, args = pool.execute_calls[0]

    assert args[0] == artifact_id
    assert args[1] == "node-abc"
    assert args[2] == bibtex.encode("utf-8")

    assert "bfts_artifacts" in query


@pytest.mark.asyncio
async def test_write_references_artifact_uses_relative_path_references_bib() -> None:
    """The relative path must be exactly ``references.bib`` so the
    downstream writeup workflow can find it next to ``best_solution.py``."""
    pool = FakePool()

    await write_references_artifact(pool, node_id="node-abc", bibtex="@article{X}")

    query, _ = pool.execute_calls[0]
    assert "references.bib" in query


@pytest.mark.asyncio
async def test_write_references_artifact_overwrites_existing() -> None:
    """Re-running ``gather_citations`` for the same best node must
    overwrite the previous BibTeX (operator may have re-prompted the
    LLM, S2 may have new entries). The UPSERT is keyed on
    ``(node_id, relative_path)`` so we don't need to delete first."""
    pool = FakePool()

    await write_references_artifact(pool, node_id="node-xyz", bibtex="@article{V1}")

    query, _ = pool.execute_calls[0]
    upper = query.upper()
    assert "ON CONFLICT" in upper
    assert "DO UPDATE" in upper


# ---------------------------------------------------------------------------
# F.3: render_tree_dot + write_tree_dot_artifact.
# ---------------------------------------------------------------------------


def test_render_tree_dot_colors_node_states() -> None:
    """Three-node tree: root good → child buggy_plots → grandchild best.
    Assert dot structure, all node ids present, edges correct, colors
    match the legend, ``best`` overrides ``good``."""
    nodes = [
        {"node_id": "root", "parent_node_id": None, "stage_name": "draft",
         "is_buggy": False, "is_buggy_plots": False,
         "metric_json": {"final_value": 0.5}},
        {"node_id": "mid", "parent_node_id": "root", "stage_name": "improve",
         "is_buggy": False, "is_buggy_plots": True,
         "metric_json": {"final_value": 0.4}},
        {"node_id": "best", "parent_node_id": "mid", "stage_name": "improve",
         "is_buggy": False, "is_buggy_plots": False,
         "metric_json": {"final_value": 0.3}},
    ]
    dot = render_tree_dot(nodes, run_id="r1", best_node_id="best")
    assert dot.startswith("digraph BFTS_r1 {")
    assert dot.rstrip().endswith("}")
    for nid in ("root", "mid", "best"):
        assert f'"{nid}"' in dot
    assert '"root" -> "mid"' in dot
    assert '"mid" -> "best"' in dot
    assert 'fillcolor="gold"' in dot      # best
    assert 'fillcolor="yellow"' in dot    # buggy_plots
    assert 'fillcolor="green"' in dot     # good non-best


def test_render_tree_dot_handles_pending_and_buggy_nodes() -> None:
    """Pending (``is_buggy is None``) and buggy nodes get the right colors;
    legend subgraph carries every state name."""
    nodes = [
        {"node_id": "p", "parent_node_id": None, "stage_name": "draft",
         "is_buggy": None, "is_buggy_plots": None, "metric_json": None},
        {"node_id": "b", "parent_node_id": "p", "stage_name": "debug",
         "is_buggy": True, "is_buggy_plots": None, "metric_json": None},
    ]
    dot = render_tree_dot(nodes, run_id="r2", best_node_id=None)
    assert 'fillcolor="lightgray"' in dot  # pending
    assert 'fillcolor="red"' in dot        # buggy
    for state in ("best", "good", "buggy", "buggy_plots", "pending"):
        assert f'"legend_{state}"' in dot


def test_render_tree_dot_sanitizes_colon_run_id() -> None:
    """The digraph identifier sanitizer must not break on
    ``wfr_<hex>:tree:0`` (colons are illegal unquoted in dot)."""
    nodes = [{"node_id": "n1", "parent_node_id": None, "stage_name": "draft",
              "is_buggy": False, "is_buggy_plots": False,
              "metric_json": {"final_value": 0.1}}]
    dot = render_tree_dot(nodes, run_id="wfr_abc:tree:0", best_node_id=None)
    header = dot.splitlines()[0]
    assert ":" not in header
    assert header.startswith("digraph BFTS_")


def test_render_tree_dot_handles_nested_metric_schema() -> None:
    """Nested ``metric_names[*].data[*].final_value`` shape is read for
    the score-label suffix so multi-metric runs still show a score."""
    nodes = [{
        "node_id": "n1", "parent_node_id": None, "stage_name": "draft",
        "is_buggy": False, "is_buggy_plots": False,
        "metric_json": {
            "metric_names": [
                {"data": [{"final_value": 0.4242}]}
            ]
        },
    }]
    dot = render_tree_dot(nodes, run_id="r3", best_node_id=None)
    assert "0.4242" in dot


def test_render_tree_dot_metric_json_string_payload_is_tolerated() -> None:
    """``metric_json`` arriving as a raw JSON string (asyncpg jsonb return)
    is parsed before label generation; an unparseable string just yields
    a node without a score suffix instead of crashing."""
    import json as _json
    nodes = [
        {"node_id": "n1", "parent_node_id": None, "stage_name": "draft",
         "is_buggy": False, "is_buggy_plots": False,
         "metric_json": _json.dumps({"final_value": 0.123})},
        {"node_id": "n2", "parent_node_id": None, "stage_name": "draft",
         "is_buggy": False, "is_buggy_plots": False,
         "metric_json": "not valid json"},
    ]
    dot = render_tree_dot(nodes, run_id="r4", best_node_id=None)
    assert "0.123" in dot
    assert '"n2"' in dot


@pytest.mark.asyncio
async def test_write_tree_dot_artifact_upserts_with_run_id_keyed_artifact_id() -> None:
    """The artifact_id is ``<run_id>:tree.dot`` so replay overwrites
    the previous render rather than colliding on the unique
    ``(node_id, relative_path)`` constraint."""
    pool = FakePool()

    artifact_id = await write_tree_dot_artifact(
        pool, run_id="r1", dot_text="digraph X {}", anchor_node_id="n1"
    )

    assert artifact_id == "r1:tree.dot"
    query, args = pool.execute_calls[0]
    assert "tree.dot" in query
    assert "tree_viz" in query
    assert "ON CONFLICT" in query.upper()
    assert args[0] == "r1:tree.dot"
    assert args[1] == "n1"
    assert args[2] == b"digraph X {}"
