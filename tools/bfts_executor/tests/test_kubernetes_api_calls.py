"""Test: run_command drives the WsApiClient exec pattern correctly.

We mock the websocket loop so the test exercises every channel branch
(STDOUT_CHANNEL, STDERR_CHANNEL, ERROR_CHANNEL) and the exit-code
parse via parse_error_data. Mirrors .centaur/services/api/api/sandbox/
kubernetes.py:1525-1551.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tools.bfts_executor.client import _KubernetesSandboxAPI


class _FakeMsg:
    def __init__(self, type_: int, data: bytes) -> None:
        self.type = type_
        self.data = data


class _FakeWS:
    def __init__(self, frames: list[_FakeMsg]) -> None:
        self._frames = list(frames)

    async def __aenter__(self) -> "_FakeWS":
        return self

    async def __aexit__(self, *a) -> None:
        return None

    async def receive(self) -> _FakeMsg:
        if not self._frames:
            from aiohttp import WSMsgType
            return _FakeMsg(WSMsgType.CLOSED, b"")
        return self._frames.pop(0)


@pytest.mark.asyncio
async def test_run_command_aggregates_stdout_and_returns_exit_zero() -> None:
    from aiohttp import WSMsgType
    from kubernetes_asyncio.stream.ws_client import STDOUT_CHANNEL

    ws_core = MagicMock()

    async def _connect(*args, **kwargs):
        return _FakeWS([
            _FakeMsg(WSMsgType.BINARY, bytes([STDOUT_CHANNEL]) + b"hello\n"),
        ])

    ws_core.connect_get_namespaced_pod_exec = _connect
    ws_api = MagicMock()
    ws_api.parse_error_data = MagicMock(return_value=0)
    api = _KubernetesSandboxAPI(
        ws_core_api=ws_core,
        ws_api_client=ws_api,
        core_api=MagicMock(),
        custom_api=MagicMock(),
        networking_api=MagicMock(),
        namespace="centaur-system",
    )
    res = await api.run_command("sbx-1", "echo hello", timeout_s=10.0)
    assert res.stdout == "hello\n"
    assert res.stderr == ""
    assert res.exit_code == 0


@pytest.mark.asyncio
async def test_run_command_captures_stderr_channel() -> None:
    from aiohttp import WSMsgType
    from kubernetes_asyncio.stream.ws_client import STDERR_CHANNEL

    ws_core = MagicMock()

    async def _connect(*args, **kwargs):
        return _FakeWS([
            _FakeMsg(WSMsgType.BINARY, bytes([STDERR_CHANNEL]) + b"boom\n"),
        ])

    ws_core.connect_get_namespaced_pod_exec = _connect
    ws_api = MagicMock()
    ws_api.parse_error_data = MagicMock(return_value=0)
    api = _KubernetesSandboxAPI(
        ws_core_api=ws_core,
        ws_api_client=ws_api,
        core_api=MagicMock(),
        custom_api=MagicMock(),
        networking_api=MagicMock(),
        namespace="centaur-system",
    )
    res = await api.run_command("sbx-1", "false", timeout_s=10.0)
    assert res.stderr == "boom\n"


@pytest.mark.asyncio
async def test_run_command_extracts_exit_code_from_error_channel() -> None:
    from aiohttp import WSMsgType
    from kubernetes_asyncio.stream.ws_client import ERROR_CHANNEL

    ws_core = MagicMock()
    payload = b'{"status":"Failure","reason":"NonZeroExitCode","details":{"causes":[{"reason":"ExitCode","message":"42"}]}}'

    async def _connect(*args, **kwargs):
        return _FakeWS([
            _FakeMsg(WSMsgType.BINARY, bytes([ERROR_CHANNEL]) + payload),
        ])

    ws_core.connect_get_namespaced_pod_exec = _connect
    ws_api = MagicMock()
    ws_api.parse_error_data = MagicMock(return_value=42)
    api = _KubernetesSandboxAPI(
        ws_core_api=ws_core,
        ws_api_client=ws_api,
        core_api=MagicMock(),
        custom_api=MagicMock(),
        networking_api=MagicMock(),
        namespace="centaur-system",
    )
    res = await api.run_command("sbx-1", "exit 42", timeout_s=10.0)
    assert res.exit_code == 42
    ws_api.parse_error_data.assert_called_once()


@pytest.mark.asyncio
async def test_namespace_defaults_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUBERNETES_NAMESPACE", "centaur-test")
    api = _KubernetesSandboxAPI()
    assert api.namespace == "centaur-test"
