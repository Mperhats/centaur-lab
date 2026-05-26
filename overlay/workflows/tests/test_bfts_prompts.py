"""Test: prompt fragments compile, function specs round-trip."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_prompts import (
    METRIC_PARSE_SPEC,
    PLOT_SELECTION_SPEC,
    PROMPT_IMPL_GUIDELINE,
    PROMPT_RESP_FMT,
    REVIEW_FUNC_SPEC,
    VLM_FEEDBACK_SPEC,
    compile_prompt_to_md,
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
    assert "working" in PROMPT_IMPL_GUIDELINE


def test_resp_fmt_mentions_single_codeblock() -> None:
    assert "python" in PROMPT_RESP_FMT.lower()
    assert "codeblock" in PROMPT_RESP_FMT.lower() or "code block" in PROMPT_RESP_FMT.lower()
