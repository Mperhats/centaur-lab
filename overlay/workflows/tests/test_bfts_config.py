"""Test: shared BFTS LLM defaults and resolution."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_config import (
    DEFAULT_DRAFT_MODEL,
    DEFAULT_FEEDBACK_MODEL,
    DEFAULT_LLM_API_KEY_SECRET,
    DEFAULT_METRIC_REDUCER,
    DEFAULT_VLM_MODEL,
    ENV_DRAFT_MODEL,
    ENV_METRIC_REDUCER,
    resolve_llm_settings,
    resolve_search_settings,
)
from bfts_root import Input as RootInput
from bfts_tree import Input as TreeInput


def test_resolve_defaults_without_input_or_env(monkeypatch) -> None:
    monkeypatch.delenv(ENV_DRAFT_MODEL, raising=False)
    settings = resolve_llm_settings()
    assert settings.llm_api_key_secret == DEFAULT_LLM_API_KEY_SECRET
    assert settings.draft_model == DEFAULT_DRAFT_MODEL
    assert settings.feedback_model == DEFAULT_FEEDBACK_MODEL
    assert settings.vlm_model == DEFAULT_VLM_MODEL


def test_resolve_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("BFTS_DRAFT_MODEL", "gpt-4o-2024-11-20")
    settings = resolve_llm_settings()
    assert settings.draft_model == "gpt-4o-2024-11-20"
    assert settings.feedback_model == DEFAULT_FEEDBACK_MODEL


def test_input_overrides_take_precedence_over_env(monkeypatch) -> None:
    monkeypatch.setenv("BFTS_DRAFT_MODEL", "from-env")
    settings = resolve_llm_settings(draft_model="from-input")
    assert settings.draft_model == "from-input"


def test_shared_input_fields_default_to_none() -> None:
    root = RootInput()
    tree = TreeInput(run_id="r1", parent_run_id=None)
    for inp in (root, tree):
        assert inp.llm_api_key_secret is None
        assert inp.draft_model is None
        assert inp.feedback_model is None
        assert inp.vlm_model is None


def test_input_overrides_round_trip() -> None:
    root = RootInput(
        draft_model="gpt-4o-2024-11-20",
        feedback_model="gpt-4o-2024-11-20",
        vlm_model="gpt-4o-2024-11-20",
        llm_api_key_secret="OPENAI_API_KEY",
    )
    settings = resolve_llm_settings(
        draft_model=root.draft_model,
        feedback_model=root.feedback_model,
        vlm_model=root.vlm_model,
        llm_api_key_secret=root.llm_api_key_secret,
    )
    assert settings.draft_model == "gpt-4o-2024-11-20"
    assert settings.llm_api_key_secret == "OPENAI_API_KEY"


# --- SearchSettings (Phase 4g.2) ----------------------------------------
#
# `metric_reducer` is the first SearchSettings field; future Phase 4c
# work (debug_prob / max_debug_depth / num_workers) will land in the same
# dataclass alongside it, mirroring LLMSettings' layering.

def test_resolve_search_settings_default_is_mean(monkeypatch) -> None:
    monkeypatch.delenv(ENV_METRIC_REDUCER, raising=False)
    assert DEFAULT_METRIC_REDUCER == "mean"
    settings = resolve_search_settings()
    assert settings.metric_reducer == "mean"


def test_resolve_search_settings_env_override(monkeypatch) -> None:
    monkeypatch.setenv(ENV_METRIC_REDUCER, "weighted_mean")
    settings = resolve_search_settings()
    assert settings.metric_reducer == "weighted_mean"


def test_resolve_search_settings_input_beats_env(monkeypatch) -> None:
    monkeypatch.setenv(ENV_METRIC_REDUCER, "weighted_mean")
    settings = resolve_search_settings(metric_reducer="min")
    assert settings.metric_reducer == "min"


def test_root_input_has_metric_reducer_field_defaulting_to_none() -> None:
    root = RootInput()
    assert root.metric_reducer is None


def test_tree_input_has_metric_reducer_field_defaulting_to_none() -> None:
    tree = TreeInput(run_id="r1", parent_run_id=None)
    assert tree.metric_reducer is None
