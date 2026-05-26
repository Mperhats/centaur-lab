"""Test: exec_python surfaces coreutils timeout(1) exit 124 as exc_type='TimeoutError'."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from tools.bfts_executor.client import BFTSExecutor


@dataclass
class _FakePodExecResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_s: float


class _TimeoutAPI:
    async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
        return None

    async def run_command(self, sandbox_id: str, command: str, *, timeout_s: float) -> _FakePodExecResult:
        # coreutils timeout(1) returns 124 on timeout-then-clean-exit;
        # 137 if SIGKILL fired (128+9). We surface both as TimeoutError —
        # the workflow doesn't need to distinguish.
        return _FakePodExecResult(stdout="partial\n", stderr="", exit_code=124, duration_s=10.0)


@pytest.mark.asyncio
async def test_exec_python_timeout_surfaces_TimeoutError() -> None:
    executor = BFTSExecutor(sandbox_api=_TimeoutAPI())
    result = await executor.exec_python(sandbox_id="sbx-t", code="import time; time.sleep(60)", timeout_s=10.0)
    assert result.exc_type == "TimeoutError"
    assert result.exc_info == {"exit_code": 124, "timeout_s": 10.0}
    assert result.term_out == ["partial\n"]


@pytest.mark.asyncio
async def test_exec_python_command_passes_kill_after_60() -> None:
    """Verify the SIGINT-then-SIGKILL-at-T+60 pattern is in the command string."""
    captured: list[str] = []

    class _CapturingAPI:
        async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
            return None

        async def run_command(self, sandbox_id: str, command: str, *, timeout_s: float) -> _FakePodExecResult:
            captured.append(command)
            return _FakePodExecResult(stdout="", stderr="", exit_code=0, duration_s=0.0)

    executor = BFTSExecutor(sandbox_api=_CapturingAPI())
    await executor.exec_python(sandbox_id="sbx-t2", code="pass", timeout_s=42.0)
    assert any("timeout --signal=INT --kill-after=60 42 python -u" in c for c in captured)
