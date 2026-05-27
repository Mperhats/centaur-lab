"""Regression tests for ``bfts_research`` orchestration."""

from __future__ import annotations

from pathlib import Path


def test_research_brief_step_uses_tool_proxy_not_to_thread() -> None:
    """``wfr_0fe5e7e5f48d4d2b`` failed: coroutine checkpointed via to_thread."""
    source = Path("workflows/bfts_research.py").read_text(encoding="utf-8")
    assert "asyncio.to_thread(_call)" not in source
    assert "lambda: ctx.tools.semantic_scholar.research_brief" in source
