"""Prompt fragments + OpenAI function specs for BFTS expansion calls.

All data is mirrored from .scientist/ai_scientist/treesearch/
parallel_agent.py:81-451 (research 02 §Agent turn shape, §Prompt
structure). Treat as a contract — every wire-shape downstream
(particularly the metric_parse_spec output) feeds directly into the
metric ingestion path on _bfts_state.

Underscore-prefixed: workflow loader skips it.
"""
from __future__ import annotations

from typing import Any, Iterable, Union

PromptType = Union[str, dict, list]


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

Save intermediate results to ``working/`` under your current working
directory (the runner has already chdir'd you there). Specifically:

- ``np.save(os.path.join('working', 'experiment_data.npy'), <data>)`` —
  every metric and per-dataset value you want graded.
- ``working/*.png`` — every plot you want reviewed.

Use ``device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')``;
fall back to CPU silently. Don't print large blobs to stdout — the
workflow caps captured output at ~5KB.

Mirrored from .scientist/ai_scientist/treesearch/parallel_agent.py:
296-394 (research 02 §Agent turn shape)."""

PROMPT_RESP_FMT: str = """## Response format

Respond in natural language, then a SINGLE Python codeblock (triple
backticks, ``python`` language tag). The runner extracts the codeblock
and writes it as ``runfile.py``."""


def render_prompts(*fragments: PromptType) -> str:
    """Concatenate fragments through ``compile_prompt_to_md`` for an LLM call."""
    return "\n".join(compile_prompt_to_md(f) for f in fragments)


__all__: Iterable[str] = (
    "compile_prompt_to_md",
    "render_prompts",
    "REVIEW_FUNC_SPEC",
    "METRIC_PARSE_SPEC",
    "VLM_FEEDBACK_SPEC",
    "PLOT_SELECTION_SPEC",
    "PROMPT_IMPL_GUIDELINE",
    "PROMPT_RESP_FMT",
)
