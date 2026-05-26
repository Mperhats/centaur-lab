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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from _bfts_config import (
    DEFAULT_DRAFT_MODEL,
    DEFAULT_FEEDBACK_MODEL,
    DEFAULT_LLM_API_KEY_SECRET,
    DEFAULT_VLM_MODEL,
)
from _bfts_llm import LLMCall, call_for_text, call_with_function, extract_code
from _bfts_prompts import (
    METRIC_PARSE_SPEC,
    PROMPT_IMPL_GUIDELINE,
    PROMPT_RESP_FMT,
    REVIEW_FUNC_SPEC,
    prior_attempts_section,
    render_prompts,
)


_DRAFT_TEMP = 1.0
_FEEDBACK_TEMP = 0.5
# VLM batch cap matches ``bfts_vlm.client._MAX_PLOTS`` (Sakana's hardcoded 10).
# Above this we ask the feedback model to pick the most informative subset
# before running the VLM review.
_VLM_MAX_PLOTS = 10


def _coerce_exec_result(result: Any) -> dict[str, Any]:
    """Normalize the return of ``ctx.tools.bfts_executor.exec_python``.

    The executor tool returns an :class:`ExecutionResult` dataclass on
    success, which the centaur tool_manager serializes to a dict shape
    ``{"term_out": [...], "exec_time": ..., "exc_type": ..., ...}``.

    On *tool* failure (K8s 422 sandbox-name validation, websocket
    handshake 404, tool not registered, ...) the tool_manager catches
    the exception and returns ``{"error": "...", "tool": ...,
    "method": ...}`` instead — a shape with NO ``term_out`` key. Without
    coercion the downstream ``exec_res["term_out"]`` read raises
    ``KeyError: 'term_out'`` and the entire expand workflow fails,
    masking the real underlying failure (live regression 2026-05-26:
    sandbox name with underscore was rejected by K8s, surfaced as a
    confusing ``KeyError`` in ``bfts_expand_one``).

    Coerce the failure into an ExecutionResult-shape dict that flows
    through the rest of the expand pipeline as a buggy node, so the
    real error reaches ``bfts_nodes.exc_info_json`` for postmortem.
    """
    if isinstance(result, dict) and "term_out" in result:
        return result
    err_text = (
        result.get("error", "tool returned unexpected shape")
        if isinstance(result, dict)
        else str(result)
    )
    return {
        "term_out": [f"[bfts_executor tool failure] {err_text}"],
        "exec_time": 0.0,
        "exc_type": "ToolCallError",
        "exc_info": {
            "raw": result if isinstance(result, dict) else {"value": str(result)}
        },
        "exc_stack": None,
    }


@dataclass
class ExpandContext:
    sandbox_id: str
    parent_node: Optional[dict[str, Any]]   # row dict from bfts_nodes; None = new draft
    idea: dict[str, Any]
    llm_api_key: str
    node_id: str
    # Per-expansion subdirectory under the sandbox's /workspace PVC.
    # Phase 4h: ``bfts_expand_one`` (and any controller that fans out
    # parallel expansions inside the same sandbox) MUST pass a distinct
    # ``working_dir`` per child so concurrent ``exec_python`` calls don't
    # race on ``runfile.py`` / ``experiment_data.npy`` / ``*.png`` in the
    # shared workspace. The default ``"working"`` keeps Phase 0-3
    # sequential callers and existing tests on the original layout.
    # The executor validates the value against ``^[A-Za-z0-9_-]+$``.
    working_dir: str = "working"
    draft_model: str = DEFAULT_DRAFT_MODEL
    feedback_model: str = DEFAULT_FEEDBACK_MODEL
    vlm_model: str = DEFAULT_VLM_MODEL
    # F.2: most-recent-first list of node summaries injected into the
    # draft / improve propose prompt as a markdown ``## Prior attempts``
    # section. Loaded by bfts_expand_one via
    # ``_bfts_state.list_recent_node_summaries`` before constructing
    # this dataclass. Empty list disables the injection; debug branch
    # ignores this field (parent failure context is already in the
    # prompt).
    prior_attempts: list[dict[str, Any]] = field(default_factory=list)
    # F.4: when set, ``expand_node`` skips the draft/debug/improve LLM
    # propose call entirely and re-runs the parent's ``code`` with a
    # deterministic random/numpy/torch seed preamble. ``parent_node``
    # MUST be non-None and carry ``code``; the seeding mode is only
    # ever invoked from ``bfts_tree.handler`` after ``set_best`` so
    # the parent is the chosen best node. Everything else in the
    # pipeline (exec, bug_judge, metric parse, plot, VLM) runs
    # unchanged so seed nodes get the same persisted shape as a
    # normal expansion (metric_json / is_buggy / etc.).
    seed_override: int | None = None


