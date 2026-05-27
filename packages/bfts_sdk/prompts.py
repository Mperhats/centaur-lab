"""Prompt fragments + OpenAI function specs for BFTS expansion calls.

All data is mirrored from .scientist/ai_scientist/treesearch/
parallel_agent.py:81-451 (research 02 §Agent turn shape, §Prompt
structure). Treat as a contract — every wire-shape downstream
(particularly the metric_parse_spec output) feeds directly into the
metric ingestion path on _bfts_state.

Underscore-prefixed: workflow loader skips it.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

PromptType = str | dict | list


def compile_prompt_to_md(prompt: PromptType, depth: int = 1) -> str:
    """Compile a nested dict/list/str prompt into markdown.

    Mirrors Sakana's compile_prompt_to_md (backend/utils.py:44-102):
    dict keys become ``#``-headers at the given depth; lists become
    bullet items; strings are emitted as-is. Verbatim parity matters
    because every prompt downstream is built as nested dicts and the
    LLM-prompt-engineering work assumes this exact rendering.

    Non-(str|list|dict) values fall back to ``str(value)`` — Sakana
    parity, but caller-beware: passing ``None`` will render the literal
    string ``"None"`` into the LLM prompt. Task 2.7's expansion driver
    should coerce nullable DB columns (e.g. ``parent_code``) to ``""``
    before assembling the prompt dict.
    """
    if isinstance(prompt, str):
        return prompt + "\n"
    if isinstance(prompt, list):
        return "\n".join(f"- {compile_prompt_to_md(p, depth + 1).rstrip()}" for p in prompt) + "\n"
    if isinstance(prompt, dict):
        parts: list[str] = []
        for key, value in prompt.items():
            header = "#" * depth + " " + str(key)
            parts.append(header + "\n" + compile_prompt_to_md(value, depth + 1))
        return "\n".join(parts) + "\n"
    return str(prompt) + "\n"


REVIEW_FUNC_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_review",
        "description": "Summarize whether the experiment ran successfully.",
        "parameters": {
            "type": "object",
            "properties": {
                "is_bug": {"type": "boolean", "description": "True if execution failed or returned nonsense."},
                "summary": {"type": "string", "description": "One-paragraph summary."},
            },
            "required": ["is_bug", "summary"],
        },
    },
}

METRIC_PARSE_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_metrics",
        "description": "Emit the parsed metric values from the metric-parse exec stdout.",
        "parameters": {
            "type": "object",
            "properties": {
                "metric_names": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "metric_name": {"type": "string"},
                            "lower_is_better": {"type": "boolean"},
                            "description": {"type": "string"},
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "dataset_name": {"type": "string"},
                                        "final_value": {"type": "number"},
                                        "best_value": {"type": "number"},
                                    },
                                    "required": ["dataset_name", "final_value", "best_value"],
                                },
                            },
                        },
                        "required": ["metric_name", "lower_is_better", "description", "data"],
                    },
                }
            },
            "required": ["metric_names"],
        },
    },
}

VLM_FEEDBACK_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_vlm_feedback",
        "description": "Review the plots and judge whether they are valid and informative.",
        "parameters": {
            "type": "object",
            "properties": {
                "plot_analyses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"analysis": {"type": "string"}},
                        "required": ["analysis"],
                    },
                },
                "valid_plots_received": {"type": "boolean"},
                "vlm_feedback_summary": {"type": "string"},
            },
            "required": ["plot_analyses", "valid_plots_received", "vlm_feedback_summary"],
        },
    },
}

PLOT_SELECTION_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "select_top_plots",
        "description": "Select up to 10 most relevant plots for VLM review.",
        "parameters": {
            "type": "object",
            "properties": {
                "selected_indices": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0},
                    "minItems": 1,
                    "maxItems": 10,
                }
            },
            "required": ["selected_indices"],
        },
    },
}

PROMPT_IMPL_GUIDELINE: str = """## Implementation guideline

