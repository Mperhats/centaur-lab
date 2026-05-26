"""Tests for ``SemanticScholarClient`` API-key resolution.

The Graph API can be called anonymously or with an ``x-api-key`` header.
Resolution must happen at request time (inside ``_headers()``), not at
``__init__`` — the tool loader instantiates the client during
``_collect_methods()`` when ``ToolContext.secrets`` is still ``{}``, so
eager resolution would permanently strip the header even when the
secret is later injected per-call.

These tests pin that behavior. ``secret(...)`` is monkeypatched to an
env-only resolver so the assertions stay hermetic regardless of which
backend the SDK auto-configures.
"""

from __future__ import annotations

import os

import pytest

from tools.semantic_scholar import client as s2_client
from tools.semantic_scholar.client import SemanticScholarClient


@pytest.fixture(autouse=True)
def _env_only_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``secret(...)`` to behave like a pure env-var lookup.

    The default SDK backend is ``StubBackend`` which echoes the key name
    when the env var is unset, so the "anonymous" assertion below would
    otherwise depend on global SDK state instead of the lazy-fallback
    logic we want to exercise.
    """
    monkeypatch.setattr(
        s2_client,
        "secret",
        lambda key, default="": os.environ.get(key, default),  # noqa: TID251
    )


def test_headers_empty_when_no_key_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)

    client = SemanticScholarClient()

    assert client._headers() == {}


def test_headers_uses_constructor_injected_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Env var must not bleed through when the caller passed an explicit key.
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "ignored-env")

    client = SemanticScholarClient(api_key="abc")

    assert client._headers() == {"x-api-key": "abc"}


def test_headers_lazy_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    # The env var is intentionally set AFTER construction to prove the
    # fallback runs at ``_headers()`` time, not at ``__init__`` time.
    # In production the same delay happens between ``_collect_methods()``
    # and per-call secret resolution.
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    client = SemanticScholarClient()

    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "from-env")

    assert client._headers() == {"x-api-key": "from-env"}


def test_get_api_key_is_instance_method() -> None:
    # Guard against the static-method regression: upstream convention is
    # an instance method so subclasses (and per-instance state) work.
    assert "self" in SemanticScholarClient._get_api_key.__code__.co_varnames
