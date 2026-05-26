"""Test: shared BFTS LLM defaults and resolution."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_config import (
    DEFAULT_DEBUG_PROB,
    DEFAULT_DRAFT_MODEL,
    DEFAULT_FEEDBACK_MODEL,
    DEFAULT_LLM_API_KEY_SECRET,
    DEFAULT_MAX_DEBUG_DEPTH,
    DEFAULT_METRIC_REDUCER,
    DEFAULT_NUM_DRAFTS,
    DEFAULT_NUM_WORKERS,
    DEFAULT_VLM_MODEL,
    ENV_DEBUG_PROB,
    ENV_DRAFT_MODEL,
    ENV_MAX_DEBUG_DEPTH,
    ENV_METRIC_REDUCER,
    ENV_NUM_DRAFTS,
    ENV_NUM_WORKERS,
    SOURCE_DEFAULT,
    SOURCE_ENV,
    SOURCE_HYPERPARAMS,
    SOURCE_INPUT,
    SearchSources,
    resolve_llm_settings,
    resolve_search_config,
    resolve_search_settings,
)
from bfts_root import Input as RootInput
from bfts_tree import Input as TreeInput

from ._mocks import MockPool


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
    settings, _sources = resolve_search_settings()
    assert settings.metric_reducer == "mean"


def test_resolve_search_settings_env_override(monkeypatch) -> None:
    monkeypatch.setenv(ENV_METRIC_REDUCER, "weighted_mean")
    settings, _sources = resolve_search_settings()
    assert settings.metric_reducer == "weighted_mean"


def test_resolve_search_settings_input_beats_env(monkeypatch) -> None:
    monkeypatch.setenv(ENV_METRIC_REDUCER, "weighted_mean")
    settings, _sources = resolve_search_settings(metric_reducer="min")
    assert settings.metric_reducer == "min"


def test_root_input_has_metric_reducer_field_defaulting_to_none() -> None:
    root = RootInput()
    assert root.metric_reducer is None


def test_tree_input_has_metric_reducer_field_defaulting_to_none() -> None:
    tree = TreeInput(run_id="r1", parent_run_id=None)
    assert tree.metric_reducer is None


def test_resolve_search_settings_rejects_unknown_reducer(monkeypatch) -> None:
    """Defense in depth: typos in the deployment env OR per-run Input must
    fail at resolve time (not later inside the selector)."""
    monkeypatch.delenv(ENV_METRIC_REDUCER, raising=False)
    with pytest.raises(ValueError, match="metric_reducer"):
        resolve_search_settings(metric_reducer="not_real")
    monkeypatch.setenv(ENV_METRIC_REDUCER, "also_not_real")
    with pytest.raises(ValueError, match="metric_reducer"):
        resolve_search_settings()


# --- SearchSettings: full search-policy fields (Phase 4c.4) ------------
#
# After 4c.4 ``SearchSettings`` carries every search-policy field
# ``bfts_root`` resolves: ``debug_prob``, ``max_debug_depth``,
# ``num_drafts``, ``num_workers``, ``metric_reducer``. The sync
# ``resolve_search_settings`` covers Input → env → default for callers
# without a pool (``bfts_tree`` standalone, unit tests). The async
# ``resolve_search_config`` adds the ``bfts_hyperparams`` DB layer
# between Input and env so reflection-tuned values feed forward to
# the next ``bfts_root`` run automatically.

def test_search_settings_has_all_five_fields() -> None:
    """SearchSettings is the canonical resolved-search-policy carrier;
    every field a tree iteration needs to be deterministic across
    siblings must live here."""
    monkeypatch_env = pytest.MonkeyPatch()
    try:
        for env_var in (
            ENV_DEBUG_PROB, ENV_MAX_DEBUG_DEPTH,
            ENV_NUM_DRAFTS, ENV_NUM_WORKERS, ENV_METRIC_REDUCER,
        ):
            monkeypatch_env.delenv(env_var, raising=False)
        s, _ = resolve_search_settings()
        assert s.debug_prob == DEFAULT_DEBUG_PROB
        assert s.max_debug_depth == DEFAULT_MAX_DEBUG_DEPTH
        assert s.num_drafts == DEFAULT_NUM_DRAFTS
        assert s.num_workers == DEFAULT_NUM_WORKERS
        assert s.metric_reducer == DEFAULT_METRIC_REDUCER
    finally:
        monkeypatch_env.undo()


def test_resolve_search_settings_layers_input_env_default(monkeypatch) -> None:
    """The sync resolver layers Input → env → default per field (no DB)."""
    monkeypatch.setenv(ENV_DEBUG_PROB, "0.6")
    monkeypatch.setenv(ENV_NUM_WORKERS, "5")
    monkeypatch.delenv(ENV_NUM_DRAFTS, raising=False)
    monkeypatch.delenv(ENV_MAX_DEBUG_DEPTH, raising=False)
    monkeypatch.delenv(ENV_METRIC_REDUCER, raising=False)

    s, _ = resolve_search_settings(debug_prob=0.7, num_drafts=8)
    assert s.debug_prob == 0.7  # Input wins
    assert s.num_drafts == 8  # Input wins
    assert s.num_workers == 5  # Env wins (no Input)
    assert s.max_debug_depth == DEFAULT_MAX_DEBUG_DEPTH  # default
    assert s.metric_reducer == DEFAULT_METRIC_REDUCER  # default


# --- resolve_search_config (Phase 4c.4 — adds DB layer) -----------------


@pytest.mark.asyncio
async def test_resolve_search_config_uses_input_override_first(
    monkeypatch,
) -> None:
    """Input override beats every other layer (DB row, env, default).

    Operator-supplied values must always win so an ad-hoc run can
    bypass the reflection-tuned default."""
    monkeypatch.delenv(ENV_DEBUG_PROB, raising=False)
    pool = MockPool(fetchrow_result={"debug_prob": 0.3})

    s, _ = await resolve_search_config(pool, debug_prob=0.7)
    assert s.debug_prob == 0.7


@pytest.mark.asyncio
async def test_resolve_search_config_falls_back_to_db_row(
    monkeypatch,
) -> None:
    """When Input is None the bfts_hyperparams row wins over env/default."""
    monkeypatch.delenv(ENV_DEBUG_PROB, raising=False)
    pool = MockPool(
        fetchrow_result={
            "debug_prob": 0.3,
            "max_debug_depth": 5,
            "num_drafts": 7,
            "num_workers": 6,
            "metric_reducer": "min",
        }
    )

    s, _ = await resolve_search_config(pool)
    assert s.debug_prob == 0.3
    assert s.max_debug_depth == 5
    assert s.num_drafts == 7
    assert s.num_workers == 6
    assert s.metric_reducer == "min"


@pytest.mark.asyncio
async def test_resolve_search_config_falls_back_to_env(monkeypatch) -> None:
    """When Input AND DB row are absent, env vars apply."""
    monkeypatch.setenv(ENV_DEBUG_PROB, "0.6")
    monkeypatch.setenv(ENV_MAX_DEBUG_DEPTH, "9")
    monkeypatch.setenv(ENV_NUM_DRAFTS, "11")
    monkeypatch.setenv(ENV_NUM_WORKERS, "13")
    monkeypatch.setenv(ENV_METRIC_REDUCER, "min")
    pool = MockPool(fetchrow_result=None)

    s, _ = await resolve_search_config(pool)
    assert s.debug_prob == 0.6
    assert s.max_debug_depth == 9
    assert s.num_drafts == 11
    assert s.num_workers == 13
    assert s.metric_reducer == "min"


@pytest.mark.asyncio
async def test_resolve_search_config_falls_back_to_default(
    monkeypatch,
) -> None:
    """All four layers absent → module DEFAULT_* constants."""
    for env_var in (
        ENV_DEBUG_PROB, ENV_MAX_DEBUG_DEPTH,
        ENV_NUM_DRAFTS, ENV_NUM_WORKERS, ENV_METRIC_REDUCER,
    ):
        monkeypatch.delenv(env_var, raising=False)
    pool = MockPool(fetchrow_result=None)

    s, _ = await resolve_search_config(pool)
    assert s.debug_prob == DEFAULT_DEBUG_PROB
    assert s.max_debug_depth == DEFAULT_MAX_DEBUG_DEPTH
    assert s.num_drafts == DEFAULT_NUM_DRAFTS
    assert s.num_workers == DEFAULT_NUM_WORKERS
    assert s.metric_reducer == DEFAULT_METRIC_REDUCER


@pytest.mark.asyncio
async def test_resolve_search_config_per_field_layering(monkeypatch) -> None:
    """Each field resolves independently — Input on one, DB on another,
    env on a third, default on the fourth, in the same call."""
    monkeypatch.setenv(ENV_NUM_WORKERS, "4")
    monkeypatch.delenv(ENV_DEBUG_PROB, raising=False)
    monkeypatch.delenv(ENV_MAX_DEBUG_DEPTH, raising=False)
    monkeypatch.delenv(ENV_NUM_DRAFTS, raising=False)
    monkeypatch.delenv(ENV_METRIC_REDUCER, raising=False)
    pool = MockPool(
        fetchrow_result={
            "debug_prob": 0.25,
            "max_debug_depth": None,
            "num_drafts": 6,
            "num_workers": None,
            "metric_reducer": None,
        }
    )

    s, _ = await resolve_search_config(pool, num_drafts=10)
    assert s.num_drafts == 10  # Input wins
    assert s.debug_prob == 0.25  # DB wins (Input absent)
    assert s.num_workers == 4  # env wins (DB null, Input absent)
    assert s.max_debug_depth == DEFAULT_MAX_DEBUG_DEPTH  # default (everything absent)
    assert s.metric_reducer == DEFAULT_METRIC_REDUCER  # default


@pytest.mark.asyncio
async def test_resolve_search_config_handles_none_pool(monkeypatch) -> None:
    """A None pool (e.g. bfts_tree standalone) skips the DB read but
    still resolves Input → env → default per field."""
    monkeypatch.delenv(ENV_DEBUG_PROB, raising=False)
    monkeypatch.delenv(ENV_MAX_DEBUG_DEPTH, raising=False)
    monkeypatch.delenv(ENV_NUM_DRAFTS, raising=False)
    monkeypatch.delenv(ENV_NUM_WORKERS, raising=False)
    monkeypatch.delenv(ENV_METRIC_REDUCER, raising=False)

    s, _ = await resolve_search_config(None, debug_prob=0.42)
    assert s.debug_prob == 0.42
    assert s.num_drafts == DEFAULT_NUM_DRAFTS


@pytest.mark.asyncio
async def test_resolve_search_config_rejects_unknown_reducer(
    monkeypatch,
) -> None:
    """Unknown reducer (Input, DB, or env) raises early — same fail-fast
    contract as resolve_search_settings."""
    monkeypatch.delenv(ENV_METRIC_REDUCER, raising=False)
    pool = MockPool(fetchrow_result={"metric_reducer": "definitely_not_real"})

    with pytest.raises(ValueError, match="metric_reducer"):
        await resolve_search_config(pool)


@pytest.mark.asyncio
async def test_resolve_search_config_queries_pool_once(monkeypatch) -> None:
    """Resolver issues exactly one fetchrow call for latest_hyperparams."""
    monkeypatch.delenv(ENV_DEBUG_PROB, raising=False)
    pool = MockPool(fetchrow_result=None)

    await resolve_search_config(pool)
    assert len(pool.fetchrow_calls) == 1


# --- Input default flips (Phase 4c.4) ------------------------------------
#
# Resolver chain Input → DB → env → default ONLY works if Input fields
# default to None — non-None dataclass defaults short-circuit every other
# layer. Lock that invariant on both Input dataclasses.


def test_root_input_search_fields_default_to_none() -> None:
    root = RootInput()
    assert root.num_drafts is None
    assert root.num_workers is None
    assert root.max_debug_depth is None
    assert root.debug_prob is None


def test_tree_input_search_fields_default_to_none() -> None:
    tree = TreeInput(run_id="r1", parent_run_id=None)
    assert tree.num_drafts is None
    assert tree.num_workers is None
    assert tree.max_debug_depth is None
    assert tree.debug_prob is None


# --- SearchSources per-field provenance (Phase 4c.4 follow-up) ----------
#
# ``SearchSources`` lets the postmortem "why did this run use X?" query
# bottom out at one ``SELECT config_json->'sources' FROM bfts_runs``.
# The tier values must be exactly the four ``SOURCE_*`` constants so a
# typo in the persistence layer fails loudly here, not in production.


@pytest.mark.asyncio
async def test_resolve_search_config_returns_sources_for_each_tier(
    monkeypatch,
) -> None:
    """One call hits all four tiers — Input wins one field, the
    bfts_hyperparams row wins another, env wins a third, default wins
    the fourth — and ``SearchSources`` records the winning tier per
    field independently."""
    monkeypatch.setenv(ENV_NUM_WORKERS, "4")
    monkeypatch.delenv(ENV_DEBUG_PROB, raising=False)
    monkeypatch.delenv(ENV_MAX_DEBUG_DEPTH, raising=False)
    monkeypatch.delenv(ENV_NUM_DRAFTS, raising=False)
    monkeypatch.delenv(ENV_METRIC_REDUCER, raising=False)
    pool = MockPool(
        fetchrow_result={
            "debug_prob": 0.25,        # DB wins for debug_prob
            "max_debug_depth": None,    # NULL → falls through to default
            "num_drafts": 6,            # would win, but Input overrides
            "num_workers": None,        # NULL → falls through to env
            "metric_reducer": None,     # NULL → falls through to default
        }
    )

    settings, sources = await resolve_search_config(pool, num_drafts=10)

    assert settings.num_drafts == 10 and sources.num_drafts == SOURCE_INPUT
    assert settings.debug_prob == 0.25 and sources.debug_prob == SOURCE_HYPERPARAMS
    assert settings.num_workers == 4 and sources.num_workers == SOURCE_ENV
    assert (
        settings.max_debug_depth == DEFAULT_MAX_DEBUG_DEPTH
        and sources.max_debug_depth == SOURCE_DEFAULT
    )
    assert (
        settings.metric_reducer == DEFAULT_METRIC_REDUCER
        and sources.metric_reducer == SOURCE_DEFAULT
    )


@pytest.mark.asyncio
async def test_resolve_search_config_marks_db_row_as_hyperparams(
    monkeypatch,
) -> None:
    """A pure DB fall-back (Input None, env unset) must record every
    field's source as ``hyperparams`` — confirming the DB tier label
    and excluding any accidental ``default`` slip-through."""
    for env_var in (
        ENV_DEBUG_PROB, ENV_MAX_DEBUG_DEPTH,
        ENV_NUM_DRAFTS, ENV_NUM_WORKERS, ENV_METRIC_REDUCER,
    ):
        monkeypatch.delenv(env_var, raising=False)
    pool = MockPool(
        fetchrow_result={
            "debug_prob": 0.3,
            "max_debug_depth": 5,
            "num_drafts": 7,
            "num_workers": 6,
            "metric_reducer": "min",
        }
    )

    _settings, sources = await resolve_search_config(pool)

    assert sources.debug_prob == SOURCE_HYPERPARAMS
    assert sources.max_debug_depth == SOURCE_HYPERPARAMS
    assert sources.num_drafts == SOURCE_HYPERPARAMS
    assert sources.num_workers == SOURCE_HYPERPARAMS
    assert sources.metric_reducer == SOURCE_HYPERPARAMS


def test_resolve_search_settings_sources_omit_hyperparams_tier(
    monkeypatch,
) -> None:
    """The sync (no-DB) resolver can never emit ``hyperparams`` — the
    tier doesn't exist for it. Locks the contract that ``bfts_tree``'s
    persistence path can't accidentally claim a DB row decided a
    field when the parent forwarded an env-tier value."""
    monkeypatch.setenv(ENV_NUM_WORKERS, "9")
    for env_var in (
        ENV_DEBUG_PROB, ENV_MAX_DEBUG_DEPTH,
        ENV_NUM_DRAFTS, ENV_METRIC_REDUCER,
    ):
        monkeypatch.delenv(env_var, raising=False)

    _settings, sources = resolve_search_settings(num_drafts=4)

    assert sources.num_drafts == SOURCE_INPUT
    assert sources.num_workers == SOURCE_ENV
    assert sources.debug_prob == SOURCE_DEFAULT
    assert sources.max_debug_depth == SOURCE_DEFAULT
    assert sources.metric_reducer == SOURCE_DEFAULT
    # No field claims hyperparams — that tier requires resolve_search_config.
    for field_value in (
        sources.debug_prob, sources.max_debug_depth,
        sources.num_drafts, sources.num_workers, sources.metric_reducer,
    ):
        assert field_value != SOURCE_HYPERPARAMS


def test_search_sources_is_frozen_dataclass() -> None:
    """SearchSources must be frozen so the resolver's snapshot can't be
    mutated after the fact (e.g. from inside a log formatter)."""
    s = SearchSources(
        debug_prob=SOURCE_INPUT,
        max_debug_depth=SOURCE_HYPERPARAMS,
        num_drafts=SOURCE_ENV,
        num_workers=SOURCE_DEFAULT,
        metric_reducer=SOURCE_DEFAULT,
    )
    with pytest.raises((AttributeError, Exception)):
        s.debug_prob = SOURCE_DEFAULT  # type: ignore[misc]
