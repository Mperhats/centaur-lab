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

import os
import time
from dataclasses import dataclass
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

# Constants for the BFTS Sandbox CRD body. Pinned to v1alpha1 to match the
# upstream constant at .centaur/services/api/api/sandbox/
# kubernetes_agent_sandbox.py:15-17. When upstream graduates v1beta1 (see
# the deferred section of the plan) bump both in lockstep.
_AGENT_SANDBOX_GROUP = "agents.x-k8s.io"
_AGENT_SANDBOX_VERSION = "v1alpha1"
_AGENT_SANDBOX_PLURAL = "sandboxes"
_DEFAULT_EXECUTOR_IMAGE = "bfts-executor:latest"
_DEFAULT_STORAGE_SIZE = "10Gi"
_WORKSPACE_MOUNT_PATH = "/workspace"
_WORKSPACE_VOLUME_NAME = "workspace"


def _parse_ws_frame(data: bytes | str) -> tuple[int, str]:
    # Lifted verbatim from .centaur/services/api/api/sandbox/kubernetes.py:393-396.
    if isinstance(data, bytes):
        return data[0], data[1:].decode("utf-8", errors="replace")
    return ord(data[0]), data[1:]


def _is_not_found(exc: BaseException) -> bool:
    # Mirrors KubernetesExecutorBackend._is_not_found at kubernetes.py:490-492.
    return getattr(exc, "status", None) == 404


class _PodExecResult(Protocol):
    stdout: str
    stderr: str
    exit_code: int
    duration_s: float


@dataclass
class _RealPodExecResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_s: float


class _SandboxAPI(Protocol):
    async def create_sandbox(
        self,
        sandbox_id: str,
        *,
        run_id: str,
        image: str = ...,
        storage_size: str = ...,
        storage_class: str | None = ...,
    ) -> None: ...

    async def pause_sandbox(self, sandbox_id: str) -> None: ...

    async def resume_sandbox(self, sandbox_id: str) -> None: ...

    async def stop_sandbox(self, sandbox_id: str) -> None: ...

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

    async def create_sandbox(
        self,
        sandbox_id: str,
        *,
        run_id: str,
        image: str = "bfts-executor:latest",
        storage_size: str = "10Gi",
        storage_class: str | None = None,
    ) -> str:
        api = self._require_api()
        await api.create_sandbox(
            sandbox_id,
            run_id=run_id,
            image=image,
            storage_size=storage_size,
            storage_class=storage_class,
        )
        # Block until the pod is Ready so the workflow can immediately exec
        # without an extra wait step.
        await api._wait_pod_ready(sandbox_id, timeout_s=180.0)  # type: ignore[attr-defined]
        return sandbox_id

    async def pause_sandbox(self, sandbox_id: str) -> None:
        await self._require_api().pause_sandbox(sandbox_id)

    async def resume_sandbox(self, sandbox_id: str) -> None:
        await self._require_api().resume_sandbox(sandbox_id)

    async def stop_sandbox(self, sandbox_id: str) -> None:
        await self._require_api().stop_sandbox(sandbox_id)

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


