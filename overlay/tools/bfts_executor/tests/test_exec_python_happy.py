"""Test: exec_python happy path returns Sakana-shape ExecutionResult."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from bfts_executor.client import BFTSExecutor
from bfts_executor.models import ExecutionResult


@dataclass
class _FakePodExecResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_s: float


class _FakeSandboxAPI:
    """Mocks just the two calls exec_python makes: write_file + run_command."""

    def __init__(self, exec_result: _FakePodExecResult) -> None:
        self._exec_result = exec_result
        self.writes: list[tuple[str, str, str]] = []
        self.commands: list[tuple[str, str, float]] = []

    async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
        self.writes.append((sandbox_id, path, content))

    async def run_command(
        self, sandbox_id: str, command: str, *, timeout_s: float
    ) -> _FakePodExecResult:
        self.commands.append((sandbox_id, command, timeout_s))
        return self._exec_result


@pytest.mark.asyncio
async def test_exec_python_returns_execution_result() -> None:
    fake = _FakeSandboxAPI(
        _FakePodExecResult(
            stdout="hi\n",
            stderr="",
            exit_code=0,
            duration_s=0.05,
        )
    )
    executor = BFTSExecutor(sandbox_api=fake)
    result = await executor.exec_python(
        sandbox_id="sbx-test-abc",
        code="print('hi')",
        timeout_s=60.0,
    )
    assert isinstance(result, ExecutionResult)
    assert result.term_out == ["hi\n"]
    assert result.exec_time == 0.05
    assert result.exc_type is None
    assert result.exc_info is None
    assert result.exc_stack is None


@pytest.mark.asyncio
async def test_exec_python_writes_runfile_and_invokes_python() -> None:
    fake = _FakeSandboxAPI(_FakePodExecResult(stdout="", stderr="", exit_code=0, duration_s=0.01))
    executor = BFTSExecutor(sandbox_api=fake)
    await executor.exec_python(sandbox_id="sbx-1", code="x = 1", timeout_s=10.0)
    # Code is written under the working dir prefix (matches Sakana's
    # 'working_dir = os.path.join(os.getcwd(), "working")' contract, research
    # 02 §Workspace layout).
    assert len(fake.writes) == 1
    sandbox_id, path, content = fake.writes[0]
    assert sandbox_id == "sbx-1"
    assert path == "/workspace/working/runfile.py"
    assert content == "x = 1"
    # Command runs via python -u in the working dir; SIGINT-then-SIGKILL is
    # the timeout mode (Task 1.4 covers the kill path).
    assert len(fake.commands) == 1
    cmd_sandbox, cmd_str, cmd_timeout = fake.commands[0]
    assert cmd_sandbox == "sbx-1"
    assert "python -u /workspace/working/runfile.py" in cmd_str
    assert "cd /workspace/working" in cmd_str
    assert cmd_timeout == 10.0


@pytest.mark.asyncio
async def test_exec_python_captures_exception_via_exit_code() -> None:
    """Non-zero exit code is captured as exc_type='SubprocessError'.

    Sakana inspects exc_type to set node.is_buggy in the bug-judge step
    (research 02 §Agent turn shape, LLM call #2). We surface non-zero
    exits as a non-None exc_type so the same downstream judge fires.
    """
    fake = _FakeSandboxAPI(
        _FakePodExecResult(
            stdout="some output\n",
            stderr='Traceback (most recent call last):\n  File "...", line 1, in <module>\nValueError: bad\n',
            exit_code=1,
            duration_s=0.02,
        )
    )
    executor = BFTSExecutor(sandbox_api=fake)
    result = await executor.exec_python(sandbox_id="sbx-2", code="raise ValueError('bad')", timeout_s=10.0)
    assert result.exc_type == "SubprocessError"
    assert result.exc_info == {"exit_code": 1}
    assert any("ValueError" in chunk for chunk in result.term_out)
