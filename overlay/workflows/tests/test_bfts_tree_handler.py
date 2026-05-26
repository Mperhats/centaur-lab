"""Test: bfts_tree handler input parsing + terminate condition."""
from __future__ import annotations

import json as _json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bfts_tree import Input, WORKFLOW_NAME, _should_terminate


def test_workflow_name() -> None:
    assert WORKFLOW_NAME == "bfts_tree"


def test_input_defaults() -> None:
    inp = Input(run_id="r1", parent_run_id=None, idea={"name": "x"})
    assert inp.num_drafts == 3
    assert inp.num_workers == 4
    assert inp.max_debug_depth == 3
    assert inp.debug_prob == 0.5
    assert inp.max_iters == 20
    assert inp.seed == 0


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
