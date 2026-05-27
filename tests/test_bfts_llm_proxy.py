"""Unit tests for BFTS batch iron-proxy URL resolution and direct egress."""

from __future__ import annotations

from packages.bfts_sdk.config import (
    ENV_LLM_DIRECT_EGRESS,
    ENV_LLM_HTTPS_PROXY,
    llm_direct_egress_enabled,
    resolve_llm_https_proxy,
)


def test_resolve_llm_https_proxy_unset(monkeypatch) -> None:
    monkeypatch.delenv(ENV_LLM_HTTPS_PROXY, raising=False)
    monkeypatch.delenv(ENV_LLM_DIRECT_EGRESS, raising=False)
    assert resolve_llm_https_proxy() is None


def test_resolve_llm_https_proxy_strips(monkeypatch) -> None:
    monkeypatch.delenv(ENV_LLM_DIRECT_EGRESS, raising=False)
    monkeypatch.setenv(
        ENV_LLM_HTTPS_PROXY,
        " http://centaur-batch-proxy.centaur-system.svc:8080 ",
    )
    assert (
        resolve_llm_https_proxy()
        == "http://centaur-batch-proxy.centaur-system.svc:8080"
    )


def test_resolve_llm_https_proxy_blank_is_none(monkeypatch) -> None:
    monkeypatch.delenv(ENV_LLM_DIRECT_EGRESS, raising=False)
    monkeypatch.setenv(ENV_LLM_HTTPS_PROXY, "   ")
    assert resolve_llm_https_proxy() is None


def test_direct_egress_disables_proxy(monkeypatch) -> None:
    monkeypatch.setenv(ENV_LLM_HTTPS_PROXY, "http://centaur-batch-proxy:8080")
    monkeypatch.setenv(ENV_LLM_DIRECT_EGRESS, "1")
    assert llm_direct_egress_enabled() is True
    assert resolve_llm_https_proxy() is None


def test_llm_http_client_direct_egress_ignores_https_proxy_env(monkeypatch) -> None:
    from packages.bfts_sdk import llm as llm_mod

    monkeypatch.setenv(ENV_LLM_DIRECT_EGRESS, "1")
    monkeypatch.setenv("HTTPS_PROXY", "http://centaur-api-proxy:8080")
    captured: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(llm_mod.httpx, "AsyncClient", _FakeClient)
    llm_mod.llm_http_client(30.0)
    assert captured.get("trust_env") is False
    assert "proxy" not in captured


def test_direct_egress_truthy_values(monkeypatch) -> None:
    for value in ("true", "YES", "on"):
        monkeypatch.setenv(ENV_LLM_DIRECT_EGRESS, value)
        assert llm_direct_egress_enabled() is True
