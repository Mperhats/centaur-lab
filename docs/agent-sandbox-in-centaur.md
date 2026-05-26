# `kubernetes-sigs/agent-sandbox` in Centaur — Integration Reference

This document maps how the [Agent Sandbox](https://github.com/kubernetes-sigs/agent-sandbox)
CRD/controller is wired into the `paradigmxyz/centaur` submodule pinned at
`.centaur/`. It complements [`centaur-science.md`](centaur-science.md), which
discusses how we *want* to use Sandbox primitives for the BFTS port; this
doc describes what is **actually in the code today**.

## TL;DR

- Vendored as a **Helm subchart** at `.centaur/contrib/chart/charts/agent-sandbox/`, pinning upstream **v0.4.6**.
- Used by an **opt-in** Python sandbox backend (`KubernetesAgentSandboxBackend`) that creates `Sandbox` CRs instead of bare Pods.
- Enables **pause/resume with persistent state** for an agent runtime — a sandbox can scale to `replicas: 0` between turns without losing workspace, `.codex`, `.claude`, branches, or uploads.
- **Defaults to OFF.** Two independent flags (`agentSandbox.enabled` for installing the controller, `sandbox.controller=agent-sandbox` for using it) must both be flipped.
- The original bare-Pod backend (`KubernetesExecutorBackend`) is still the default and is untouched by this integration.

## Provenance

| Field | Value |
|---|---|
| Upstream | https://github.com/kubernetes-sigs/agent-sandbox |
| Pinned upstream version | `v0.4.6` (controller image and bundled CRDs) |
| Introduced by | [paradigmxyz/centaur#162](https://github.com/paradigmxyz/centaur/pull/162) — "Support pausable agent sandboxes" |
| Merge commit | `5821abb8e6de18f22c39dc2eb702998cc6fc8e55` (2026-05-25 10:11 UTC) |
| Author | `Zygimantass` (zhygis) |
| Scope | +9,640 / −42 across 33 files in one commit |

Our `.centaur` submodule is currently pinned at `6a96324c` (origin/main HEAD), which is three commits ahead of `5821abb8`, so this code is present in the pinned SHA.

## What the integration consists of

Four layers, top to bottom:

1. **Vendored Helm subchart** — installs the upstream controller + CRDs.
2. **Parent Helm chart wiring** — declares the subchart dependency, surfaces toggles, grants RBAC, injects env vars.
3. **Python backend** — talks to the `Sandbox` CRD via `kubernetes_asyncio`'s `CustomObjectsApi`.
4. **Runtime registry** — picks bare-Pod or Agent-Sandbox at API startup based on env.

### 1. Vendored Helm subchart

The upstream chart is checked into the repo (not pulled from a Helm repo) at `.centaur/contrib/chart/charts/agent-sandbox/`:

- `Chart.yaml` — name/version metadata for the vendored copy.
- `crds/agents.x-k8s.io_sandboxes.yaml` — the `Sandbox` CRD (core).
- `crds/extensions.agents.x-k8s.io_sandboxclaims.yaml` — `SandboxClaim` (extension).
- `crds/extensions.agents.x-k8s.io_sandboxtemplates.yaml` — `SandboxTemplate` (extension).
- `crds/extensions.agents.x-k8s.io_sandboxwarmpools.yaml` — `SandboxWarmPool` (extension).
- `templates/deployment.yaml`, `service.yaml`, `serviceaccount.yaml`, `namespace.yaml`, `clusterrolebinding*.yaml`, `rbac.generated.yaml` — the controller workload + its RBAC.

```1:15:.centaur/contrib/chart/charts/agent-sandbox/Chart.yaml
apiVersion: v2
name: agent-sandbox
description: Kubernetes controller for managing agent sandboxes
type: application
version: 0.1.0
keywords:
  - agent
  - sandbox
  - kubernetes
home: https://github.com/kubernetes-sigs/agent-sandbox
sources:
  - https://github.com/kubernetes-sigs/agent-sandbox
maintainers:
  - name: Kubernetes SIGs
    url: https://github.com/kubernetes-sigs
```

The extensions controllers (Claim/Template/WarmPool) ship in the same chart but are gated behind `controller.extensions=true` — off by default. Today, only the core `Sandbox` CRD is exercised by Centaur's Python backend.

### 2. Parent chart wiring

The Centaur chart pulls in the vendored subchart as an optional dependency:

```13:17:.centaur/contrib/chart/Chart.yaml
  - name: agent-sandbox
    version: 0.1.0
    repository: file://charts/agent-sandbox
    alias: agentSandbox
    condition: agentSandbox.enabled
```

The toggles live in the parent `values.yaml`:

```139:149:.centaur/contrib/chart/values.yaml
agentSandbox:
  enabled: false
  namespace:
    create: true
    name: agent-sandbox-system
  image:
    repository: registry.k8s.io/agent-sandbox/agent-sandbox-controller
    tag: v0.4.6
    pullPolicy: IfNotPresent
  controller:
    extensions: false
```

```100:101:.centaur/contrib/chart/values.yaml
sandbox:
  controller: pod
```

The API service account is granted permissions on the `Sandbox` CRD so the Python backend can create/patch/delete custom objects:

```42:44:.centaur/contrib/chart/templates/rbac.yaml
  - apiGroups: ["agents.x-k8s.io"]
    resources: ["sandboxes"]
    verbs: ["create", "delete", "get", "list", "patch", "update", "watch"]
```

And the chart pipes the controller selection into the API container via env:

```308:309:.centaur/contrib/chart/templates/workloads.yaml
            - name: KUBERNETES_SANDBOX_CONTROLLER
              value: {{ .Values.sandbox.controller | quote }}
```

Alongside the state-volume knobs the new backend reads (`KUBERNETES_SANDBOX_STATE_VOLUME_ENABLED` / `_SIZE` / `_STORAGE_CLASS`, same file, lines 310–316).

### 3. Python backend

The new backend file is the only Python addition; it subclasses the existing `KubernetesExecutorBackend` and overrides the workload-shape methods so the same lifecycle code path now produces a `Sandbox` CR instead of a bare Pod.

```15:17:.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py
_AGENT_SANDBOX_GROUP = "agents.x-k8s.io"
_AGENT_SANDBOX_VERSION = "v1alpha1"
_AGENT_SANDBOX_PLURAL = "sandboxes"
```

The CRD client is `kubernetes_asyncio.client.CustomObjectsApi`:

```37:52:.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py
class KubernetesAgentSandboxBackend(KubernetesExecutorBackend):
    """Runs agent sandboxes through the Agent Sandbox controller."""

    def __init__(self) -> None:
        super().__init__()
        self._custom: client.CustomObjectsApi | None = None

    async def _ensure_clients(self) -> None:
        await super()._ensure_clients()
        if self._custom is None:
            self._custom = client.CustomObjectsApi(api_client=self._core_api().api_client)

    def _custom_api(self) -> client.CustomObjectsApi:
        if self._custom is None:
            raise RuntimeError("kubernetes custom objects client not initialized")
        return self._custom
```

Creating a sandbox composes a `Sandbox` CR around the pod spec the parent class would have submitted directly, optionally attaching a `volumeClaimTemplate` for persistent state:

```109:154:.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py
    async def _create_workload(self, pod_spec: dict[str, Any]) -> None:
        sandbox_id = pod_spec["metadata"]["name"]
        spec: dict[str, Any] = {
            "replicas": 1,
            "service": False,
            "shutdownPolicy": "Retain",
            "podTemplate": {
                "metadata": {
                    "labels": pod_spec["metadata"].get("labels", {}),
                    "annotations": pod_spec["metadata"].get("annotations", {}),
                },
                "spec": pod_spec["spec"],
            },
        }
        if _state_volume_enabled():
            pvc_spec: dict[str, Any] = {
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": _state_volume_size()}},
            }
            storage_class = _state_volume_storage_class_name()
            if storage_class:
                pvc_spec["storageClassName"] = storage_class
            spec["volumeClaimTemplates"] = [
                {
                    "metadata": {"name": "state"},
                    "spec": pvc_spec,
                }
            ]

        body: dict[str, Any] = {
            "apiVersion": f"{_AGENT_SANDBOX_GROUP}/{_AGENT_SANDBOX_VERSION}",
            "kind": "Sandbox",
            "metadata": {
                "name": sandbox_id,
                "labels": pod_spec["metadata"].get("labels", {}),
                "annotations": pod_spec["metadata"].get("annotations", {}),
            },
            "spec": spec,
        }
        await self._custom_api().create_namespaced_custom_object(
            _AGENT_SANDBOX_GROUP,
            _AGENT_SANDBOX_VERSION,
            _namespace(),
            _AGENT_SANDBOX_PLURAL,
            body,
        )
```

Pause and resume are implemented as **merge-patches on `spec.replicas`** — `0` to hibernate, `1` to wake — leaving the state PVC untouched:

```159:185:.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py
    async def pause_by_id(self, sandbox_id: str) -> None:
        await self._ensure_clients()
        await self.close_streams(
            SandboxSession(sandbox_id=sandbox_id, thread_key="", harness="", engine="")
        )
        await self._custom_api().patch_namespaced_custom_object(
            _AGENT_SANDBOX_GROUP,
            _AGENT_SANDBOX_VERSION,
            _namespace(),
            _AGENT_SANDBOX_PLURAL,
            sandbox_id,
            {"spec": {"replicas": 0}},
            _content_type="application/merge-patch+json",
        )

    async def resume_by_id(self, sandbox_id: str) -> None:
        await self._ensure_clients()
        await self._custom_api().patch_namespaced_custom_object(
            _AGENT_SANDBOX_GROUP,
            _AGENT_SANDBOX_VERSION,
            _namespace(),
            _AGENT_SANDBOX_PLURAL,
            sandbox_id,
            {"spec": {"replicas": 1}},
            _content_type="application/merge-patch+json",
        )
        await self._wait_ready(sandbox_id)
```

`status_by_id` derives the surface state from `spec.replicas` plus the underlying Pod phase: `running` / `created` / `suspended` / `stopped` / `gone`. `stop_by_id` is the only operation that destroys state — it deletes the `Sandbox`, the state PVC, and Centaur's per-sandbox prompt Secret and proxy resources.

### 4. Runtime registry

Backend selection is a single env-driven branch, evaluated once on first call:

```24:36:.centaur/services/api/api/sandbox/registry.py
def auto_configure() -> SandboxBackend:
    """Configure the Kubernetes sandbox backend."""
    import os

    controller = (os.getenv("KUBERNETES_SANDBOX_CONTROLLER") or "pod").strip().lower()
    if controller in {"agent-sandbox", "agentsandbox"}:
        from api.sandbox.kubernetes_agent_sandbox import KubernetesAgentSandboxBackend

        return KubernetesAgentSandboxBackend()

    from api.sandbox.kubernetes import KubernetesExecutorBackend

    return KubernetesExecutorBackend()
```

This means the choice is per-API-pod and immutable for that pod's lifetime — flipping `sandbox.controller` requires a redeploy, not a runtime patch.

## File map

Quick index of every file touched by the integration, grouped by layer:

| Layer | Path | Role |
|---|---|---|
| Subchart | `.centaur/contrib/chart/charts/agent-sandbox/` | Vendored upstream Helm chart (controller + CRDs + RBAC) |
| Parent chart | `.centaur/contrib/chart/Chart.yaml` | Declares `agent-sandbox` as a `file://` subchart dependency, alias `agentSandbox` |
| Parent chart | `.centaur/contrib/chart/values.yaml` | `agentSandbox.*` block; `sandbox.controller` default; state-volume knobs |
| Parent chart | `.centaur/contrib/chart/values.dev.yaml` | Dev overrides (unchanged defaults for these toggles) |
| Parent chart | `.centaur/contrib/chart/values.schema.json` | Schema entries for `agentSandbox` and `sandbox.stateVolume` |
| Parent chart | `.centaur/contrib/chart/templates/rbac.yaml` | Grants the API SA verbs on `sandboxes.agents.x-k8s.io` |
| Parent chart | `.centaur/contrib/chart/templates/workloads.yaml` | Injects `KUBERNETES_SANDBOX_CONTROLLER` + state-volume env into the API pod |
| Parent chart | `.centaur/contrib/chart/templates/NOTES.txt` | Operator hint about toggling `agentSandbox.enabled` |
| Backend | `.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py` | The `KubernetesAgentSandboxBackend` (~217 lines) |
| Backend | `.centaur/services/api/api/sandbox/kubernetes.py` | Refactored to be subclassable (extracted hooks the new backend overrides) |
| Backend | `.centaur/services/api/api/sandbox/base.py` | Adds `pause_by_id` / `resume_by_id` to the abstract interface |
| Backend | `.centaur/services/api/api/sandbox/registry.py` | Env-driven backend selection |
| Backend | `.centaur/services/api/api/agent.py` | Calls `pause_by_id` / `resume_by_id` on the agent lifecycle path |
| Sandbox image | `.centaur/services/sandbox/entrypoint.sh` | Persists workspace / uploads / branches / `.codex` / `.claude` under `/home/agent/state` when a state volume is mounted |
| Tests | `.centaur/services/api/tests/test_sandbox_kubernetes_backend.py` | Unit tests for the new backend's CR-shape, pause/resume, and cleanup paths |

## How to enable it for our deployment

`values.local.yaml` does **not** currently override either flag, so on `just up` you get the bare-Pod backend. To turn the integration on for experimentation, add:

```yaml
agentSandbox:
  enabled: true
sandbox:
  controller: agent-sandbox
  stateVolume:
    enabled: true
    size: 1Gi
    storageClassName: ""   # use the cluster default
```

Optional, for a tight local feedback loop on the idle/cleanup timers:

```yaml
api:
  extraEnv:
    IDLE_TTL_S: "30"
    SUSPENDED_RETENTION_S: "120"
```

After editing, redeploy: `just up` (or `helm upgrade` directly). Verify:

```bash
kubectl get pods -n agent-sandbox-system            # controller is running
kubectl api-resources --api-group=agents.x-k8s.io   # Sandbox CRD is registered
kubectl get sandboxes -n centaur-system             # CRs appear when agents spawn
```

## Limits and caveats

- **Per-deployment env scoping, not per-turn.** Centaur's credential-injection model still lives in `iron-proxy`, not the sandbox runtime. The Sandbox CRD is an isolation/lifecycle primitive, not a credential boundary.
- **No GPU provisioning.** A `Sandbox` is a Pod with a stable identity and a PVC; GPU scheduling is whatever the underlying Pod spec asks for via standard node selectors and resource requests.
- **Extensions are off.** `SandboxClaim`, `SandboxTemplate`, and `SandboxWarmPool` ship in the vendored chart but `agentSandbox.controller.extensions=false` by default and the Python backend does not yet consume them. Warm-pool/template-driven flows from `centaur-science.md` would need to be wired separately.
- **State retention is opt-in.** Without `sandbox.stateVolume.enabled=true`, the Agent Sandbox backend behaves like the Pod backend with extra steps — pause/resume still work at the CR level, but there is no PVC and the workspace is whatever the image ships.
- **Pinned at v0.4.6.** Bumping the controller is two coordinated edits: the `image.tag` in `values.yaml` and the bundled CRDs under `crds/`. Bumping is a deliberate `.centaur` SHA bump PR, not a per-deployment override.

## Verified locally — 2026-05-25

End-to-end verification on Docker Desktop k8s (`docker-desktop` single-node cluster, `hostpath` default `StorageClass`, chart revision 10 at version `0.1.40`).

### What was deployed

```text
$ kubectl get pods -n agent-sandbox-system
NAME                                        READY   STATUS    RESTARTS   AGE
agent-sandbox-controller-574b9bffb4-8mhnt   1/1     Running   0          79s

$ kubectl api-resources --api-group=agents.x-k8s.io
NAME        SHORTNAMES   APIVERSION                 NAMESPACED   KIND
sandboxes   sandbox      agents.x-k8s.io/v1alpha1   true         Sandbox

$ kubectl get crds | grep sandbox
sandboxclaims.extensions.agents.x-k8s.io      ...
sandboxes.agents.x-k8s.io                     ...
sandboxtemplates.extensions.agents.x-k8s.io   ...
sandboxwarmpools.extensions.agents.x-k8s.io   ...

$ kubectl exec -n centaur-system deploy/centaur-centaur-api -- env | grep KUBERNETES_SANDBOX
KUBERNETES_SANDBOX_CONTROLLER=agent-sandbox
KUBERNETES_SANDBOX_STATE_VOLUME_ENABLED=1
KUBERNETES_SANDBOX_STATE_VOLUME_SIZE=1Gi
```

### Spawn through `KubernetesAgentSandboxBackend`

Calling `POST /agent/spawn` produced a `Sandbox` CR plus its backing `Pod` and state `PVC`, all named off the same `<thread_key>`-derived id:

```text
Sandbox CR:    centaur-centaur-sandbox-sandbox-demo-...  replicas=1  shutdownPolicy=Retain
Backing Pod:   centaur-centaur-sandbox-sandbox-demo-...  Running  Ready=True
State PVC:     state-centaur-centaur-sandbox-sandbox-demo-...  Bound  1Gi  hostpath
```

End-to-end spawn latency was ~3.8s on a warmed cluster (sandbox image already locally cached).

### State volume is real

Before pause, the sandbox entrypoint had already populated `/home/agent/state` with the workspace layout, and a custom `marker.txt` written via `kubectl exec` was visible:

```text
$ kubectl exec -n centaur-system <sandbox-id> -c sandbox -- ls -la /home/agent/state
drwxr-xr-x 2 agent agent  branches
drwxr-xr-x 4 agent agent  claude
drwxr-xr-x 5 agent agent  codex
-rw-r--r-- 1 agent agent  marker.txt
drwxr-xr-x 2 agent agent  uploads
drwxr-xr-x 4 agent agent  workspace
```

### Pause = `patch spec.replicas=0`

Patching the Sandbox CR's `spec.replicas` to `0` (exactly what `KubernetesAgentSandboxBackend.pause_by_id` does) terminated the Pod within a few seconds while the CR and PVC remained:

```text
PAUSED STEADY STATE:
Sandbox CR:  replicas=0  shutdownPolicy=Retain
Pod:         Error from server (NotFound)
State PVC:   Bound  1Gi          ← state preserved
```

### Resume = `patch spec.replicas=1`, marker survives

Patching `spec.replicas` back to `1` produced a fresh Pod that reattached the same PVC:

```text
t=1s  phase=Pending  ready=False
t=2s  phase=Running  ready=True   ← ~2s from patch to Ready
```

The marker we wrote pre-pause was intact:

```text
$ kubectl exec -n centaur-system <sandbox-id> -c sandbox -- cat /home/agent/state/marker.txt
bfts-pause-resume-1779759026     ← matches the value we wrote before the pause
```

This is the property BFTS needs: a tree node can hold its state indefinitely between expansions while consuming **zero** Pod compute, and resume in seconds with the exact filesystem it left behind.

### Caveats encountered, recorded for future operators

- **Subchart CRDs need a one-time `kubectl apply`.** Helm only installs CRDs from a subchart's `crds/` directory on first install. Because earlier revisions had `agentSandbox.enabled=false`, the subchart wasn't materialized and its CRDs never registered. The fix is one command, recorded for future operators:

  ```bash
  kubectl apply -f .centaur/contrib/chart/charts/agent-sandbox/crds/
  ```

  Only required when flipping `agentSandbox.enabled` on for the first time on an existing release. A fresh `helm install` doesn't need it.

- **Controller image pull was flaky from `registry.k8s.io`.** First pull failed with `EOF` from the S3-backed mirror. A manual `docker pull registry.k8s.io/agent-sandbox/agent-sandbox-controller:v0.4.6` warmed Docker Desktop's image cache and the pod came up on the next kubelet retry. With `pullPolicy: IfNotPresent` (the chart default), subsequent pod restarts use the cached image.

- **The controller's `events` RBAC is missing.** The controller logs `events is forbidden: ... cannot create resource "events"` repeatedly. Non-fatal — it's just leader-election event reporting — but worth filing upstream eventually.

- **`api/sandbox/kubernetes.py:_wait_ready` is hardcoded to 60s.** If the node is under memory/CPU pressure when a sandbox is spawned, the Pod can't schedule fast enough and the spawn returns `500 Internal Server Error`. This is the existing behavior of the Pod backend too — not introduced by the agent-sandbox path. Mitigation for local dev: keep stale sandbox/proxy Pods cleaned up; for BFTS specifically, a `SandboxWarmPool` (not yet wired into the Python backend) would replace the cold-spawn budget entirely.



- [`docs/centaur-science.md`](centaur-science.md) — the forward-looking BFTS-on-Centaur spec that motivates leaning harder on Sandbox primitives (warm pools, templates, hibernation).
- Upstream PR: [paradigmxyz/centaur#162](https://github.com/paradigmxyz/centaur/pull/162).
- Upstream project: [kubernetes-sigs/agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox).
