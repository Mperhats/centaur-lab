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
        sandbox_id="sbx-1", parent_node=None, idea={}, llm_api_key="sk-test", node_id="n-1"
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
    # Good-path expansion must surface the metric-parse + plot sub-step
    # outputs so bfts_tree can persist them via update_node_metric.
    assert result["parse_metrics_code"] == "print('m')"
    assert result["parse_term_out"] == ["m\n"]
    assert result["plot_code"] == "import matplotlib"
    assert result["plot_term_out"] == []


@pytest.mark.asyncio
async def test_buggy_path_omits_parse_and_plot_keys() -> None:
    """The buggy short-circuit return dict must NOT contain parse_*/plot_*
    keys — bfts_tree relies on `.get()` defaults to pass None for unchanged
    columns."""
    canned = {
        "draft_propose": {"plan": "p", "code": "raise RuntimeError()"},
        "draft_exec": {"term_out": ["err\n"], "exec_time": 0.1, "exc_type": "SubprocessError", "exc_info": {"exit_code": 1}, "exc_stack": None},
        "bug_judge": {"is_bug": True, "summary": "raised"},
    }
    ctx = _RecordingCtx(canned)
    expand_ctx = ExpandContext(sandbox_id="sbx-1", parent_node=None, idea={}, llm_api_key="sk-test", node_id="n-bug")
    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
    for k in ("parse_metrics_code", "parse_term_out", "plot_code", "plot_term_out"):
        assert k not in result


@pytest.mark.asyncio
async def test_buggy_exec_skips_plotting() -> None:
    canned = {
        "draft_propose": {"plan": "p", "code": "raise RuntimeError()"},
        "draft_exec": {"term_out": ["err\n"], "exec_time": 0.1, "exc_type": "SubprocessError", "exc_info": {"exit_code": 1}, "exc_stack": None},
        "bug_judge": {"is_bug": True, "summary": "raised"},
    }
    ctx = _RecordingCtx(canned)
    expand_ctx = ExpandContext(sandbox_id="sbx-1", parent_node=None, idea={}, llm_api_key="sk-test", node_id="n-2")
    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
    # On buggy exec, plotting + metric_extract are skipped.
    assert ctx.calls == ["draft_propose", "draft_exec", "bug_judge"]
    assert result["is_buggy"] is True
    assert result["metric"] is None


@pytest.mark.asyncio
async def test_tool_failure_in_exec_python_is_coerced_to_buggy() -> None:
    """When ``ctx.tools.bfts_executor.exec_python`` fails, the centaur
    tool_manager returns ``{"error": "...", "tool": ..., "method": ...}``
    instead of an ``ExecutionResult`` dict. The expand pipeline used to
    crash with ``KeyError: 'term_out'`` on the next ``exec_res["term_out"]``
    read, masking the real underlying K8s/HTTP error (see live failure
    in ``wfr_6123347aa5e440ad`` 2026-05-26: HTTP 422 sandbox-name
    validation surfaced as ``KeyError('term_out')`` in
    ``bfts_expand_one``).

    Regression: the pipeline MUST detect the tool-failure shape, coerce
    it to a buggy ExecutionResult-shape dict (``exc_type='ToolCallError'``,
    ``term_out`` carrying the raw error string), and return ``is_buggy=True``
    so the parent tree records the failure as a real node + retains the
    error in ``exc_info_json`` for postmortem inspection.
    """
    canned = {
        "draft_propose": {"plan": "p", "code": "print(1)"},
        "draft_exec": {
            "error": "404, message='Invalid response status', url=...",
            "tool": "bfts_executor",
            "method": "exec_python",
        },
    }
    ctx = _RecordingCtx(canned)
    expand_ctx = ExpandContext(
        sandbox_id="sbx-1",
        parent_node=None,
        idea={},
        llm_api_key="sk-test",
        node_id="n-tool-failure",
    )
    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
    assert result["is_buggy"] is True, "tool failure must mark node buggy"
    assert result["exc_type"] == "ToolCallError"
    assert "404" in "\n".join(result["term_out"]), (
        "raw error string must be preserved in term_out for postmortem"
    )
    assert result["metric"] is None
    for k in ("parse_metrics_code", "parse_term_out", "plot_code", "plot_term_out"):
        assert k not in result, (
            "buggy short-circuit must not include parse_/plot_ keys"
        )
