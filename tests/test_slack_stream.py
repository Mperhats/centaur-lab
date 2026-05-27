"""Tests for Slack stream helpers."""

from __future__ import annotations

from packages.bfts_sdk.slack_stream import (
    format_bfts_stream_intro,
    format_failure_thread_message,
    format_idea_markdown,
    format_research_brief_thread_message,
    workflow_run_error_text,
    workflow_run_failed,
)


def test_format_research_brief_thread_message() -> None:
    msg = format_research_brief_thread_message(
        topic="active inference",
        markdown="# Brief\n\nBody.",
    )
    assert "Research brief" in msg
    assert "active inference" in msg
    assert "# Brief" in msg


def test_format_bfts_stream_intro() -> None:
    intro = format_bfts_stream_intro("My Idea")
    assert "BFTS tree search" in intro
    assert "My Idea" in intro
    assert "Step 2" not in intro


def test_format_idea_markdown() -> None:
    md = format_idea_markdown(
        {
            "Title": "VFE-NCA",
            "Short Hypothesis": "Free-energy updates beat MSE.",
            "Experiments": ["Train 32x32", "Ablate damage"],
        }
    )
    assert "Research idea" in md
    assert "VFE-NCA" in md
    assert "Free-energy" in md
    assert "• Train 32x32" in md


def test_workflow_run_failed_and_error_text() -> None:
    run = {
        "status": "failed",
        "error_text": "LLM call failed: 502 bad gateway",
        "run_id": "wfr_abc",
    }
    assert workflow_run_failed(run)
    assert "502" in workflow_run_error_text(run)
    assert not workflow_run_failed({"status": "completed"})


def test_format_failure_thread_message_includes_child() -> None:
    msg = format_failure_thread_message(
        delivery={"platform": "slack", "channel": "C1", "recipient_user_id": "U1"},
        headline="Ideation failed",
        orchestrator_run_id="wfr_parent",
        error_text="timeout",
        child_run_id="wfr_child",
        child_workflow="ideation",
    )
    assert "<@U1>" in msg
    assert "Ideation failed" in msg
    assert "wfr_parent" in msg
    assert "wfr_child" in msg
    assert "ideation" in msg
    assert "timeout" in msg
