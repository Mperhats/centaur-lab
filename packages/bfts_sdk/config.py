"""Shared LLM defaults and resolution for BFTS workflows.

Centaur config layering (matches ``slack_sync`` and overlay tools):

1. **Per-run** — optional fields on ``bfts_root.Input`` / ``bfts_tree.Input``
   (workflow POST ``run_input`` JSON).
2. **Reflection-tuned** — ``bfts_hyperparams`` latest row, written by the
   ``bfts_reflection_nightly`` workflow and consumed by
   ``resolve_search_config`` (search-policy fields only; LLM resolution
   has no DB layer because the deployment env owns model selection).
3. **Deployment** — ``api.extraEnv`` in ``values.local.yaml`` (Helm → API pod
   env vars ``BFTS_*`` below).
4. **Code defaults** — constants in this module.
5. **Credentials** — ``secret("ANTHROPIC_API_KEY")`` / ``secret("OPENAI_API_KEY")``
   placeholders resolved by iron-proxy (never ``os.getenv`` for keys).

There is no separate workflow YAML config file; discovery is Python modules
only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from packages.bfts_sdk.metric import DEFAULT_REDUCER, REDUCERS

if TYPE_CHECKING:
    import asyncpg

DEFAULT_LLM_API_KEY_SECRET = "ANTHROPIC_API_KEY"
DEFAULT_DRAFT_MODEL = "claude-sonnet-4-20250514"
DEFAULT_FEEDBACK_MODEL = "claude-sonnet-4-20250514"
DEFAULT_VLM_MODEL = "claude-sonnet-4-20250514"
DEFAULT_METRIC_REDUCER = DEFAULT_REDUCER

# Search-policy defaults — last-resort tier in resolve_search_config's
# Input → bfts_hyperparams DB row → BFTS_* env → these chain. Also
# read by bfts_reflection_nightly to seed the first hyperparams row.
DEFAULT_DEBUG_PROB = 0.5
DEFAULT_MAX_DEBUG_DEPTH = 3
DEFAULT_NUM_DRAFTS = 4
DEFAULT_NUM_WORKERS = 2
# F.2: how many recent executed-node summaries to inject as a markdown
# "Prior attempts" section into draft / improve prompts. 0 disables the
# memory injection entirely; the debug branch always skips (parent
# failure context is already in the prompt). Sakana's Journal-based
# memory is the loose upstream analog
# (.scientist/ai_scientist/treesearch/parallel_agent.py:2072-2081).
DEFAULT_PRIOR_ATTEMPTS_WINDOW = 5
# F.4: number of multi-seed re-evaluations of the best Stage-1 node.
# 0 disables the fan-out (preserves Phase 0-4 behavior). 3 is a typical
# small-sample stat sanity (mean / std over 3 seeds); higher values
# eat sandbox quota. Sakana's default is 3-5 in their config.
DEFAULT_NUM_SEEDS = 0

# Source-tier names returned by the resolver helpers and recorded into
# ``bfts_runs.config_json["sources"]`` for replay observability.
SOURCE_INPUT = "input"
SOURCE_HYPERPARAMS = "hyperparams"
SOURCE_ENV = "env"
SOURCE_DEFAULT = "default"

ENV_LLM_API_KEY_SECRET = "BFTS_LLM_API_KEY_SECRET"
ENV_DRAFT_MODEL = "BFTS_DRAFT_MODEL"
ENV_FEEDBACK_MODEL = "BFTS_FEEDBACK_MODEL"
ENV_VLM_MODEL = "BFTS_VLM_MODEL"
ENV_METRIC_REDUCER = "BFTS_METRIC_REDUCER"
ENV_DEBUG_PROB = "BFTS_DEBUG_PROB"
ENV_MAX_DEBUG_DEPTH = "BFTS_MAX_DEBUG_DEPTH"
ENV_NUM_DRAFTS = "BFTS_NUM_DRAFTS"
ENV_NUM_WORKERS = "BFTS_NUM_WORKERS"
ENV_PRIOR_ATTEMPTS_WINDOW = "BFTS_PRIOR_ATTEMPTS_WINDOW"
ENV_NUM_SEEDS = "BFTS_NUM_SEEDS"
ENV_LLM_HTTPS_PROXY = "BFTS_LLM_HTTPS_PROXY"
ENV_LLM_DIRECT_EGRESS = "BFTS_LLM_DIRECT_EGRESS"


def _env_knob(name: str) -> str | None:
    """Read a non-secret ``BFTS_*`` operator knob from the API pod env.

    Ruff's ``TID251`` (see ``ruff.toml``) bans bare ``os.getenv`` on
    the assumption every env read is a credential — wrong here. These
    are non-secret config tuners (``BFTS_DEBUG_PROB``,
    ``BFTS_DRAFT_MODEL`` and friends) injected via Helm
    ``api.extraEnv``, and the deployment env is the canonical source
    for them. Centralizing the read here is preferable to scattering
    ``# noqa: TID251`` across seven call sites: one annotated function
    instead of seven annotated lines, and the rationale lives next to
    the suppression.
    """
    return os.getenv(name)  # noqa: TID251


@dataclass(frozen=True)
class LLMSettings:
    """Resolved LLM configuration for one tree run."""

    llm_api_key_secret: str
    draft_model: str
    feedback_model: str
    vlm_model: str


def resolve_llm_settings(
    *,
    draft_model: str | None = None,
    feedback_model: str | None = None,
    vlm_model: str | None = None,
    llm_api_key_secret: str | None = None,
) -> LLMSettings:
    """Merge per-run Input overrides, deployment env, and code defaults.

    Resolution order for each field: explicit Input value → ``BFTS_*`` env
    (from Helm ``api.extraEnv``) → module default constant.
    """
    return LLMSettings(
        llm_api_key_secret=(
            llm_api_key_secret
            or _env_knob(ENV_LLM_API_KEY_SECRET)
            or DEFAULT_LLM_API_KEY_SECRET
        ),
        draft_model=(
            draft_model or _env_knob(ENV_DRAFT_MODEL) or DEFAULT_DRAFT_MODEL
        ),
        feedback_model=(
            feedback_model or _env_knob(ENV_FEEDBACK_MODEL) or DEFAULT_FEEDBACK_MODEL
        ),
        vlm_model=(
            vlm_model or _env_knob(ENV_VLM_MODEL) or DEFAULT_VLM_MODEL
        ),
    )


@dataclass(frozen=True)
class SearchSettings:
    """Resolved BFTS search-policy configuration for one tree run.

    Sibling of ``LLMSettings``. All five fields land here so a single
    snapshot governs a tree's selector (``debug_prob``,
    ``max_debug_depth``, ``num_workers``), draft fan-out
    (``num_drafts``), and node scoring (``metric_reducer``).
    Reflection-tuned values flow in via ``resolve_search_config`` (DB
    layer); ``resolve_search_settings`` is the no-DB sync sibling.
    """

    debug_prob: float
    max_debug_depth: int
    num_drafts: int
    num_workers: int
    metric_reducer: str
    prior_attempts_window: int
    num_seeds: int


@dataclass(frozen=True)
class SearchSources:
    """Which resolution tier won for each ``SearchSettings`` field.

    Values are one of ``"input"`` (caller-provided override),
    ``"hyperparams"`` (latest ``bfts_hyperparams`` DB row),
    ``"env"`` (``BFTS_*`` env var), or ``"default"`` (code constant).
    Persisted into ``bfts_runs.config_json["sources"]`` so an operator
    postmortem can answer "why did this run use debug_prob=X?" with a
    single ``SELECT config_json->'sources' ...`` query rather than
    cross-table archaeology.
    """

    debug_prob: str
    max_debug_depth: str
    num_drafts: str
    num_workers: str
    metric_reducer: str
    prior_attempts_window: str
    num_seeds: str


def _validate_reducer(reducer: str) -> str:
    if reducer not in REDUCERS:
        raise ValueError(
            f"unknown metric_reducer: {reducer!r} (valid: {', '.join(REDUCERS)})"
        )
    return reducer


def resolve_search_settings(
    *,
    debug_prob: float | None = None,
    max_debug_depth: int | None = None,
    num_drafts: int | None = None,
    num_workers: int | None = None,
    metric_reducer: str | None = None,
    prior_attempts_window: int | None = None,
    num_seeds: int | None = None,
) -> tuple[SearchSettings, SearchSources]:
    """Merge per-run Input overrides, deployment env, and code defaults.

    Resolution order per field: explicit Input value → ``BFTS_*`` env
    (from Helm ``api.extraEnv``) → module default constant. No DB
    layer; ``bfts_tree`` calls this when its parent has either
    forwarded already-resolved values or the tree was started
    standalone for testing/debugging. ``bfts_root`` instead uses
    ``resolve_search_config`` to layer ``bfts_hyperparams`` between
    Input and env. Unknown reducer strings (Input or env) raise
    ``ValueError`` here (fail-fast at run start). The companion
    ``SearchSources`` records which tier won each field; tier values
    here are limited to ``input`` / ``env`` / ``default`` (no
    ``hyperparams`` because there is no DB read).
    """
    debug_prob_val, debug_prob_src = _resolve_float(
        debug_prob, ENV_DEBUG_PROB, DEFAULT_DEBUG_PROB
    )
    max_debug_depth_val, max_debug_depth_src = _resolve_int(
        max_debug_depth, ENV_MAX_DEBUG_DEPTH, DEFAULT_MAX_DEBUG_DEPTH
    )
    num_drafts_val, num_drafts_src = _resolve_int(
        num_drafts, ENV_NUM_DRAFTS, DEFAULT_NUM_DRAFTS
    )
    num_workers_val, num_workers_src = _resolve_int(
        num_workers, ENV_NUM_WORKERS, DEFAULT_NUM_WORKERS
    )
    metric_reducer_raw, metric_reducer_src = _resolve_str(
        metric_reducer, ENV_METRIC_REDUCER, DEFAULT_METRIC_REDUCER
    )
    prior_attempts_window_val, prior_attempts_window_src = _resolve_int(
        prior_attempts_window,
        ENV_PRIOR_ATTEMPTS_WINDOW,
        DEFAULT_PRIOR_ATTEMPTS_WINDOW,
    )
    num_seeds_val, num_seeds_src = _resolve_int(
        num_seeds, ENV_NUM_SEEDS, DEFAULT_NUM_SEEDS,
    )
    settings = SearchSettings(
        debug_prob=debug_prob_val,
        max_debug_depth=max_debug_depth_val,
        num_drafts=num_drafts_val,
        num_workers=num_workers_val,
        metric_reducer=_validate_reducer(metric_reducer_raw),
        prior_attempts_window=prior_attempts_window_val,
        num_seeds=num_seeds_val,
    )
    sources = SearchSources(
        debug_prob=debug_prob_src,
        max_debug_depth=max_debug_depth_src,
        num_drafts=num_drafts_src,
        num_workers=num_workers_src,
        metric_reducer=metric_reducer_src,
        prior_attempts_window=prior_attempts_window_src,
        num_seeds=num_seeds_src,
    )
    return settings, sources


async def resolve_search_config(
    pool: asyncpg.Pool | None,
    *,
    debug_prob: float | None = None,
    max_debug_depth: int | None = None,
    num_drafts: int | None = None,
    num_workers: int | None = None,
    metric_reducer: str | None = None,
    prior_attempts_window: int | None = None,
    num_seeds: int | None = None,
) -> tuple[SearchSettings, SearchSources]:
    """Same resolution as ``resolve_search_settings`` plus a DB layer.

    Per-field order: explicit Input value → ``bfts_hyperparams`` latest
    row (written by the nightly reflection workflow) → ``BFTS_*`` env →
    module default. ``bfts_root`` uses this so reflection-tuned values
    feed forward to subsequent runs without operator intervention; the
    Input → DB → env → default chain is the canonical config story.
    The companion ``SearchSources`` records which tier won each field
    (``input`` / ``hyperparams`` / ``env`` / ``default``).

    A ``None`` pool skips the DB read (used by tests and any caller
    without a workflow context). DB values come from the most-recent
    ``bfts_hyperparams`` row; ``None`` in any policy field is only
    possible if a future schema change loosens a ``NOT NULL``, in
    which case the per-field default contract still applies (the
    layer is treated as absent and the env/default tier wins).
    """
    db_row: dict[str, Any] | None = None
    if pool is not None:
        from packages.bfts_sdk.hyperparams import latest_hyperparams

        db_row = await latest_hyperparams(pool)

    debug_prob_val, debug_prob_src = _resolve_float_with_db(
        debug_prob, db_row, "debug_prob",
        ENV_DEBUG_PROB, DEFAULT_DEBUG_PROB,
    )
    max_debug_depth_val, max_debug_depth_src = _resolve_int_with_db(
        max_debug_depth, db_row, "max_debug_depth",
        ENV_MAX_DEBUG_DEPTH, DEFAULT_MAX_DEBUG_DEPTH,
    )
    num_drafts_val, num_drafts_src = _resolve_int_with_db(
        num_drafts, db_row, "num_drafts",
        ENV_NUM_DRAFTS, DEFAULT_NUM_DRAFTS,
    )
    num_workers_val, num_workers_src = _resolve_int_with_db(
        num_workers, db_row, "num_workers",
        ENV_NUM_WORKERS, DEFAULT_NUM_WORKERS,
    )
    metric_reducer_raw, metric_reducer_src = _resolve_str_with_db(
        metric_reducer, db_row, "metric_reducer",
        ENV_METRIC_REDUCER, DEFAULT_METRIC_REDUCER,
    )
    prior_attempts_window_val, prior_attempts_window_src = _resolve_int_with_db(
        prior_attempts_window, db_row, "prior_attempts_window",
        ENV_PRIOR_ATTEMPTS_WINDOW, DEFAULT_PRIOR_ATTEMPTS_WINDOW,
    )
    num_seeds_val, num_seeds_src = _resolve_int_with_db(
        num_seeds, db_row, "num_seeds",
        ENV_NUM_SEEDS, DEFAULT_NUM_SEEDS,
    )
    settings = SearchSettings(
        debug_prob=debug_prob_val,
        max_debug_depth=max_debug_depth_val,
        num_drafts=num_drafts_val,
        num_workers=num_workers_val,
        metric_reducer=_validate_reducer(metric_reducer_raw),
        prior_attempts_window=prior_attempts_window_val,
        num_seeds=num_seeds_val,
    )
    sources = SearchSources(
        debug_prob=debug_prob_src,
        max_debug_depth=max_debug_depth_src,
        num_drafts=num_drafts_src,
        num_workers=num_workers_src,
        metric_reducer=metric_reducer_src,
        prior_attempts_window=prior_attempts_window_src,
        num_seeds=num_seeds_src,
    )
    return settings, sources


def _resolve_float(
    input_val: float | None, env_var: str, default: float
) -> tuple[float, str]:
    if input_val is not None:
        return float(input_val), SOURCE_INPUT
    env = _env_knob(env_var)
    if env is not None:
        return float(env), SOURCE_ENV
    return float(default), SOURCE_DEFAULT


def _resolve_int(
    input_val: int | None, env_var: str, default: int
) -> tuple[int, str]:
    if input_val is not None:
        return int(input_val), SOURCE_INPUT
    env = _env_knob(env_var)
    if env is not None:
        return int(env), SOURCE_ENV
    return int(default), SOURCE_DEFAULT


def _resolve_str(
    input_val: str | None, env_var: str, default: str
) -> tuple[str, str]:
    if input_val is not None:
        return input_val, SOURCE_INPUT
    env = _env_knob(env_var)
    if env is not None:
        return env, SOURCE_ENV
    return default, SOURCE_DEFAULT


def _db_value(
    db_row: dict[str, Any] | None, key: str
) -> Any | None:
    if db_row is None:
        return None
    return db_row.get(key)


def _resolve_float_with_db(
    input_val: float | None,
    db_row: dict[str, Any] | None,
    db_key: str,
    env_var: str,
    default: float,
) -> tuple[float, str]:
    if input_val is not None:
        return float(input_val), SOURCE_INPUT
    db_val = _db_value(db_row, db_key)
    if db_val is not None:
        return float(db_val), SOURCE_HYPERPARAMS
    return _resolve_float(None, env_var, default)


def _resolve_int_with_db(
    input_val: int | None,
    db_row: dict[str, Any] | None,
    db_key: str,
    env_var: str,
    default: int,
) -> tuple[int, str]:
    if input_val is not None:
        return int(input_val), SOURCE_INPUT
    db_val = _db_value(db_row, db_key)
    if db_val is not None:
        return int(db_val), SOURCE_HYPERPARAMS
    return _resolve_int(None, env_var, default)


def _resolve_str_with_db(
    input_val: str | None,
    db_row: dict[str, Any] | None,
    db_key: str,
    env_var: str,
    default: str,
) -> tuple[str, str]:
    if input_val is not None:
        return input_val, SOURCE_INPUT
    db_val = _db_value(db_row, db_key)
    if db_val is not None:
        return str(db_val), SOURCE_HYPERPARAMS
    return _resolve_str(None, env_var, default)


def resolve_llm_api_key(secret_name: str) -> str:
    """Return the iron-proxy placeholder for ``secret_name``.

    The workflow passes this string in outbound HTTP headers; iron-proxy
    substitutes the real credential at the network boundary.
    """
    from centaur_sdk import secret

    return secret(secret_name, "")


def api_key_secret_for_model(model: str) -> str:
    """Infra secret name matching the model provider (for tools / VLM)."""
    m = model.lower()
    if m.startswith("claude-") or m.startswith("anthropic."):
        return "ANTHROPIC_API_KEY"
    return "OPENAI_API_KEY"


def resolve_api_key_for_model(model: str) -> str:
    """Placeholder API key for a vision/chat model string."""
    return resolve_llm_api_key(api_key_secret_for_model(model))


def llm_direct_egress_enabled() -> bool:
    """Whether BFTS LLM calls bypass iron-proxy from the trusted API pod.

    Sandboxes must keep using per-sandbox iron-proxy. Only workflow workers
    running ``bfts_tree`` / VLM review in the API pod may set
    ``BFTS_LLM_DIRECT_EGRESS=1`` so Anthropic calls are not subject to
    iron-proxy's ~30s upstream header timeout (synthetic 502). Real keys
    still resolve via ``centaur_sdk.secret`` in the control plane.
    """
    raw = _env_knob(ENV_LLM_DIRECT_EGRESS)
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def resolve_llm_https_proxy() -> str | None:
    """Optional batch iron-proxy URL for BFTS-owned LLM httpx clients.

    When ``BFTS_LLM_DIRECT_EGRESS`` is enabled, returns ``None`` so httpx
    connects directly with credentials from ``resolve_llm_api_key``.

    Otherwise, when set via ``BFTS_LLM_HTTPS_PROXY`` (Helm ``api.extraEnv``),
    expand and VLM calls route through a dedicated proxy pool instead of the
    pod's default ``HTTPS_PROXY`` interactive pool.

    Returns ``None`` when unset so callers defer to httpx's normal
    ``HTTPS_PROXY`` env behavior.
    """
    if llm_direct_egress_enabled():
        return None
    raw = _env_knob(ENV_LLM_HTTPS_PROXY)
    if raw is None or not str(raw).strip():
        return None
    return str(raw).strip()
