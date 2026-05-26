"""Idempotent `bfts-sandbox-egress` NetworkPolicy.

The chart-shipped default-deny (.centaur/contrib/chart/templates/
networkpolicy.yaml:9-13) blocks all traffic, and `-allow-dns` (L15-34)
re-allows kube-dns. K8s NetworkPolicies are union-based, so adding this
namespace-scoped Egress-only rule on top is additive: pods labeled
`centaur.ai/bfts-sandbox: "true"` get api:8000 + internet:443 in
addition to DNS, while ingress remains denied.

We deliberately do NOT add `centaur.ai/managed: "true"` to BFTS pods —
that label is the podSelector for the chart's `-sandbox` policy at L307-
327 which restricts egress to api:8000 only and would block PyPI /
dataset fetches.

RBAC: `.centaur/contrib/chart/templates/rbac.yaml:39-41` already grants
the api service account create/delete/get/list/watch on
networking.k8s.io/networkpolicies.
"""
from __future__ import annotations

from typing import Any

POLICY_NAME = "bfts-sandbox-egress"


def _is_conflict(exc: BaseException) -> bool:
    return getattr(exc, "status", None) == 409


def _build_body() -> dict[str, Any]:
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": POLICY_NAME,
            "labels": {"centaur.ai/bfts": "true"},
        },
        "spec": {
            "podSelector": {"matchLabels": {"centaur.ai/bfts-sandbox": "true"}},
            "policyTypes": ["Egress"],
            "egress": [
                {
                    "to": [
                        {
                            "podSelector": {
                                "matchLabels": {
                                    "app.kubernetes.io/component": "api",
                                }
                            }
                        }
                    ],
                    "ports": [{"protocol": "TCP", "port": 8000}],
                },
                {
                    "ports": [{"protocol": "TCP", "port": 443}],
                },
            ],
        },
    }


async def ensure_sandbox_egress_policy(
    networking_api: Any, *, namespace: str
) -> None:
    """Create the policy if missing; swallow 409 if it already exists."""
    try:
        await networking_api.create_namespaced_network_policy(namespace, _build_body())
    except Exception as exc:
        if not _is_conflict(exc):
            raise
