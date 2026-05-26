"""Test: write_file preserves single quotes verbatim through the heredoc."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from tools.bfts_executor.client import _KubernetesSandboxAPI


@dataclass
class _ExecCapture:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_s: float = 0.0


@pytest.mark.asyncio
async def test_write_file_does_not_escape_single_quotes() -> None:
    """Reproduces the C1 regression: ``print('don't')`` must roundtrip.

    The quoted heredoc is literal-mode (POSIX), so write_file must NOT
    pre-escape single quotes — that escape pattern is only valid inside
    an outer single-quoted shell string.
    """
    captured_commands: list[str] = []

    async def _capture(sandbox_id: str, command: str, *, timeout_s: float):
        captured_commands.append(command)
        return _ExecCapture()

    api = _KubernetesSandboxAPI(namespace="centaur-system")
    api.run_command = _capture  # type: ignore[method-assign]

    await api.write_file("sbx-1", "/workspace/working/runfile.py", "print('don\\'t')\n")

    assert len(captured_commands) == 1
    cmd = captured_commands[0]
    # The heredoc body must contain the literal source, not the
    # outer-single-quote escape sequence "'\\''".
    assert "print('don\\'t')" in cmd
    assert "'\\''" not in cmd
