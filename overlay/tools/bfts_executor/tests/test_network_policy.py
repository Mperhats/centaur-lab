"""Test: ensure_sandbox_egress_policy is idempotent + carries correct rules."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from network_policy import (
    POLICY_NAME,
    ensure_sandbox_egress_policy,
)


@pytest.mark.asyncio
async def test_creates_policy_with_bfts_selector_and_additive_egress() -> None:
    api = MagicMock()
    api.create_namespaced_network_policy = AsyncMock(return_value=None)
    await ensure_sandbox_egress_policy(api, namespace="centaur-system")
    api.create_namespaced_network_policy.assert_awaited_once()
    args = api.create_namespaced_network_policy.call_args.args
    assert args[0] == "centaur-system"
    body = args[1]
    assert body["apiVersion"] == "networking.k8s.io/v1"
    assert body["kind"] == "NetworkPolicy"
    assert body["metadata"]["name"] == POLICY_NAME == "bfts-sandbox-egress"
    spec = body["spec"]
    assert spec["podSelector"]["matchLabels"] == {"centaur.ai/bfts-sandbox": "true"}
    # Egress-only — Ingress + DNS are covered by the chart's default-deny
    # + -allow-dns policies.
    assert spec["policyTypes"] == ["Egress"]
    # Two rules: api:8000 + internet:443.
    rules = spec["egress"]
    assert len(rules) == 2
    api_rule = rules[0]
    assert api_rule["ports"] == [{"protocol": "TCP", "port": 8000}]
    assert any(
        peer.get("podSelector", {}).get("matchLabels", {}).get(
            "app.kubernetes.io/component"
        )
        == "api"
        for peer in api_rule["to"]
    )
    internet_rule = rules[1]
    assert internet_rule["ports"] == [{"protocol": "TCP", "port": 443}]
    assert "to" not in internet_rule or internet_rule["to"] == []


@pytest.mark.asyncio
async def test_409_conflict_is_silent_idempotent() -> None:
    api = MagicMock()
    conflict = type("E", (Exception,), {"status": 409})()
    api.create_namespaced_network_policy = AsyncMock(side_effect=conflict)
    # Must not raise.
    await ensure_sandbox_egress_policy(api, namespace="centaur-system")


@pytest.mark.asyncio
async def test_other_status_codes_propagate() -> None:
    api = MagicMock()
    err = type("E", (Exception,), {"status": 500})()
    api.create_namespaced_network_policy = AsyncMock(side_effect=err)
    with pytest.raises(Exception):
        await ensure_sandbox_egress_policy(api, namespace="centaur-system")
