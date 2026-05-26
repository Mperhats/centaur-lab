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
    # Back-compat: no working_dir kwarg => list_dir reads the legacy
    # /workspace/working/ path.
    assert api.list_calls == [("sbx-c", "/workspace/working")]


@pytest.mark.asyncio
async def test_collect_artifacts_per_node_working_dir(tmp_path: Path) -> None:
    """working_dir routes collection to /workspace/<working_dir>/.

    Phase 4h prerequisite (`docs/superpowers/plans/2026-05-26-bfts-phase4.md`
    §Task 4h.1): per-node artifacts live under per-node subdirectories so
    two parallel expansions don't slurp each other's plots.
    """
    files = {
        "/workspace/node_a/experiment_data.npy": b"A_NPY",
        "/workspace/node_a/loss.png": b"A_PNG",
        "/workspace/node_b/experiment_data.npy": b"B_NPY",
        "/workspace/node_b/loss.png": b"B_PNG",
    }
    api = _FakeSandboxAPI(files)
    executor = BFTSExecutor(sandbox_api=api)

    collected_a = await executor.collect_artifacts(
        sandbox_id="sbx",
        dest_dir=tmp_path / "a",
        node_id="node-a",
        working_dir="node_a",
    )
    collected_b = await executor.collect_artifacts(
        sandbox_id="sbx",
        dest_dir=tmp_path / "b",
        node_id="node-b",
        working_dir="node_b",
    )

    # Each collection only pulls files from its own per-node workspace.
    assert (tmp_path / "a" / "experiment_node-a" / "experiment_data.npy").read_bytes() == b"A_NPY"
    assert (tmp_path / "a" / "experiment_node-a" / "loss.png").read_bytes() == b"A_PNG"
    assert (tmp_path / "b" / "experiment_node-b" / "experiment_data.npy").read_bytes() == b"B_NPY"
    assert (tmp_path / "b" / "experiment_node-b" / "loss.png").read_bytes() == b"B_PNG"

    # And neither node's directory ever sees the other's files.
    assert not (tmp_path / "a" / "experiment_node-a" / "B_NPY").exists()
    assert not (tmp_path / "b" / "experiment_node-b" / "A_NPY").exists()

    assert sorted(collected_a) == ["experiment_data.npy", "loss.png"]
    assert sorted(collected_b) == ["experiment_data.npy", "loss.png"]

    # list_dir was scoped per call — disjoint paths => no cross-pollination.
    assert api.list_calls == [
        ("sbx", "/workspace/node_a"),
        ("sbx", "/workspace/node_b"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "",
        "../escape",
        "foo/bar",
        "/abs/path",
        ".hidden",
        "..",
    ],
)
async def test_collect_artifacts_rejects_unsafe_working_dir(
    tmp_path: Path, bad: str
) -> None:
    api = _FakeSandboxAPI({})
    executor = BFTSExecutor(sandbox_api=api)
    with pytest.raises(ValueError):
        await executor.collect_artifacts(
            sandbox_id="sbx",
            dest_dir=tmp_path,
            node_id="node-x",
            working_dir=bad,
        )
    # Validation happens before any I/O.
    assert api.list_calls == []
    assert api.read_calls == []
