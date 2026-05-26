"""Test: pause/resume/stop hit the right CustomObjectsApi calls.

Mirrors KubernetesAgentSandboxBackend.pause_by_id / resume_by_id /
stop_by_id at .centaur/services/api/api/sandbox/kubernetes_agent_sandbox
.py:159-217.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bfts_executor.client import _KubernetesSandboxAPI


def _mk_api() -> tuple[_KubernetesSandboxAPI, MagicMock]:
    custom = MagicMock()
    custom.patch_namespaced_custom_object = AsyncMock(return_value=None)
    custom.delete_namespaced_custom_object = AsyncMock(return_value=None)
    custom.get_namespaced_custom_object = AsyncMock(
        return_value={"spec": {"replicas": 1}}
    )
    core = MagicMock()
    core.read_namespaced_pod = AsyncMock(return_value=type(
        "P", (), {"status": type("S", (), {"phase": "Running"})()}
    )())
    api = _KubernetesSandboxAPI(
        custom_api=custom,
        core_api=core,
        networking_api=MagicMock(),
        ws_core_api=MagicMock(),
        ws_api_client=MagicMock(),
        namespace="centaur-system",
    )
    return api, custom


@pytest.mark.asyncio
async def test_pause_patches_replicas_zero() -> None:
    api, custom = _mk_api()
    await api.pause_sandbox("sbx-1")
    custom.patch_namespaced_custom_object.assert_awaited_once()
    args = custom.patch_namespaced_custom_object.call_args.args
    assert args[0] == "agents.x-k8s.io"
    assert args[1] == "v1alpha1"
    assert args[2] == "centaur-system"
    assert args[3] == "sandboxes"
    assert args[4] == "sbx-1"
    assert args[5] == {"spec": {"replicas": 0}}
    # Merge-patch content type per upstream (kubernetes_agent_sandbox.py:171).
    assert custom.patch_namespaced_custom_object.call_args.kwargs == {
        "_content_type": "application/merge-patch+json",
    }


@pytest.mark.asyncio
async def test_resume_patches_replicas_one() -> None:
    api, custom = _mk_api()
    await api.resume_sandbox("sbx-1")
    custom.patch_namespaced_custom_object.assert_awaited_once()
    body = custom.patch_namespaced_custom_object.call_args.args[5]
    assert body == {"spec": {"replicas": 1}}


@pytest.mark.asyncio
async def test_stop_deletes_crd_and_swallows_404() -> None:
    api, custom = _mk_api()
    await api.stop_sandbox("sbx-1")
    custom.delete_namespaced_custom_object.assert_awaited_once()
    args = custom.delete_namespaced_custom_object.call_args.args
    assert args[:4] == ("agents.x-k8s.io", "v1alpha1", "centaur-system", "sandboxes")
    assert args[4] == "sbx-1"


@pytest.mark.asyncio
async def test_stop_is_idempotent_on_404() -> None:
    api, custom = _mk_api()
    exc = type("E", (Exception,), {"status": 404})
    custom.delete_namespaced_custom_object.side_effect = exc()
    # Must not raise.
    await api.stop_sandbox("sbx-1")
