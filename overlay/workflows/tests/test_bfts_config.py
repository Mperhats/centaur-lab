"""Test: shared BFTS LLM defaults and resolution."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_config import (
    DEFAULT_DRAFT_MODEL,
    DEFAULT_FEEDBACK_MODEL,
    DEFAULT_LLM_API_KEY_SECRET,
    DEFAULT_VLM_MODEL,
    ENV_DRAFT_MODEL,
    resolve_llm_settings,
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