def _branch(parent: Optional[dict[str, Any]]) -> str:
    if parent is None:
        return "draft"
    return "debug" if parent.get("is_buggy") else "improve"


def _seed_preamble(seed: int) -> str:
    """Python preamble injected at the top of ``parent.code`` for F.4
    seed re-evaluation. ``torch`` is optional (the bfts-executor base
    image only installs it on x86_64); ``try/except`` keeps the seed
    node working on the ARM64 image used by local-dev macs.
    """
    return (
        f"import random; random.seed({seed})\n"
        f"import numpy as np; np.random.seed({seed})\n"
        f"try:\n"
        f"    import torch; torch.manual_seed({seed})\n"
        f"except Exception:\n"
        f"    pass\n"
    )


async def _seed_propose(expand_ctx: ExpandContext) -> dict[str, str]:
    """No-LLM "propose" stand-in for seed nodes.

    ``expand_node`` calls this through ``ctx.step("seed_propose", ...)``
    so the step shape (and durable replay contract) matches the
    LLM-driven propose calls; we just synthesize the result instead
    of round-tripping to the LLM.
    """
    parent = expand_ctx.parent_node
    if parent is None or not parent.get("code"):
        raise ValueError(
            "seed_override requires a parent node with executable code"
        )
    seed = int(expand_ctx.seed_override or 0)
    return {
        "plan": f"seed re-evaluation with seed={seed}",
        "code": _seed_preamble(seed) + parent["code"],
    }


def _propose_prompt(expand_ctx: ExpandContext) -> str:
    branch = _branch(expand_ctx.parent_node)
    # F.2: prior-attempts memory is injected for draft + improve only.
    # Debug already has the parent's failed code + stderr; piling on the
    # last K nodes would inflate the prompt without adding signal.
    memory_block = prior_attempts_section(expand_ctx.prior_attempts)
    if branch == "draft":
        return render_prompts(
            {"Idea": expand_ctx.idea},
            memory_block,
            PROMPT_IMPL_GUIDELINE,
            PROMPT_RESP_FMT,
            {"Task": (
                "Write Python code that runs the experiment described "
                "above and saves results to ``experiment_data.npy`` in "
                "the current working directory."
            )},
        )
    if branch == "debug":
        parent = expand_ctx.parent_node or {}
        return render_prompts(
            {"Idea": expand_ctx.idea},
            PROMPT_IMPL_GUIDELINE,
            PROMPT_RESP_FMT,
            {"Failed code": f"```python\n{parent.get('code','')}\n```"},
            {"stderr": (parent.get("term_out_json") or "")[-2000:] if isinstance(parent.get("term_out_json"), str) else ""},
            {"Task": "Fix the bug in the failed code above and re-run."},
        )
    parent = expand_ctx.parent_node or {}
    return render_prompts(
        {"Idea": expand_ctx.idea},
        memory_block,
        PROMPT_IMPL_GUIDELINE,
        PROMPT_RESP_FMT,
        {"Previous good code": f"```python\n{parent.get('code','')}\n```"},
        {"Task": "Improve on the previous code above."},
    )


def _metric_parse_prompt(code: str, term_out: list[str]) -> str:
    return render_prompts(
        {"Original experiment code": f"```python\n{code}\n```"},
        {"Experiment stdout": "\n".join(term_out)[-3000:]},
        {"Task": (
            "Write a Python script that reads ``experiment_data.npy`` "
            "from the current working directory (cwd is already the "
            "per-node working_dir) and PRINTS the metric values."
        )},
    )


