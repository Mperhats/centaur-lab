"""Unit tests for BFTS batch iron-proxy URL resolution."""

from __future__ import annotations

from packages.bfts_sdk.config import ENV_LLM_HTTPS_PROXY, resolve_llm_https_proxy


def test_resolve_llm_https_proxy_unset(monkeypatch) -> None:
    monkeypatch.delenv(ENV_LLM_HTTPS_PROXY, raising=False)
    assert resolve_llm_https_proxy() is None


def test_resolve_llm_https_proxy_strips(monkeypatch) -> None:
    monkeypatch.setenv(
        ENV_LLM_HTTPS_PROXY,
        " http://centaur-batch-proxy.centaur-system.svc:8080 ",
    )
    assert (
        resolve_llm_https_proxy()
        == "http://centaur-batch-proxy.centaur-system.svc:8080"
    )


def test_resolve_llm_https_proxy_blank_is_none(monkeypatch) -> None:
    monkeypatch.setenv(ENV_LLM_HTTPS_PROXY, "   ")
    assert resolve_llm_https_proxy() is None
