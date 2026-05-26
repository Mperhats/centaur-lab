"""BFTS executor: drive a Sandbox CR through the experiment-exec contract.

Reproduces the Interpreter.run(code, reset_session=True) -> ExecutionResult
contract from .scientist/ai_scientist/treesearch/interpreter.py:81-313
(research 02 §Code execution contract) over the agent-sandbox Sandbox
CR. The hard timeout is enforced inside the sandbox by ``timeout(1)``
(SIGTERM by default; we use ``-s INT`` + ``-k 60`` for SIGINT then SIGKILL
at T+60, matching Sakana's interpreter.py:283-289).

Construction:
- Production: ``BFTSExecutor()`` uses :class:`_KubernetesSandboxAPI` which
  drives ``agents.x-k8s.io/v1alpha1 Sandbox`` via ``kubernetes_asyncio``.
- Tests:      ``BFTSExecutor(sandbox_api=<fake>)`` lets us assert the
  wire shape without spinning up Kubernetes. The protocol is just two
  methods: ``write_file``, ``run_command``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from models import ExecutionResult

WORKING_DIR = "/workspace/working"
"""Per-node writable workspace under the inline workspace PVC.

Matches Sakana's ``os.path.join(os.getcwd(), 'working')`` contract: the
bfts-executor image sets ``WORKDIR /workspace`` so ``os.getcwd()`` is
``/workspace`` and generated experiment code writes its
``experiment_data.npy`` and ``*.png`` into ``working/``. Spec correction
#12 — we own the image and the path; the inline ``volumeClaimTemplates``
(Task 1.6) attaches a per-Sandbox PVC at this mount point.
"""

RUNFILE_NAME = "runfile.py"
"""Filename shown in tracebacks. Sakana uses the same name
(.scientist/ai_scientist/treesearch/interpreter.py:139-140)."""


class _PodExecResult(Protocol):
    stdout: str
    stderr: str
    exit_code: int
    duration_s: float


class _SandboxAPI(Protocol):
    async def write_file(self, sandbox_id: str, path: str, content: str) -> None: ...

    async def run_command(
        self, sandbox_id: str, command: str, *, timeout_s: float
    ) -> _PodExecResult: ...

    async def list_dir(self, sandbox_id: str, path: str) -> list[str]: ...

    async def read_file_bytes(self, sandbox_id: str, path: str) -> bytes: ...


class BFTSExecutor:
    """Run code inside a Sandbox CR with Sakana-shape outputs."""

    def __init__(self, sandbox_api: _SandboxAPI | None = None) -> None:
        # In production we lazy-load _KubernetesSandboxAPI to keep this
        # module importable in tests without a kube_config. Task 1.6 wires
        # the real implementation.
        self._api = sandbox_api

    def _require_api(self) -> _SandboxAPI:
        if self._api is None:
            raise RuntimeError(
                "BFTSExecutor was constructed without a sandbox_api; the "
                "real Kubernetes-backed API lands in Task 1.6."
            )
        return self._api

    async def exec_python(
        self,
        sandbox_id: str,
        code: str,
        timeout_s: float,
    ) -> ExecutionResult:
        api = self._require_api()
        runfile_path = f"{WORKING_DIR}/{RUNFILE_NAME}"

        # 1. Write the code to the sandbox PVC.
        await api.write_file(sandbox_id, runfile_path, code)

        # 2. Run it with chdir to working/ (matches Sakana's
        #    interpreter.py:120 + 138 chdir-twice defense). ``timeout`` is
        #    coreutils ``timeout(1)``; ``-s INT`` sends SIGINT at T,
        #    ``-k 60`` SIGKILL at T+60 (research 02 §Code execution
        #    contract).
        command = (
            f"mkdir -p {WORKING_DIR} && cd {WORKING_DIR} && "
            f"timeout --signal=INT --kill-after=60 {int(timeout_s)} "
            f"python -u {runfile_path}"
        )
        exec_result = await api.run_command(
            sandbox_id, command, timeout_s=timeout_s
        )

        # 3. Wrap into ExecutionResult. Non-zero exit => is_buggy upstream.
        term_out: list[str] = []
        if exec_result.stdout:
            term_out.append(exec_result.stdout)
        if exec_result.stderr:
            term_out.append(exec_result.stderr)

        if exec_result.exit_code == 0:
            exc_type: str | None = None
            exc_info: dict[str, Any] | None = None
        elif exec_result.exit_code == 124:
            # coreutils timeout(1) returns 124 on timeout.
            exc_type = "TimeoutError"
            exc_info = {"exit_code": 124, "timeout_s": timeout_s}
        else:
            exc_type = "SubprocessError"
            exc_info = {"exit_code": exec_result.exit_code}

        return ExecutionResult(
            term_out=term_out,
            exec_time=exec_result.duration_s,
            exc_type=exc_type,
            exc_info=exc_info,
            exc_stack=None,
        )

    async def collect_artifacts(
        self,
        sandbox_id: str,
        dest_dir: "Path",
        node_id: str,
    ) -> list[str]:
        """Copy working/*.npy + working/*.png out of the sandbox to dest_dir.

        Returns the list of collected basenames (sorted). Mirrors Sakana's
        per-node artifact directory layout (research 02 §Workspace layout):
        ``logs/<exp>/experiment_results/experiment_<node_id>_proc_<pid>/``.

        We drop the ``_proc_<pid>`` suffix because in Centaur the PID is
        meaningless (the workflow is the durable identity).
        """
        api = self._require_api()
        entries = await api.list_dir(sandbox_id, WORKING_DIR)
        keep = [e for e in entries if e.endswith(".npy") or e.endswith(".png")]
        node_dir = dest_dir / f"experiment_{node_id}"
        node_dir.mkdir(parents=True, exist_ok=True)
        collected: list[str] = []
        for full in keep:
            basename = full.rsplit("/", 1)[-1]
            content = await api.read_file_bytes(sandbox_id, full)
            (node_dir / basename).write_bytes(content)
            collected.append(basename)
        return sorted(collected)
