"""Tests for ``SemanticScholarClient`` API-key resolution.

The upstream ``semanticscholar`` library accepts an ``api_key`` at
construction time and sends it as the ``x-api-key`` header on every
request. Resolution must happen lazily on first ``self.client`` access
(not at ``__init__`` time), because the ToolManager instantiates the
client during ``_collect_methods()`` when ``ToolContext.secrets`` is
still ``{}`` — eager resolution would permanently strip the header
even when the secret is later injected per-call.

These tests pin that behavior at the ``_get_api_key()`` boundary
(which is what feeds the library constructor) and at the ``self.client``
property (which lazily constructs the library instance). To avoid
coupling to the library's private storage (the ``api_key`` is squirreled
away as ``auth_header`` on an inner async helper), the property test
substitutes a recording fake for ``SemanticScholar`` and asserts on the
``api_key`` kwarg the library actually received.
"""

from __future__ import annotations

import os
from typing import Any

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


def test_get_api_key_returns_none_when_no_key_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anonymous calls: return ``None`` (not ``""``) so the library
    constructor doesn't send an empty ``x-api-key`` header."""
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)

    client = SemanticScholarClient()

    assert client._get_api_key() is None


def test_get_api_key_uses_constructor_injected_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env var must not bleed through when the caller passed an explicit key."""
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "ignored-env")

    client = SemanticScholarClient(api_key="abc")

    assert client._get_api_key() == "abc"


def test_get_api_key_lazy_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The env var is intentionally set AFTER construction to prove the
    fallback runs at resolution time, not at ``__init__`` time. In
    production the same delay happens between ``_collect_methods()`` and
    per-call secret resolution.
    """
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    client = SemanticScholarClient()

    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "from-env")

    assert client._get_api_key() == "from-env"


def test_get_api_key_is_instance_method() -> None:
    """Guard against the static-method regression: upstream convention is
    an instance method so subclasses (and per-instance state) work.
    """
    assert "self" in SemanticScholarClient._get_api_key.__code__.co_varnames


def _record_semantic_scholar_init(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """Replace the upstream ``SemanticScholar`` class with a recorder.

    Returns the list of kwargs every ``SemanticScholar(...)`` call sees,
    so tests can assert on the lazily-resolved ``api_key`` without
    inspecting the library's private storage.
    """
    calls: list[dict[str, Any]] = []

    class _RecordingSemanticScholar:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(dict(kwargs))

    monkeypatch.setattr(s2_client, "SemanticScholar", _RecordingSemanticScholar)
    return calls


def test_client_property_passes_lazily_resolved_key_to_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The library client is constructed on first ``client`` access, *not*
    at ``__init__`` time — so a secret injected after construction still
    reaches the library's ``api_key`` kwarg.
    """
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    calls = _record_semantic_scholar_init(monkeypatch)

    s2 = SemanticScholarClient()
    assert calls == []  # no eager construction

    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "lazy-key")
    _ = s2.client

    assert len(calls) == 1
    assert calls[0]["api_key"] == "lazy-key"


def test_client_property_passes_none_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anonymous path: no env, no constructor arg → library is given
    ``api_key=None``, so the ``x-api-key`` header isn't sent.
    """
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
    calls = _record_semantic_scholar_init(monkeypatch)

    s2 = SemanticScholarClient()
    _ = s2.client

    assert len(calls) == 1
    assert calls[0]["api_key"] is None
