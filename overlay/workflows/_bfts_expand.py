"""Per-node expansion pipeline.

One call to expand_node() runs the 5–7 LLM-call + 3 exec-call pipeline
from research 02 §Agent turn shape:

  draft_propose / debug_propose / improve_propose  (LLM call #1)
  *_exec                                            (sandbox exec #1)
  bug_judge                                         (LLM call #2)
  metric_parse_propose                              (LLM call #3)
  metric_parse_exec                                 (sandbox exec #2)
  metric_extract                                    (LLM call #4)
  plot_propose                                      (LLM call #5, skipped if buggy)
  plot_exec                                         (sandbox exec #3, skipped if buggy)
  collect_artifacts                                 (sandbox call #4, skipped if buggy)
  vlm_analyze                                       (LLM call #6, skipped if no plot artifacts)

Each call is its own ctx.step so workflow restart resumes mid-pipeline.

VLM gate (LLM call #6) lives in this module — it runs after plot_exec
on the good path. Persistence of the gate (mark_buggy_plots) and
best-node artifact export happen in bfts_tree + _bfts_export.

Underscore-prefixed: workflow loader skips it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from _bfts_config import (
    DEFAULT_DRAFT_MODEL,
    DEFAULT_FEEDBACK_MODEL,
    DEFAULT_LLM_API_KEY_SECRET,
    DEFAULT_VLM_MODEL,
)
from _bfts_llm import LLMCall, call_for_text, call_with_function, extract_code
from _bfts_prompts import METRIC_PARSE_SPEC, REVIEW_FUNC_SPEC, render_prompts


_DRAFT_TEMP = 1.0
_FEEDBACK_TEMP = 0.5
# VLM batch cap matches ``bfts_vlm.client._MAX_PLOTS`` (Sakana's hardcoded 10).
# Above this we ask the feedback model to pick the most informative subset
# before running the VLM review.
_VLM_MAX_PLOTS = 10


@dataclass
class ExpandContext:
    sandbox_id: str
    parent_node: Optional[dict[str, Any]]   # row dict from bfts_nodes; None = new draft
    idea: dict[str, Any]
    llm_api_key: str
    node_id: str
    draft_model: str = DEFAULT_DRAFT_MODEL
    feedback_model: str = DEFAULT_FEEDBACK_MODEL
    vlm_model: str = DEFAULT_VLM_MODEL


def _branch(parent: Optional[dict[str, Any]]) -> str:
    if parent is None:
        return "draft"
    return "debug" if parent.get("is_buggy") else "improve"


def _propose_prompt(expand_ctx: ExpandContext) -> str:
    branch = _branch(expand_ctx.parent_node)
    if branch == "draft":
        return render_prompts(
            {"Idea": expand_ctx.idea},
            {"Task": "Write Python code that runs the experiment described above."},
        )
    if branch == "debug":
        parent = expand_ctx.parent_node or {}
        return render_prompts(
            {"Idea": expand_ctx.idea},
            {"Failed code": f"```python\n{parent.get('code','')}\n```"},
            {"stderr": (parent.get("term_out_json") or "")[-2000:] if isinstance(parent.get("term_out_json"), str) else ""},
            {"Task": "Fix the bug in the failed code above and re-run."},
        )
    parent = expand_ctx.parent_node or {}
    return render_prompts(
        {"Idea": expand_ctx.idea},
        {"Previous good code": f"```python\n{parent.get('code','')}\n```"},
        {"Task": "Improve on the previous code above."},
    )


def _metric_parse_prompt(code: str, term_out: list[str]) -> str:
    return render_prompts(
        {"Original experiment code": f"```python\n{code}\n```"},
        {"Experiment stdout": "\n".join(term_out)[-3000:]},
        {"Task": "Write a Python script that reads working/experiment_data.npy and PRINTS the metric values."},
    )


def _plot_prompt(code: str, metric: dict[str, Any]) -> str:
    return render_prompts(
        {"Experiment code": f"```python\n{code}\n```"},
        {"Metrics": metric},
        {"Task": "Write matplotlib code that loads working/experiment_data.npy and saves *.png plots to working/."},
    )


async def _propose_code(expand_ctx: ExpandContext) -> dict[str, Any]:
    text = await call_for_text(
        LLMCall(
            model=expand_ctx.draft_model,
            temperature=_DRAFT_TEMP,
            api_key=expand_ctx.llm_api_key,
            prompt=_propose_prompt(expand_ctx),
        )
    )
    plan, code = extract_code(text)
    return {"plan": plan, "code": code}


async def _bug_judge(
    text_blobs: list[str], *, llm_api_key: str, feedback_model: str
) -> dict[str, Any]:
    return await call_with_function(
        LLMCall(
            model=feedback_model,
            temperature=_FEEDBACK_TEMP,
            api_key=llm_api_key,
            prompt="Judge whether this experiment succeeded:\n\n" + "\n\n".join(text_blobs),
        ),
        function_spec=REVIEW_FUNC_SPEC,
    )


async def _metric_extract(
    parse_term_out: list[str], *, llm_api_key: str, feedback_model: str
) -> dict[str, Any]:
    return await call_with_function(
        LLMCall(
            model=feedback_model,
            temperature=_FEEDBACK_TEMP,
            api_key=llm_api_key,
            prompt="Extract metrics from this stdout:\n\n" + "\n".join(parse_term_out)[-3000:],
        ),
        function_spec=METRIC_PARSE_SPEC,
    )


async def expand_node(*, ctx: Any, expand_ctx: ExpandContext) -> dict[str, Any]:
    """Run one full expansion. Returns a dict suitable for update_node_metric."""

    branch = _branch(expand_ctx.parent_node)

    proposed = await ctx.step(
        f"{branch}_propose", lambda: _propose_code(expand_ctx)
    )

    exec_res = await ctx.step(
        f"{branch}_exec",
        lambda: ctx.tools.bfts_executor.exec_python(
            sandbox_id=expand_ctx.sandbox_id,
            code=proposed["code"],
            timeout_s=3600,
        ),
    )

    judge = await ctx.step(
        "bug_judge",
        lambda: _bug_judge(
            [proposed["code"], "\n".join(exec_res["term_out"])],
            llm_api_key=expand_ctx.llm_api_key,
            feedback_model=expand_ctx.feedback_model,
        ),
    )
    is_buggy = bool(judge["is_bug"]) or exec_res["exc_type"] is not None

    if is_buggy:
        return {
            "plan": proposed["plan"],
            "code": proposed["code"],
            "term_out": exec_res["term_out"],
            "exec_time_seconds": exec_res["exec_time"],
            "exc_type": exec_res["exc_type"],
            "exc_info": exec_res["exc_info"],
            "exc_stack": exec_res["exc_stack"],
            "metric": None,
            "is_buggy": True,
            "analysis": judge["summary"],
            "stage_name": branch,
        }

    parse_code = await ctx.step(
        "metric_parse_propose",
        lambda: _metric_parse_inline(expand_ctx, proposed, exec_res),
    )

    parse_exec = await ctx.step(
        "metric_parse_exec",
        lambda: ctx.tools.bfts_executor.exec_python(
            sandbox_id=expand_ctx.sandbox_id, code=parse_code, timeout_s=300,
        ),
    )

    metric = await ctx.step(
        "metric_extract",
        lambda: _metric_extract(
            parse_exec["term_out"],
            llm_api_key=expand_ctx.llm_api_key,
            feedback_model=expand_ctx.feedback_model,
        ),
    )

    plot_code = await ctx.step(
        "plot_propose",
        lambda: _plot_propose_inline(expand_ctx, proposed, metric),
    )

    plot_exec = await ctx.step(
        "plot_exec",
        lambda: ctx.tools.bfts_executor.exec_python(
            sandbox_id=expand_ctx.sandbox_id, code=plot_code, timeout_s=300,
        ),
    )

    artifacts = await ctx.step(
        "collect_artifacts",
        lambda: ctx.tools.bfts_executor.collect_artifacts(
            sandbox_id=expand_ctx.sandbox_id,
            dest_dir=Path(f"/tmp/bfts/{expand_ctx.node_id}"),
            node_id=expand_ctx.node_id,
        ),
    )
    plot_paths = [
        str(Path(f"/tmp/bfts/{expand_ctx.node_id}/experiment_{expand_ctx.node_id}") / name)
        for name in artifacts if name.endswith(".png")
    ]

    if plot_paths:
        vlm_model = expand_ctx.vlm_model
        task_desc = str(expand_ctx.idea.get("Title", ""))
        # Sakana picks the 10 most informative plots via a feedback-model
        # call before the VLM batch when >10 plots were produced
        # (`.scientist/ai_scientist/treesearch/parallel_agent.py:910-980`).
        # Phase 4g.3 ports that as its own ctx.step so a mid-pipeline
        # restart resumes after the (cached) picker call.
        if len(plot_paths) > _VLM_MAX_PLOTS:
            picked = await ctx.step(
                "select_best_plots",
                lambda paths=plot_paths, desc=task_desc, m=vlm_model: (
                    ctx.tools.bfts_vlm.select_best_n_plots(
                        plot_paths=paths,
                        n=_VLM_MAX_PLOTS,
                        task_desc=desc,
                        model=m,
                    )
                ),
            )
        else:
            picked = plot_paths
        vlm = await ctx.step(
            "vlm_analyze",
            lambda paths=picked, desc=task_desc, m=vlm_model: (
                ctx.tools.bfts_vlm.analyze_plots(
                    plot_paths=paths,
                    task_desc=desc,
                    model=m,
                )
            ),
        )
    else:
        vlm = {"is_valid": False, "per_plot_analyses": [], "summary": "no plots produced"}

    return {
        "plan": proposed["plan"],
        "code": proposed["code"],
        "term_out": exec_res["term_out"],
        "exec_time_seconds": exec_res["exec_time"],
        "exc_type": exec_res["exc_type"],
        "exc_info": exec_res["exc_info"],
        "exc_stack": exec_res["exc_stack"],
        "metric": metric,
        "is_buggy": False,
        "analysis": judge["summary"],
        "stage_name": branch,
        "parse_metrics_code": parse_code,
        "parse_term_out": parse_exec["term_out"],
        "plot_code": plot_code,
        "plot_term_out": plot_exec["term_out"],
        "is_buggy_plots": not vlm["is_valid"],
        "plot_analyses": vlm["per_plot_analyses"],
        "vlm_feedback_summary": vlm["summary"],
    }


async def _metric_parse_inline(
    expand_ctx: ExpandContext, proposed: dict[str, Any], exec_res: dict[str, Any]
) -> str:
    text = await call_for_text(
        LLMCall(
            model=expand_ctx.draft_model,
            temperature=_DRAFT_TEMP,
            api_key=expand_ctx.llm_api_key,
            prompt=_metric_parse_prompt(proposed["code"], exec_res["term_out"]),
        )
    )
    _plan, code = extract_code(text)
    return code


async def _plot_propose_inline(
    expand_ctx: ExpandContext, proposed: dict[str, Any], metric: dict[str, Any]
) -> str:
    text = await call_for_text(
        LLMCall(
            model=expand_ctx.draft_model,
            temperature=_DRAFT_TEMP,
            api_key=expand_ctx.llm_api_key,
            prompt=_plot_prompt(proposed["code"], metric),
        )
    )
    _plan, code = extract_code(text)
    return code
