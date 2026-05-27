"""Unit tests for in-flight BFTS Slack progress formatting."""

from __future__ import annotations

from pathlib import Path

from tools.bfts_runner.slack.progress import (
    TreeSearchSnapshot,
    format_tree_search_snapshot,
)


def test_fetch_workflow_status_uses_error_text_column() -> None:
    source = Path("tools/bfts_runner/slack/progress.py").read_text(encoding="utf-8")
    assert "error_text" in source
    assert "error_json" not in source


def test_format_tree_search_snapshot_running() -> None:
    text = format_tree_search_snapshot(
        tree_index=0,
        tree_run_id="wfr_root:tree:0",
        snapshot=TreeSearchSnapshot(
            workflow_status="running",
            node_count=4,
            max_step=3,
            buggy_count=3,
            good_count=0,
        ),
    )
    assert "Tree 0" in text
    assert "running" in text
    assert "step: 4" in text
    assert "buggy: 3" in text


def test_format_tree_search_snapshot_completed_with_good_nodes() -> None:
    text = format_tree_search_snapshot(
        tree_index=1,
        tree_run_id="wfr_root:tree:1",
        snapshot=TreeSearchSnapshot(
            workflow_status="completed",
            node_count=6,
            max_step=5,
            buggy_count=2,
            good_count=1,
        ),
    )
    assert "completed" in text
    assert "good node" in text