Save intermediate results to your current working directory (the
runner has already chdir'd you into a per-node workspace). Specifically:

- ``np.save('experiment_data.npy', <data>)`` — every metric and
  per-dataset value you want graded. Use a Python dict mapping names
  to scalars / arrays, and rely on ``allow_pickle=True`` when loading.
- ``*.png`` — every plot you want reviewed. Use ``plt.savefig('foo.png')``
  rather than ``plt.show()``; the runner is headless.

Available packages in the sandbox runtime (bfts-executor image):
``numpy``, ``scipy``, ``scikit-learn``, ``matplotlib``. Other libraries
(``torch``, ``tensorflow``, ``transformers``, ``jax``, …) are NOT
installed — prefer sklearn / numpy / scipy for toy experiments.
If you genuinely need a deep-learning library, ``pip install`` it at
the top of your script (the sandbox has network egress for PyPI).

Don't print large blobs to stdout — the workflow caps captured
output at ~5KB.

Path note: Phase 4h placed each expansion in its own per-node working
directory (``/workspace/<node_id>/``) and the runner ``cd``'s into
that directory before invoking your script. So a bare filename like
``experiment_data.npy`` resolves correctly without any subdirectory
prefix — do NOT prefix with ``working/`` because that would resolve
to a nested ``/workspace/<node_id>/working/`` path which the
downstream metric_parse + collect_artifacts steps don't read.

Mirrored from .scientist/ai_scientist/treesearch/parallel_agent.py:
296-394 (research 02 §Agent turn shape)."""

PROMPT_RESP_FMT: str = """## Response format

Respond in natural language, then a SINGLE Python codeblock (triple
backticks, ``python`` language tag). The runner extracts the codeblock
and writes it as ``runfile.py``."""


def render_prompts(*fragments: PromptType) -> str:
    """Concatenate fragments through ``compile_prompt_to_md`` for an LLM call."""
    return "\n".join(compile_prompt_to_md(f) for f in fragments)


def prior_attempts_section(summaries: list[dict[str, Any]]) -> str:
    """Render last-K node summaries as a markdown ``## Prior attempts`` section.

    ``summaries`` arrives most-recent-first from
    ``_bfts_state.list_recent_node_summaries``; we reverse so the LLM
    reads them chronologically (oldest → newest). Empty list returns
    the empty string so callers can append unconditionally without a
    null-section header.

    Each summary becomes one bullet with the node's short id, stage,
    buggy flag, the first line of its plan, and the (one-line)
    analysis. Sakana's Journal-based memory is the upstream analog
    (parallel_agent.py:2072-2081); the cheap, no-extra-LLM-call
    fixed-window option here resolves research 02 §OQ #7.
    """
    if not summaries:
        return ""
    lines = ["## Prior attempts (oldest first)\n"]
    for s in reversed(summaries):
        buggy = "yes" if s.get("is_buggy") else "no"
        plan_raw = (s.get("plan") or "").strip().splitlines()
        plan_one_line = plan_raw[0] if plan_raw else "(no plan recorded)"
        analysis_raw = (s.get("analysis") or "").strip()
        analysis_one_line = (
            analysis_raw.splitlines()[0]
            if analysis_raw
            else "(no analysis)"
        )
        node_id = s.get("node_id") or "?"
        short_nid = node_id[:8] if isinstance(node_id, str) else "?"
        lines.append(
            f"- **{short_nid}** ({s.get('stage_name', '?')}, "
            f"buggy: {buggy}): {plan_one_line} — {analysis_one_line}"
        )
    return "\n".join(lines) + "\n"


__all__: Iterable[str] = (
    "METRIC_PARSE_SPEC",
    "PLOT_SELECTION_SPEC",
    "PROMPT_IMPL_GUIDELINE",
    "PROMPT_RESP_FMT",
    "REVIEW_FUNC_SPEC",
    "VLM_FEEDBACK_SPEC",
    "compile_prompt_to_md",
    "prior_attempts_section",
    "render_prompts",
)
