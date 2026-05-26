"""Test: _bfts_export.select_best picks deterministic argmin over good nodes."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_export import select_best, write_best_node_id_artifact

from ._fakes import FakePool


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
