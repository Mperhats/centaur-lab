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
        self.vlm_analyze_paths: list[str] | None = None
        self.vlm_analyze_model: str | None = None
        self.picker_paths: list[str] | None = None
        self.picker_n: int | None = None
        self.picker_model: str | None = None
        ctx_self = self

        class _Tools:
            class _Vlm:
                async def analyze_plots(_self, **kwargs):
                    ctx_self.vlm_analyze_paths = list(kwargs.get("plot_paths") or [])
                    ctx_self.vlm_analyze_model = kwargs.get("model")
                    return canned["vlm"]

                async def select_best_n_plots(_self, **kwargs):
                    ctx_self.picker_paths = list(kwargs.get("plot_paths") or [])
                    ctx_self.picker_n = kwargs.get("n")
                    ctx_self.picker_model = kwargs.get("model")
                    return list(canned["picker"])  # type: ignore[arg-type]

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
    expand_ctx = ExpandContext(sandbox_id="s", parent_node=None, idea={}, llm_api_key="k", node_id="n1")
    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
    assert "vlm_analyze" in ctx.calls
    assert "select_best_plots" not in ctx.calls
    assert result["is_buggy_plots"] is False
    assert result["plot_analyses"] == []
    assert result["vlm_feedback_summary"] == "ok"


@pytest.mark.asyncio
async def test_expand_node_runs_picker_when_more_than_ten_plots() -> None:
    """>10 plots -> picker step runs first; analyze_plots sees the chosen subset.

    Phase 4g.3 fidelity fix — Sakana's ``_analyze_plots_with_vlm`` calls a
    feedback model to pick the 10 most informative plots before the VLM
    review when more were produced.
    """
    artifacts = [f"loss_epoch_{i:02d}.png" for i in range(12)]
    picked = [f"loss_epoch_{i:02d}.png" for i in range(10)]
    canned = {
        "draft_propose": {"plan": "p", "code": "print(1)"},
        "draft_exec": {"term_out": ["ok\n"], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "bug_judge": {"is_bug": False, "summary": "ok"},
        "metric_parse_propose": "print('m')",
        "metric_parse_exec": {"term_out": ["m\n"], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "metric_extract": {"metric_names": []},
        "plot_propose": "import matplotlib",
        "plot_exec": {"term_out": [], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "collect_artifacts": artifacts,
        # No canned ``vlm_analyze`` so the step's lambda actually runs and we
        # can capture the ``plot_paths`` it forwards into ``analyze_plots``.
        "vlm": {"is_valid": True, "per_plot_analyses": [], "summary": "ok"},
        "picker": picked,
    }
    ctx = _Ctx(canned)
    # Distinct sentinels so a regression that routes the picker to the VLM
    # model (Sakana spec violation) flips the model assertions below.
    expand_ctx = ExpandContext(
        sandbox_id="s",
        parent_node=None,
        idea={},
        llm_api_key="k",
        node_id="n1",
        feedback_model="claude-3-5-haiku-test",
        vlm_model="claude-vision-test",
    )
    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
    assert ctx.calls.index("select_best_plots") < ctx.calls.index("vlm_analyze")
    assert ctx.picker_n == 10
    assert ctx.picker_paths is not None and len(ctx.picker_paths) == 12
    assert ctx.vlm_analyze_paths is not None and len(ctx.vlm_analyze_paths) == 10
    assert [Path(p).name for p in ctx.vlm_analyze_paths] == picked
    assert result["is_buggy_plots"] is False
    # Sakana spec: the picker is a text-only ranking call on the feedback
    # model; the VLM model is reserved for the actual vision review
    # (`.scientist/ai_scientist/treesearch/parallel_agent.py:928-937`).
    assert ctx.picker_model == "claude-3-5-haiku-test"
    assert ctx.picker_model != "claude-vision-test"
    assert ctx.vlm_analyze_model == "claude-vision-test"
