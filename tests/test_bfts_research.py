"""Regression tests for ``bfts_research`` orchestration."""

from __future__ import annotations

from pathlib import Path


def test_research_brief_step_uses_tool_proxy_not_to_thread() -> None:
    """``wfr_0fe5e7e5f48d4d2b`` failed: coroutine checkpointed via to_thread."""
    source = Path("workflows/bfts_research.py").read_text(encoding="utf-8")
    assert "asyncio.to_thread(_call)" not in source
    assert "ctx.tools.semantic_scholar.research_brief" in source
    assert "lambda q=query" in source


def test_bfts_root_polls_tree_search_for_slack_stream() -> None:
    source = Path("workflows/bfts_root.py").read_text(encoding="utf-8")
    assert "tools.bfts_runner.slack.progress" in source
    assert "_wait_for_tree_with_slack_progress" in source
    assert "stream_tree_progress_" in source


def test_bfts_research_unified_slack_session_and_artifact_posts() -> None:
    """Unified agent-session for whole pipeline; brief + idea remain plain posts."""
    source = Path("workflows/bfts_research.py").read_text(encoding="utf-8")
    assert "open_slack_ideation_stream" not in source
    assert "tools.bfts_runner.slack.format" in source
    assert "tools.bfts_runner.slack.post" in source
    assert "tools.bfts_runner.slack.stream" in source
    assert "post_slack_research_brief" in source
    assert "post_slack_research_idea" in source
    assert "post_slack_bfts_started" not in source
    assert "format_bfts_stream_intro" not in source
    assert "format_research_phase_status" not in source
    assert "compact_markdown" in source
    assert "SuspendWorkflow" in source
    assert "_ResearchPipelineStop" in source
    assert "results_count" in source
    assert "format_empty_literature_thread_message" in source
    assert "post_slack_empty_literature" in source
    assert "packages.bfts_sdk.literature_query" in source
    assert "plan_literature_queries_" in source
    assert "_resolve_literature_brief" in source
    assert "literature_query" in source
    assert "open_slack_research_stream" in source
    assert "open_slack_bfts_stream" not in source
    assert "literature_search" in source
    assert "query_refinement" in source
    assert "stream_step_lit_search_in_progress" in source
    assert "stream_step_lit_search_complete" in source
    assert "stream_step_ideation_in_progress" in source
    assert "stream_step_ideation_complete" in source
    assert "stream_step_refine_query_in_progress" in source
    assert "close_research_stream_empty_literature" in source
    assert "start_ideation" in source
    open_idx = source.index("open_slack_research_stream")
    lit_idx = source.index("stream_step_lit_search_in_progress")
    ideation_idx = source.index("start_ideation")
    empty_idx = source.index("post_slack_empty_literature")
    assert open_idx < lit_idx < ideation_idx
    assert empty_idx < ideation_idx
