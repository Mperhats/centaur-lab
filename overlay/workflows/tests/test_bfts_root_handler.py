"""Test: bfts_root handler input parsing + deterministic sandbox_id format."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bfts_root import Input, WORKFLOW_NAME, _sandbox_id


def test_workflow_name() -> None:
    assert WORKFLOW_NAME == "bfts_root"


def test_input_required_idea() -> None:
    inp = Input(idea={"name": "test", "Title": "X"})
    assert inp.idea["name"] == "test"
    assert inp.num_drafts == 3
    assert inp.max_iters == 20


def test_sandbox_id_is_deterministic_and_run_scoped() -> None:
    assert _sandbox_id(run_id="run-abc", tree_idx=0) == "bfts-run-abc-tree-0"
    assert _sandbox_id(run_id="run-abc", tree_idx=2) == "bfts-run-abc-tree-2"
    # Different run -> different sandbox_id.
    assert _sandbox_id(run_id="run-def", tree_idx=0) == "bfts-run-def-tree-0"