class _KubernetesSandboxAPI:
    """Drive an `agents.x-k8s.io/v1alpha1` Sandbox CR end-to-end.

    Owns CRD lifecycle (create / pause / resume / stop) AND pod exec; the
    BFTS workflow's sandbox identity is independent of Centaur's
    sandbox_sessions table — see Spec correction #11 (we do NOT call
    `ctx.agent_turn` to provision sandboxes; that path drags in the
    spawn → message → execute loop from `do_agent_turn` at
    .centaur/services/api/api/workflow_engine.py:1124).

    Constructor accepts pre-built API clients (for tests) or lazily loads
    them at first use (production). Lazy init pattern mirrors
    KubernetesExecutorBackend._ensure_clients at kubernetes.py:424-463.
    """

    def __init__(
        self,
        *,
        core_api: Any | None = None,
        custom_api: Any | None = None,
        networking_api: Any | None = None,
        ws_core_api: Any | None = None,
        ws_api_client: Any | None = None,
        namespace: str | None = None,
    ) -> None:
        self.core_api = core_api
        self.custom_api = custom_api
        self.networking_api = networking_api
        self.ws_core_api = ws_core_api
        self.ws_api_client = ws_api_client
        self.namespace = namespace or os.getenv("KUBERNETES_NAMESPACE", "centaur-system")

    async def _ensure_clients(self) -> None:
        if (
            self.core_api is not None
            and self.custom_api is not None
            and self.networking_api is not None
            and self.ws_core_api is not None
            and self.ws_api_client is not None
        ):
            return
        # Lazy import so the module is testable without a kube_config.
        from kubernetes_asyncio import client, config
        from kubernetes_asyncio.config.config_exception import ConfigException
        from kubernetes_asyncio.stream import WsApiClient

        try:
            config.load_incluster_config()
        except ConfigException:
            await config.load_kube_config()
        core_api_client = client.ApiClient(
            configuration=client.Configuration.get_default_copy()
        )
        if self.core_api is None:
            self.core_api = client.CoreV1Api(api_client=core_api_client)
        if self.custom_api is None:
            self.custom_api = client.CustomObjectsApi(api_client=core_api_client)
        if self.networking_api is None:
            self.networking_api = client.NetworkingV1Api(api_client=core_api_client)
        if self.ws_api_client is None:
            self.ws_api_client = WsApiClient(
                configuration=client.Configuration.get_default_copy(),
                heartbeat=30,
            )
        if self.ws_core_api is None:
            self.ws_core_api = client.CoreV1Api(api_client=self.ws_api_client)

    # ----- CRD lifecycle (mirrors kubernetes_agent_sandbox.py:109-217) -----

    async def create_sandbox(
        self,
        sandbox_id: str,
        *,
        run_id: str,
        image: str = _DEFAULT_EXECUTOR_IMAGE,
        storage_size: str = _DEFAULT_STORAGE_SIZE,
        storage_class: str | None = None,
    ) -> None:
        """Create a BFTS Sandbox CRD with inline volumeClaimTemplates.

        Body shape mirrors KubernetesAgentSandboxBackend._create_workload
        at .centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:
        109-154 — same spec.replicas/service/shutdownPolicy defaults, same
        volumeClaimTemplates layout. Differences:
          * labels select on `centaur.ai/bfts-sandbox`, NOT
            `centaur.ai/managed`, so the chart's -sandbox NetworkPolicy
            (.centaur/contrib/chart/templates/networkpolicy.yaml:307-327)
            does not lock our egress to api:8000 only.
          * volumeClaimTemplates is set unconditionally (we do not read the
            global KUBERNETES_SANDBOX_STATE_VOLUME_ENABLED env var; Spec
            correction #12).
          * podTemplate.spec.containers uses the overlay-owned
            bfts-executor image and a `sleep infinity` CMD — no harness.
        """
        await self._ensure_clients()
        labels = {
            "centaur.ai/bfts-sandbox": "true",
            "centaur.ai/bfts-run": run_id,
        }
        pvc_spec: dict[str, Any] = {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": storage_size}},
        }
        if storage_class:
            pvc_spec["storageClassName"] = storage_class
        body: dict[str, Any] = {
            "apiVersion": f"{_AGENT_SANDBOX_GROUP}/{_AGENT_SANDBOX_VERSION}",
            "kind": "Sandbox",
            "metadata": {"name": sandbox_id, "labels": labels},
            "spec": {
                "replicas": 1,
                "service": False,
                "shutdownPolicy": "Retain",
                "volumeClaimTemplates": [
                    {"metadata": {"name": _WORKSPACE_VOLUME_NAME}, "spec": pvc_spec},
                ],
                "podTemplate": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "containers": [
                            {
                                "name": "sandbox",
                                "image": image,
                                "imagePullPolicy": "IfNotPresent",
                                "command": ["sleep", "infinity"],
                                "workingDir": _WORKSPACE_MOUNT_PATH,
                                "volumeMounts": [
                                    {
                                        "name": _WORKSPACE_VOLUME_NAME,
                                        "mountPath": _WORKSPACE_MOUNT_PATH,
                                    },
                                ],
                            }
                        ],
                    },
                },
            },
        }
        await self.custom_api.create_namespaced_custom_object(
            _AGENT_SANDBOX_GROUP,
            _AGENT_SANDBOX_VERSION,
            self.namespace,
            _AGENT_SANDBOX_PLURAL,
            body,
        )

    async def pause_sandbox(self, sandbox_id: str) -> None:
        """Patch the Sandbox to replicas=0; the controller deletes the pod.

        Mirrors kubernetes_agent_sandbox.py:159-172.
        """
        await self._ensure_clients()
        await self.custom_api.patch_namespaced_custom_object(
            _AGENT_SANDBOX_GROUP,
            _AGENT_SANDBOX_VERSION,
            self.namespace,
            _AGENT_SANDBOX_PLURAL,
            sandbox_id,
            {"spec": {"replicas": 0}},
            _content_type="application/merge-patch+json",
        )

    async def resume_sandbox(
        self, sandbox_id: str, *, ready_timeout_s: float = 120.0
    ) -> None:
        """Patch the Sandbox to replicas=1, then wait for the pod to be Ready.

        Mirrors kubernetes_agent_sandbox.py:174-185.
        """
        await self._ensure_clients()
        await self.custom_api.patch_namespaced_custom_object(
            _AGENT_SANDBOX_GROUP,
            _AGENT_SANDBOX_VERSION,
            self.namespace,
            _AGENT_SANDBOX_PLURAL,
            sandbox_id,
            {"spec": {"replicas": 1}},
            _content_type="application/merge-patch+json",
        )
        await self._wait_pod_ready(sandbox_id, timeout_s=ready_timeout_s)

    async def stop_sandbox(self, sandbox_id: str) -> None:
        """Delete the Sandbox CRD; PVC follows via owner refs.

        Mirrors kubernetes_agent_sandbox.py:74-85 + 212-217. 404 is OK
        (idempotent stop).
        """
        await self._ensure_clients()
        try:
            await self.custom_api.delete_namespaced_custom_object(
                _AGENT_SANDBOX_GROUP,
                _AGENT_SANDBOX_VERSION,
                self.namespace,
                _AGENT_SANDBOX_PLURAL,
                sandbox_id,
            )
        except Exception as exc:
            if not _is_not_found(exc):
                raise

    async def _wait_pod_ready(self, sandbox_id: str, *, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                pod = await self.core_api.read_namespaced_pod(
                    sandbox_id, self.namespace
                )
            except Exception as exc:
                if _is_not_found(exc):
                    await _sleep(0.5)
                    continue
                raise
            phase = (getattr(getattr(pod, "status", None), "phase", "") or "").lower()
            if phase == "running":
                return
            await _sleep(0.5)
        raise TimeoutError(
            f"sandbox readiness timed out after {timeout_s:.0f}s: {sandbox_id}"
        )

    # ----- pod exec via WsApiClient (mirrors kubernetes.py:1503-1551) -----

    async def run_command(
        self,
        sandbox_id: str,
        command: str,
        *,
        timeout_s: float,
    ) -> _RealPodExecResult:
        await self._ensure_clients()
        from aiohttp import WSMsgType
        from kubernetes_asyncio.stream.ws_client import (
            ERROR_CHANNEL,
            STDERR_CHANNEL,
            STDOUT_CHANNEL,
        )

        start = time.perf_counter()
        websocket_ctx = await self.ws_core_api.connect_get_namespaced_pod_exec(
            sandbox_id,
            self.namespace,
            command=["/bin/sh", "-c", command],
            container="sandbox",
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        error_data = ""
        async with websocket_ctx as websocket:
            while True:
                msg = await websocket.receive()
                if msg.type in {WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED}:
                    break
                if msg.type not in {WSMsgType.BINARY, WSMsgType.TEXT}:
                    continue
                channel, payload = _parse_ws_frame(msg.data)
                if channel == STDOUT_CHANNEL:
                    stdout_parts.append(payload)
                elif channel == STDERR_CHANNEL:
                    stderr_parts.append(payload)
                elif channel == ERROR_CHANNEL:
                    error_data += payload
        exit_code = (
            self.ws_api_client.parse_error_data(error_data) if error_data else 0
        )
        duration = time.perf_counter() - start
        return _RealPodExecResult(
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            exit_code=exit_code,
            duration_s=duration,
        )

    async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
        # Heredoc-stream the file via /bin/sh. UTF-8 text only (BFTS code).
        encoded = content.replace("'", "'\\''")
        cmd = (
            f"mkdir -p $(dirname '{path}') && cat > '{path}' << '__BFTS_EOF__'\n"
            f"{encoded}\n__BFTS_EOF__"
        )
        await self.run_command(sandbox_id, cmd, timeout_s=30.0)

    async def list_dir(self, sandbox_id: str, path: str) -> list[str]:
        result = await self.run_command(
            sandbox_id, f"ls -1 '{path}' 2>/dev/null || true", timeout_s=10.0
        )
        return [f"{path}/{n}" for n in result.stdout.splitlines() if n.strip()]

    async def read_file_bytes(self, sandbox_id: str, path: str) -> bytes:
        import base64

        result = await self.run_command(
            sandbox_id, f"base64 -w0 '{path}'", timeout_s=60.0
        )
        return base64.b64decode(result.stdout.strip())


async def _sleep(seconds: float) -> None:
    # Local helper so tests can monkeypatch sleep if they want to.
    import asyncio

    await asyncio.sleep(seconds)