def _plot_prompt(code: str, metric: dict[str, Any]) -> str:
    return render_prompts(
        {"Experiment code": f"```python\n{code}\n```"},
        {"Metrics": metric},
        {"Task": (
            "Write matplotlib code that loads ``experiment_data.npy`` "
            "from the current working directory and saves ``*.png`` "
            "plots to that same cwd (no nested subdir). Use "
            "``plt.savefig(...)`` instead of ``plt.show()`` — the "
            "runner is headless."
        )},
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

    # F.4: ``seed_override`` short-circuits the LLM propose step but
    # leaves the rest of the pipeline (exec / bug_judge / metric_parse
    # / plot / VLM) intact so seed nodes get the same persisted shape
    # as a normal expansion.
    if expand_ctx.seed_override is not None:
        branch = "seed"
        proposed = await ctx.step(
            "seed_propose", lambda: _seed_propose(expand_ctx)
        )
    else:
        branch = _branch(expand_ctx.parent_node)
        proposed = await ctx.step(
            f"{branch}_propose", lambda: _propose_code(expand_ctx)
        )

    exec_res = _coerce_exec_result(await ctx.step(
        f"{branch}_exec",
        lambda: ctx.tools.bfts_executor.exec_python(
            sandbox_id=expand_ctx.sandbox_id,
            code=proposed["code"],
            timeout_s=3600,
            working_dir=expand_ctx.working_dir,
        ),
    ))

    # Tool itself crashed (RFC 1123 / WS handshake / kubernetes_asyncio): no
    # execution outcome to judge. Short-circuit before the LLM ``bug_judge``
    # call to save tokens — we already know the node is buggy because the
    # executor never produced an ExecutionResult.
    if exec_res["exc_type"] == "ToolCallError":
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
            "analysis": "tool call failed before code execution",
            "stage_name": branch,
        }

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

    parse_exec = _coerce_exec_result(await ctx.step(
        "metric_parse_exec",
        lambda: ctx.tools.bfts_executor.exec_python(
            sandbox_id=expand_ctx.sandbox_id,
            code=parse_code,
            timeout_s=300,
            working_dir=expand_ctx.working_dir,
        ),
    ))

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

    plot_exec = _coerce_exec_result(await ctx.step(
        "plot_exec",
        lambda: ctx.tools.bfts_executor.exec_python(
            sandbox_id=expand_ctx.sandbox_id,
            code=plot_code,
            timeout_s=300,
            working_dir=expand_ctx.working_dir,
        ),
    ))

    artifacts = await ctx.step(
        "collect_artifacts",
        lambda: ctx.tools.bfts_executor.collect_artifacts(
            sandbox_id=expand_ctx.sandbox_id,
            dest_dir=Path(f"/tmp/bfts/{expand_ctx.node_id}"),
            node_id=expand_ctx.node_id,
            working_dir=expand_ctx.working_dir,
        ),
    )
    plot_paths = [
        str(Path(f"/tmp/bfts/{expand_ctx.node_id}/experiment_{expand_ctx.node_id}") / name)
        for name in artifacts if name.endswith(".png")
    ]

    if plot_paths:
        vlm_model = expand_ctx.vlm_model
        # The picker is a text-only ranking call — Sakana hits it with
        # `cfg.agent.feedback.model`
        # (`.scientist/ai_scientist/treesearch/parallel_agent.py:928-937`),
        # not the VLM. The VLM model stays on the actual vision call.
        picker_model = expand_ctx.feedback_model
        task_desc = str(expand_ctx.idea.get("Title", ""))
        # Sakana picks the 10 most informative plots via a feedback-model
        # call before the VLM batch when >10 plots were produced
        # (`.scientist/ai_scientist/treesearch/parallel_agent.py:910-980`).
        # Phase 4g.3 ports that as its own ctx.step so a mid-pipeline
        # restart resumes after the (cached) picker call.
        if len(plot_paths) > _VLM_MAX_PLOTS:
            picked = await ctx.step(
                "select_best_plots",
                lambda paths=plot_paths, desc=task_desc, m=picker_model: (
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
