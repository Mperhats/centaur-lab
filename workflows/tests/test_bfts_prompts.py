"""Test: prompt fragments compile, function specs round-trip."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_metric import mean as metric_mean
from _bfts_prompts import (
    METRIC_PARSE_SPEC,
    PLOT_SELECTION_SPEC,
    PROMPT_IMPL_GUIDELINE,
    PROMPT_RESP_FMT,
    REVIEW_FUNC_SPEC,
    VLM_FEEDBACK_SPEC,
    compile_prompt_to_md,
    prior_attempts_section,
)


def test_compile_simple_dict() -> None:
    out = compile_prompt_to_md({"Header": "value"}, depth=1)
    assert "# Header" in out
    assert "value" in out


def test_compile_nested_dict() -> None:
    out = compile_prompt_to_md({"Outer": {"Inner": "v"}}, depth=1)
    assert "# Outer" in out
    assert "## Inner" in out
    assert "v" in out


def test_compile_list_becomes_bullets() -> None:
    out = compile_prompt_to_md({"Items": ["a", "b"]}, depth=1)
    assert "# Items" in out
    assert "- a" in out
    assert "- b" in out


def test_review_func_spec_has_required_fields() -> None:
    props = REVIEW_FUNC_SPEC["function"]["parameters"]["properties"]
    assert "is_bug" in props
    assert props["is_bug"]["type"] == "boolean"
    assert "summary" in props
    assert props["summary"]["type"] == "string"


def test_vlm_feedback_spec_returns_validity_flag() -> None:
    props = VLM_FEEDBACK_SPEC["function"]["parameters"]["properties"]
    assert "valid_plots_received" in props
    assert "vlm_feedback_summary" in props
    assert "plot_analyses" in props


def test_metric_parse_spec_shape() -> None:
    props = METRIC_PARSE_SPEC["function"]["parameters"]["properties"]
    assert "metric_names" in props


def test_plot_selection_spec_present() -> None:
    assert PLOT_SELECTION_SPEC["function"]["name"] == "select_top_plots"


def test_impl_guideline_mentions_experiment_data_npy() -> None:
    assert "experiment_data.npy" in PROMPT_IMPL_GUIDELINE


def test_impl_guideline_does_not_instruct_saving_to_working_subdir() -> None:
    """Phase 4h gave every expansion its own per-node ``working_dir``
    (e.g. ``/workspace/node_<uuid>/``) and ``cd``'s into it before
    running. Nesting an additional ``working/`` subdir inside that
    per-node dir is redundant and was the cause of the 2026-05-26
    live failure where the agent saved metadata into the per-node
    dir but downstream metric_parse / plot prompts read from
    ``working/experiment_data.npy`` (resolving to
    ``/workspace/node_<uuid>/working/experiment_data.npy`` — a path
    that doesn't exist). The guideline must NOT instruct
    ``np.save('working/...', ...)`` or similar.
    """
    forbidden_patterns = (
        "np.save('working/",
        "np.save(\"working/",
        "'working/experiment_data.npy'",
        "\"working/experiment_data.npy\"",
        "to ``working/``",
        "savefig('working/",
        "savefig(\"working/",
    )
    for pat in forbidden_patterns:
        assert pat not in PROMPT_IMPL_GUIDELINE, (
            f"guideline must not instruct {pat!r}; cwd is already the "
            "per-node working_dir, so paths should be cwd-relative"
        )


def test_resp_fmt_mentions_single_codeblock() -> None:
    assert "python" in PROMPT_RESP_FMT.lower()
    assert "codeblock" in PROMPT_RESP_FMT.lower() or "code block" in PROMPT_RESP_FMT.lower()


def test_metric_parse_spec_payload_round_trips_to_metric_mean() -> None:
    """Wire-shape contract: a payload conforming to METRIC_PARSE_SPEC's
    nested schema must be readable by _bfts_metric.mean() with no shape
    massaging."""
    payload = {
        "metric_names": [
            {
                "metric_name": "val_loss",
                "lower_is_better": True,
                "description": "validation loss across folds",
                "data": [
                    {"dataset_name": "fold_0", "final_value": 0.4, "best_value": 0.3},
                    {"dataset_name": "fold_1", "final_value": 0.6, "best_value": 0.5},
                ],
            }
        ]
    }
    assert metric_mean(payload) == 0.5


def test_function_spec_names_are_unique() -> None:
    names = {
        REVIEW_FUNC_SPEC["function"]["name"],
        METRIC_PARSE_SPEC["function"]["name"],
        VLM_FEEDBACK_SPEC["function"]["name"],
        PLOT_SELECTION_SPEC["function"]["name"],
    }
    assert len(names) == 4


# ---------------------------------------------------------------------------
# F.2: prior_attempts_section markdown rendering.
# ---------------------------------------------------------------------------


def test_prior_attempts_section_renders_bullets_oldest_first() -> None:
    """``summaries`` arrives most-recent-first from
    ``list_recent_node_summaries``; the renderer reverses so the LLM
    reads chronologically. Each bullet carries the 8-char node id
    prefix, stage, buggy flag, plan first-line, and analysis."""
    summaries = [
        {"node_id": "node-newer-001", "stage_name": "improve",
         "plan": "scale lr by 10x\nthen something else",
         "is_buggy": True, "analysis": "diverged after 50 steps"},
        {"node_id": "node-older-002", "stage_name": "draft",
         "plan": "baseline", "is_buggy": False, "analysis": "ran clean"},
    ]
    out = prior_attempts_section(summaries)

    assert "## Prior attempts" in out
    n_older_idx = out.index("node-old")
    n_newer_idx = out.index("node-new")
    assert n_older_idx < n_newer_idx, (
        "newest summary must appear LAST in the rendered prompt body"
    )
    assert "buggy: yes" in out  # newer
    assert "buggy: no" in out   # older
    # Plan is truncated to first line; the multiline part doesn't leak.
    assert "then something else" not in out
    assert "diverged after 50 steps" in out
    assert "ran clean" in out


def test_prior_attempts_section_returns_empty_for_no_summaries() -> None:
    """Empty list → empty string so callers can append the section
    unconditionally without a stray ``## Prior attempts`` header."""
    assert prior_attempts_section([]) == ""


def test_prior_attempts_section_tolerates_missing_optional_fields() -> None:
    """Rows missing ``plan`` / ``analysis`` / ``stage_name`` get
    placeholder strings rather than crashing on KeyError. The DB row
    schema technically NOT-NULLs these, but a defensive renderer
    survives a future loose-schema migration."""
    summaries = [{"node_id": "n-1", "is_buggy": False}]
    out = prior_attempts_section(summaries)

    assert "## Prior attempts" in out
    assert "(no plan recorded)" in out
    assert "(no analysis)" in out
