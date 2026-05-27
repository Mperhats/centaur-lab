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


def test_wait_for_tree_with_slack_progress_emits_heartbeat() -> None:
    """``_wait_for_tree_with_slack_progress`` must call ``post_step`` every poll.

    Skipping ``post_step`` when the snapshot is unchanged lets Slack's
    agent-session message go idle long enough that Slack auto-closes the
    backing message; subsequent ``session_step`` calls then 502 with
    ``message_not_found`` (root-caused 2026-05-27 against session
    ``01KSNBG7WDX2GMDXPBA87GSAE8``). The fix is an unconditional
    ``post_step`` on every poll with a poll-counter in ``details`` so the
    payload is always distinct, defeating any slackbot/Slack-side
    no-op-on-identical-content optimization. This test pins the wiring
    so a future refactor can't reintroduce the dedupe.
    """
    source = Path("workflows/bfts_root.py").read_text(encoding="utf-8")

    func_start = source.index("async def _wait_for_tree_with_slack_progress")
    func_end = source.index("\nasync def ", func_start + 1)
    body = source[func_start:func_end]

    assert "last_snapshot_text" not in body
    assert "snapshot_text != last_snapshot_text" not in body
    # The unconditional post_step block must include the poll counter in
    # the ``details`` field so each call has distinct content.
    assert 'details=f"poll {poll}' in body
    # The step_name must remain ``stream_tree_progress_{tree_index}_{poll}``
    # so the durable workflow checkpoint cache continues to key each post
    # uniquely (different ``poll`` values produce different checkpoints).
    assert 'stream_tree_progress_{tree_index}_{poll}' in body
