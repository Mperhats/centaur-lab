"""Test: collect_artifacts moves .npy + .png out of the Sandbox PVC."""
from __future__ import annotations

from pathlib import Path

import pytest

from bfts_executor.client import BFTSExecutor


class _FakeSandboxAPI:
    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files
        self.list_calls: list[tuple[str, str]] = []
        self.read_calls: list[tuple[str, str]] = []

    async def list_dir(self, sandbox_id: str, path: str) -> list[str]:
        self.list_calls.append((sandbox_id, path))
        return [name for name in self._files if name.startswith(path)]

    async def read_file_bytes(self, sandbox_id: str, path: str) -> bytes:
        self.read_calls.append((sandbox_id, path))
        return self._files[path]

    async def write_file(self, sandbox_id: str, path: str, content: str) -> None: ...
    async def run_command(self, sandbox_id, command, *, timeout_s): ...


@pytest.mark.asyncio
async def test_collect_artifacts_picks_npy_and_png(tmp_path: Path) -> None:
    files = {
        "/workspace/working/experiment_data.npy": b"\x93NUMPY...",
        "/workspace/working/loss_curve.png": b"\x89PNG...",
        "/workspace/working/accuracy.png": b"\x89PNG_2...",
        "/workspace/working/notes.txt": b"ignore me",  # not collected
    }
    api = _FakeSandboxAPI(files)
    executor = BFTSExecutor(sandbox_api=api)
    collected = await executor.collect_artifacts(
        sandbox_id="sbx-c",
        dest_dir=tmp_path,
        node_id="node-abc",
    )
    # All collected files land under tmp_path/experiment_<node_id>/.
    assert (tmp_path / "experiment_node-abc" / "experiment_data.npy").read_bytes().startswith(b"\x93NUMPY")
    assert (tmp_path / "experiment_node-abc" / "loss_curve.png").read_bytes().startswith(b"\x89PNG")
    assert (tmp_path / "experiment_node-abc" / "accuracy.png").read_bytes().startswith(b"\x89PNG_2")
    # notes.txt is NOT collected — only .npy + .png.
    assert not (tmp_path / "experiment_node-abc" / "notes.txt").exists()
    # Returned list of relative names (for the workflow to record on the node).
    assert sorted(collected) == [
        "accuracy.png",
        "experiment_data.npy",
        "loss_curve.png",
    ]
