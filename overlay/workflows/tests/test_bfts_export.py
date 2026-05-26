"""Test: _bfts_export.select_best picks deterministic argmin over good nodes."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_export import select_best


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
