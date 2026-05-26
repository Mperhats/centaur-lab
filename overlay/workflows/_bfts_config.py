"""Shared LLM defaults and resolution for BFTS workflows.

Centaur config layering (matches ``slack_sync`` and overlay tools):

1. **Per-run** — optional fields on ``bfts_root.Input`` / ``bfts_tree.Input``
   (workflow POST ``run_input`` JSON).
2. **Deployment** — ``api.extraEnv`` in ``values.local.yaml`` (Helm → API pod
   env vars ``BFTS_*`` below).
3. **Code defaults** — constants in this module.
4. **Credentials** — ``secret("ANTHROPIC_API_KEY")`` / ``secret("OPENAI_API_KEY")``
   placeholders resolved by iron-proxy (never ``os.getenv`` for keys).

There is no separate workflow YAML config file; discovery is Python modules
only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_LLM_API_KEY_SECRET = "ANTHROPIC_API_KEY"
DEFAULT_DRAFT_MODEL = "claude-sonnet-4-20250514"
DEFAULT_FEEDBACK_MODEL = "claude-sonnet-4-20250514"
DEFAULT_VLM_MODEL = "claude-sonnet-4-20250514"

ENV_LLM_API_KEY_SECRET = "BFTS_LLM_API_KEY_SECRET"
ENV_DRAFT_MODEL = "BFTS_DRAFT_MODEL"
ENV_FEEDBACK_MODEL = "BFTS_FEEDBACK_MODEL"
ENV_VLM_MODEL = "BFTS_VLM_MODEL"


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
            or os.getenv(ENV_LLM_API_KEY_SECRET)
            or DEFAULT_LLM_API_KEY_SECRET
        ),
        draft_model=(
            draft_model or os.getenv(ENV_DRAFT_MODEL) or DEFAULT_DRAFT_MODEL
        ),
        feedback_model=(
            feedback_model or os.getenv(ENV_FEEDBACK_MODEL) or DEFAULT_FEEDBACK_MODEL
        ),
        vlm_model=(
            vlm_model or os.getenv(ENV_VLM_MODEL) or DEFAULT_VLM_MODEL
        ),
    )


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
