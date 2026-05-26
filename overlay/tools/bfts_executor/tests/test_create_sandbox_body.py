"""Test: BFTSExecutor.create_sandbox emits the right Sandbox CRD body.

Asserts the body shape mirrors the upstream pattern in
.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:109-154
while substituting BFTS-specific labels (centaur.ai/bfts-sandbox=true,
NOT centaur.ai/managed=true — Spec correction #13 in the plan) and an
inline volumeClaimTemplates entry mounted at /workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import _KubernetesSandboxAPI


@pytest.mark.asyncio
async def test_create_sandbox_emits_expected_body() -> None:
    custom_api = MagicMock()
    custom_api.create_namespaced_custom_object = AsyncMock(return_value=None)
    networking_api = MagicMock()
    networking_api.create_namespaced_network_policy = AsyncMock(return_value=None)
    api = _KubernetesSandboxAPI(
        custom_api=custom_api,
        networking_api=networking_api,
        namespace="centaur-system",
    )
    await api.create_sandbox(
        sandbox_id="bfts-run-abc-tree-0",
        run_id="run-abc",
        image="bfts-executor:latest",
        storage_size="10Gi",
        storage_class=None,
    )
    custom_api.create_namespaced_custom_object.assert_awaited_once()
    args, kwargs = custom_api.create_namespaced_custom_object.call_args
    group, version, ns, plural, body = args
    assert group == "agents.x-k8s.io"
    assert version == "v1alpha1"
    assert ns == "centaur-system"
    assert plural == "sandboxes"
    assert body["apiVersion"] == "agents.x-k8s.io/v1alpha1"
    assert body["kind"] == "Sandbox"
    assert body["metadata"]["name"] == "bfts-run-abc-tree-0"
    labels = body["metadata"]["labels"]
    assert labels["centaur.ai/bfts-sandbox"] == "true"
    assert labels["centaur.ai/bfts-run"] == "run-abc"
    # Critical: do NOT inherit the chart's centaur.ai/managed=true selector
    # (.centaur/contrib/chart/templates/networkpolicy.yaml:307-327 would
    # then lock egress to api:8000 only).
    assert "centaur.ai/managed" not in labels
    spec = body["spec"]
    assert spec["replicas"] == 1
    assert spec["service"] is False
    assert spec["shutdownPolicy"] == "Retain"
    # Inline volumeClaimTemplates (Spec correction #12) — do NOT rely on
    # the global KUBERNETES_SANDBOX_STATE_VOLUME_ENABLED env var.
    assert len(spec["volumeClaimTemplates"]) == 1
    pvc = spec["volumeClaimTemplates"][0]
    assert pvc["metadata"]["name"] == "workspace"
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert pvc["spec"]["resources"]["requests"]["storage"] == "10Gi"
    assert "storageClassName" not in pvc["spec"]
    pod_spec = spec["podTemplate"]["spec"]
    container = pod_spec["containers"][0]
    assert container["image"] == "bfts-executor:latest"
    assert container["command"] == ["sleep", "infinity"]
    assert container["workingDir"] == "/workspace"
    mounts = container["volumeMounts"]
    assert any(m["name"] == "workspace" and m["mountPath"] == "/workspace" for m in mounts)


@pytest.mark.asyncio
async def test_create_sandbox_passes_storage_class_when_given() -> None:
    custom_api = MagicMock()
    custom_api.create_namespaced_custom_object = AsyncMock(return_value=None)
    networking_api = MagicMock()
    networking_api.create_namespaced_network_policy = AsyncMock(return_value=None)
    api = _KubernetesSandboxAPI(
        custom_api=custom_api,
        networking_api=networking_api,
        namespace="centaur-system",
    )
    await api.create_sandbox(
        sandbox_id="bfts-x",
        run_id="r1",
        image="bfts-executor:latest",
        storage_size="20Gi",
        storage_class="standard",
    )
    body = custom_api.create_namespaced_custom_object.call_args.args[4]
    pvc = body["spec"]["volumeClaimTemplates"][0]
    assert pvc["spec"]["storageClassName"] == "standard"
