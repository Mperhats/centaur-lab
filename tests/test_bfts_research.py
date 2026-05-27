"""Regression tests for ``bfts_research`` orchestration."""

from __future__ import annotations

from pathlib import Path


def test_research_brief_step_uses_tool_proxy_not_to_thread() -> None:
    """``wfr_0fe5e7e5f48d4d2b`` failed: coroutine checkpointed via to_thread."""
    source = Path("workflows/bfts_research.py").read_text(encoding="utf-8")
    assert "asyncio.to_thread(_call)" not in source
    assert "lambda: ctx.tools.semantic_scholar.research_brief" in source


def test_bfts_research_slack_ux_plain_brief_and_bfts_only_stream() -> None:
    """Brief + idea are plain posts; only BFTS uses agent-session streaming."""
    source = Path("workflows/bfts_research.py").read_text(encoding="utf-8")
    assert "open_slack_ideation_stream" not in source
    assert "post_slack_research_brief" in source
    assert "post_slack_research_idea" in source
    assert "open_slack_bfts_stream" in source
    assert "post_thread_message" in source
    assert "format_research_brief_thread_message" in source
    assert "notify_thread_failure" in source
    assert "workflow_run_failed" in source
    assert "notify_run_failure" in source
