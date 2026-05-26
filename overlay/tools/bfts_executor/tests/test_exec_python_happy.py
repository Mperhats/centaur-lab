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


@pytest.mark.asyncio
async def test_exec_python_per_node_working_dir_isolation() -> None:
    """Two exec_python calls with different working_dir values must touch disjoint paths.

    Phase 4h prerequisite (`docs/superpowers/plans/2026-05-26-bfts-phase4.md`
    §Task 4h.1): per-node `/workspace/<node_id>/` working directories let
    intra-tree parallel expansion fan out without racing on
    `runfile.py` / `experiment_data.npy` / `*.png` inside a single
    `/workspace/working/`.
    """
    fake = _FakeSandboxAPI(_FakePodExecResult(stdout="", stderr="", exit_code=0, duration_s=0.0))
    executor = BFTSExecutor(sandbox_api=fake)

    await executor.exec_python(
        sandbox_id="sbx-iso",
        code="x = 1",
        timeout_s=10.0,
        working_dir="node_a",
    )
    await executor.exec_python(
        sandbox_id="sbx-iso",
        code="x = 2",
        timeout_s=10.0,
        working_dir="node_b",
    )

    assert len(fake.writes) == 2
    _, path_a, _ = fake.writes[0]
    _, path_b, _ = fake.writes[1]
    assert path_a == "/workspace/node_a/runfile.py"
    assert path_b == "/workspace/node_b/runfile.py"
    # Distinct write targets => no clobber between concurrent expansions.
    assert path_a != path_b

    assert len(fake.commands) == 2
    _, cmd_a, _ = fake.commands[0]
    _, cmd_b, _ = fake.commands[1]
    # Each command mkdirs + chdirs into its own per-node workspace and
    # runs *that* node's runfile.py.
    assert "mkdir -p /workspace/node_a" in cmd_a
    assert "cd /workspace/node_a" in cmd_a
    assert "python -u /workspace/node_a/runfile.py" in cmd_a
    assert "mkdir -p /workspace/node_b" in cmd_b
    assert "cd /workspace/node_b" in cmd_b
    assert "python -u /workspace/node_b/runfile.py" in cmd_b
    # And neither command references the other node's directory.
    assert "node_b" not in cmd_a
    assert "node_a" not in cmd_b


@pytest.mark.asyncio
async def test_exec_python_default_working_dir_is_back_compat() -> None:
    """Calling exec_python without working_dir must hit /workspace/working/.

    Phase 0–3 callers (`_bfts_expand.py`) do not pass `working_dir`, so the
    default has to keep the legacy Sakana-shape path verbatim.
    """
    fake = _FakeSandboxAPI(_FakePodExecResult(stdout="", stderr="", exit_code=0, duration_s=0.0))
    executor = BFTSExecutor(sandbox_api=fake)

    await executor.exec_python(sandbox_id="sbx-default", code="x = 1", timeout_s=10.0)

    assert len(fake.writes) == 1
    _, path, _ = fake.writes[0]
    assert path == "/workspace/working/runfile.py"
    assert len(fake.commands) == 1
    _, cmd, _ = fake.commands[0]
    assert "mkdir -p /workspace/working" in cmd
    assert "cd /workspace/working" in cmd
    assert "python -u /workspace/working/runfile.py" in cmd


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "",                # empty
        "../escape",       # path traversal
        "foo/bar",         # contains slash => could escape workspace
        "/abs/path",       # leading slash => absolute, escapes /workspace
        ".hidden",         # leading dot => dotfile, also too close to ..
        "..",              # the classic
    ],
)
async def test_exec_python_rejects_unsafe_working_dir(bad: str) -> None:
    """Defensive: controllers supply working_dir; reject anything that could
    escape /workspace/ or surprise the shell."""
    fake = _FakeSandboxAPI(_FakePodExecResult(stdout="", stderr="", exit_code=0, duration_s=0.0))
    executor = BFTSExecutor(sandbox_api=fake)

    with pytest.raises(ValueError):
        await executor.exec_python(
            sandbox_id="sbx-x",
            code="pass",
            timeout_s=10.0,
            working_dir=bad,
        )
    # No write or exec should have happened on a rejected call.
    assert fake.writes == []
    assert fake.commands == []
