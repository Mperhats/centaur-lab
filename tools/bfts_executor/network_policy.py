"""Idempotent `bfts-sandbox-egress` NetworkPolicy.

The namespace's baseline default-deny + allow-dns NetworkPolicies block
all traffic except DNS to kube-dns. K8s NetworkPolicies are union-based,
so this namespace-scoped Egress-only rule layers on top: pods labeled
`centaur.ai/bfts-sandbox: "true"` get outbound HTTPS (TCP 443) in
addition to DNS, while ingress remains denied.

We deliberately do NOT add `centaur.ai/managed: "true"` to BFTS pods —
that label is the podSelector for the chart's `-sandbox` policy at L307-
327 which restricts egress to api:8000 only and would block PyPI /
dataset fetches.

API egress (TCP 8000) is intentionally NOT granted. BFTS sandbox pods
execute Python — they fetch wheels, datasets, and call out to LLM / VLM
endpoints over HTTPS, but they never call back into the api pod (the
api drives the executor over the Kubernetes apiserver Exec subresource,
not the other way around). Cutting the rule removes dead configuration
and a needless attack surface.

When this module runs in the `centaur-bfts` sandbox namespace (the
default once `BFTS_SANDBOX_NAMESPACE=centaur-bfts` is set in
api.extraEnv), the api ServiceAccount's authorization to create
NetworkPolicies here comes from the namespaced `api-sandbox-manager`
Role + RoleBinding shipped in `centaur-lab-infra` alongside the
namespace itself, mirroring the existing centaur-system RBAC from the
chart's rbac.yaml.
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
