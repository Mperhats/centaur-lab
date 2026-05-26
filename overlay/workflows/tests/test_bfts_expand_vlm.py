"""Test: expand_node calls VLM after plotting on non-buggy nodes."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_expand import ExpandContext, expand_node


class _Ctx:
    def __init__(self, canned: dict[str, object]) -> None:
        self.canned = canned
        self.calls: list[str] = []

        class _Tools:
            class _Vlm:
                async def analyze_plots(_self, **kwargs):
                    return canned["vlm"]

            class _Exec:
                async def exec_python(_self, **kwargs):
                    return canned["exec"]

            bfts_vlm = _Vlm()
            bfts_executor = _Exec()

        self.tools = _Tools()

    async def step(self, name, fn):
        self.calls.append(name)
        if name in self.canned:
            return self.canned[name]
        return await fn() if callable(fn) else None

    def log(self, *a, **k): pass


@pytest.mark.asyncio
async def test_expand_node_runs_vlm_after_plot() -> None:
    canned = {
        "draft_propose": {"plan": "p", "code": "print(1)"},
        "draft_exec": {"term_out": ["ok\n"], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "bug_judge": {"is_bug": False, "summary": "ok"},
        "metric_parse_propose": "print('m')",
        "metric_parse_exec": {"term_out": ["m\n"], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "metric_extract": {"metric_names": []},
        "plot_propose": "import matplotlib",
        "plot_exec": {"term_out": [], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "collect_artifacts": ["loss.png"],
        "vlm_analyze": {"is_valid": True, "per_plot_analyses": [], "summary": "ok"},
    }
    ctx = _Ctx(canned)
    expand_ctx = ExpandContext(sandbox_id="s", parent_node=None, idea={}, openai_api_key="k", node_id="n1")
    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
    assert "vlm_analyze" in ctx.calls
    assert result["is_buggy_plots"] is False
    assert result["plot_analyses"] == []
    assert result["vlm_feedback_summary"] == "ok"
