"""Test: _bfts_expand.expand_node issues the right ctx.step sequence."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_expand import ExpandContext, expand_node


class _RecordingCtx:
    """Stub WorkflowContext that records each ctx.step name + returns
    canned values so we can assert the call order without I/O."""

    def __init__(self, canned: dict[str, object]) -> None:
        self._canned = canned
        self.calls: list[str] = []

    async def step(self, name, fn):
        self.calls.append(name)
        if name in self._canned:
            return self._canned[name]
        return await fn() if callable(fn) else None

    def log(self, *args, **kwargs):
        pass


@pytest.mark.asyncio
async def test_draft_expansion_calls_in_order() -> None:
    canned = {
        "draft_propose": {"plan": "p", "code": "print(1)"},
        "draft_exec": {"term_out": ["hi\n"], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "bug_judge": {"is_bug": False, "summary": "ok"},
        "metric_parse_propose": "print('m')",
        "metric_parse_exec": {"term_out": ["m\n"], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "metric_extract": {"metric_names": []},
        "plot_propose": "import matplotlib",
        "plot_exec": {"term_out": [], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "collect_artifacts": [],
    }
    ctx = _RecordingCtx(canned)
    expand_ctx = ExpandContext(
        sandbox_id="sbx-1", parent_node=None, idea={}, openai_api_key="sk-test", node_id="n-1"
    )
    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
    # Sakana's pipeline order, every entry one ctx.step:
    assert ctx.calls == [
        "draft_propose",
        "draft_exec",
        "bug_judge",
        "metric_parse_propose",
        "metric_parse_exec",
        "metric_extract",
        "plot_propose",
        "plot_exec",
        "collect_artifacts",
    ]
    assert result["code"] == "print(1)"
    assert result["is_buggy"] is False
    assert result["term_out"] == ["hi\n"]


@pytest.mark.asyncio
async def test_buggy_exec_skips_plotting() -> None:
    canned = {
        "draft_propose": {"plan": "p", "code": "raise RuntimeError()"},
        "draft_exec": {"term_out": ["err\n"], "exec_time": 0.1, "exc_type": "SubprocessError", "exc_info": {"exit_code": 1}, "exc_stack": None},
        "bug_judge": {"is_bug": True, "summary": "raised"},
    }
    ctx = _RecordingCtx(canned)
    expand_ctx = ExpandContext(sandbox_id="sbx-1", parent_node=None, idea={}, openai_api_key="sk-test", node_id="n-2")
    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
    # On buggy exec, plotting + metric_extract are skipped.
    assert ctx.calls == ["draft_propose", "draft_exec", "bug_judge"]
    assert result["is_buggy"] is True
    assert result["metric"] is None
