# Research: agent-sandbox for BFTS-on-Centaur

Date: 2026-05-25. Upstream pin reviewed: `main` at the head used by `kubectl apply -f .../releases/download/v0.4.6/...` (latest release tagged **v0.4.6**, published 2026-05-14 — see `releases` API and the release body in [Sources](#sources)). The spec describes the project as "v0.1.x"; that's stale by ~6 months and 11 releases.

## TL;DR

- **Project is no longer v0.1.x.** Latest release is **v0.4.6 (2026-05-14)** with breaking changes landed in every minor since v0.4.3. The vendored CRDs under `.centaur/contrib/chart/charts/agent-sandbox/crds/` still serve `v1alpha1`; the upstream API docs already describe `v1beta1`. Pin a specific upstream release tag and re-vendor.
- **There is no general-purpose memory-checkpoint "hibernation" in the core CRDs.** "Pause / resume" on `Sandbox` is *just* `spec.replicas: 0|1` — pod is deleted, PVCs from `volumeClaimTemplates` are reattached on resume. True suspend-to-disk-and-restore is **GKE-only**, implemented as an *external* `PodSnapshot` extension (`k8s_agent_sandbox.gke_extensions.snapshots.PodSnapshotSandboxClient`); the base `Sandbox` Python class has neither `suspend()` nor `resume()` ([sandbox.py](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/clients/python/agentic-sandbox-client/k8s_agent_sandbox/sandbox.py), [test_podsnapshot_extension.py](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/clients/python/agentic-sandbox-client/test_podsnapshot_extension.py)).
- **Warm pools are real and HPA-scalable** (the `SandboxWarmPool` CRD has a `scale` subresource) and v0.4.5 added a Prometheus metric `agent_sandbox_claim_creation_total` plus a `warmpool` policy on `SandboxClaim` with three values: `none` (force cold), `default` (any matching pool), or `<pool-name>` ([api.md](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/docs/api.md), [`WarmPoolPolicy`](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/docs/api.md)). Empty-pool fallback is *create a fresh sandbox* — claims do **not** block.
- **NetworkPolicy is built into `SandboxTemplate`.** With `networkPolicyManagement: Managed` (default) and `spec.networkPolicy` omitted, the controller installs a strict "Secure Default": ingress = router only, egress = public internet only (RFC1918 + metadata server blocked). Provide a custom `networkPolicy` (with `ingress`/`egress` arrays in standard k8s shape) to override. Upstream `docs/api.md` explicitly states the policy is **shared per template**, but the `secure-sandboxtemplate.yaml` comment claims it's *per-sandbox* — **the two upstream sources disagree**; treat as a unique-per-template shared policy (api.md is the more authoritative).
- **Stable identity changed in v0.4.6.** The headless Service is now **opt-in**: you must set `spec.service: true` on `Sandbox` or `SandboxTemplate` to get `status.serviceFQDN`. Centaur's existing backend currently sends `service: false` ([`.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:113`](../../../.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py)) — for the spec's "controller→sandbox exec path with stable hostname," BFTS-on-Centaur needs to flip this for node templates that depend on DNS rather than direct pod-IP exec.
- **agent-sandbox is *not* a GPU scheduler.** Confirmed. The spec is right — GPU/runtimeClass/Kata selection is plain Kubernetes `podTemplate.spec.{runtimeClassName, nodeSelector, tolerations, resources}` passthrough. The integration must do this in the `SandboxTemplate` per role.

## Capability matrix vs. the spec

| Spec assumption | Status | Source | Notes |
| --- | --- | --- | --- |
| (1) CRDs `Sandbox`, `SandboxTemplate`, `SandboxClaim`, `SandboxWarmPool` exist | **Shipped** | [`agents.x-k8s.io_sandboxes.yaml`](../../../.centaur/contrib/chart/charts/agent-sandbox/crds/agents.x-k8s.io_sandboxes.yaml), [`extensions.agents.x-k8s.io_sandboxtemplates.yaml`](../../../.centaur/contrib/chart/charts/agent-sandbox/crds/extensions.agents.x-k8s.io_sandboxtemplates.yaml), [`extensions.agents.x-k8s.io_sandboxclaims.yaml`](../../../.centaur/contrib/chart/charts/agent-sandbox/crds/extensions.agents.x-k8s.io_sandboxclaims.yaml), [`extensions.agents.x-k8s.io_sandboxwarmpools.yaml`](../../../.centaur/contrib/chart/charts/agent-sandbox/crds/extensions.agents.x-k8s.io_sandboxwarmpools.yaml) | Vendored CRDs serve `v1alpha1`; upstream [`docs/api.md`](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/docs/api.md) already describes `v1beta1`. README example still uses `v1alpha1`. **Pin a release tag.** |
| (1) Lifecycle field for "pause/resume/hibernate" | **Partial — pod stop/start only** | `Sandbox` CRD `spec.replicas: int32, min:0, max:1, default:1` ([yaml lines 3833-3838](../../../.centaur/contrib/chart/charts/agent-sandbox/crds/agents.x-k8s.io_sandboxes.yaml#L3833-L3838)); CRD has scale subresource | Pause = scale to 0 (pod deleted, PVC retained). No memory checkpoint. |
| (1) "Scheduled deletion" | **Shipped** | `Sandbox` CRD `spec.shutdownTime` (RFC3339) + `spec.shutdownPolicy: Delete \| Retain` (default `Retain`); `SandboxClaim` adds `DeleteForeground` and `ttlSecondsAfterFinished` (v0.4.3 release notes — [release v0.4.3 body](#sources)) | `ttlSecondsAfterFinished` is on `SandboxClaim.spec.lifecycle` only, not on `Sandbox.spec`. |
| (2) True hibernate/auto-resume "memory→disk → resume restores" | **Wrong / GKE-only** | [`gke_extensions/snapshots/`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/clients/python/agentic-sandbox-client/k8s_agent_sandbox/gke_extensions/snapshots); [`test_podsnapshot_extension.py`](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/clients/python/agentic-sandbox-client/test_podsnapshot_extension.py); roadmap "Scale-down / Resume PVC based" ([roadmap.md](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/roadmap.md)) | `sandbox.suspend(snapshot_before_suspend=True)` + `sandbox.resume()` only exist on `PodSnapshotSandboxClient` which targets GKE's `PodSnapshot` API (graduated `v1` in v0.4.5). On non-GKE: only `replicas: 0/1` is available. |
| (3) `SandboxWarmPool` allocation latency claim ("milliseconds") | **Inference — supported by design, no number** | [`SandboxWarmPool` CRD](../../../.centaur/contrib/chart/charts/agent-sandbox/crds/extensions.agents.x-k8s.io_sandboxwarmpools.yaml); [hpa-swp-scaling example](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/examples/hpa-swp-scaling/README.md) | "Adoption" is renaming/relabeling a pre-warmed `Sandbox` to the claim; no published latency SLO. Roadmap item "Creation Latency Metrics" ([#123](https://github.com/kubernetes-sigs/agent-sandbox/issues/123)) is still open. |
| (3) Pool-empty fallback | **Shipped: falls back to cold-start** | [`docs/api.md` `WarmPoolPolicy`](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/docs/api.md) — "select from all available warm pools that match the template" with `none` = always cold | `SandboxClaim` does **not** block: when no pool exists or the pool is empty under `default`/`<pool-name>` policy, the claim falls through to creating a fresh `Sandbox` (inference: api.md doesn't explicitly say "fallback"; it does say `none = always create fresh`, implying the default path is "use pool if available, otherwise create fresh"). |
| (3) Heterogeneous templates in a pool | **One template per pool** | [`SandboxWarmPool.spec.sandboxTemplateRef.name` is a single ref, required](../../../.centaur/contrib/chart/charts/agent-sandbox/crds/extensions.agents.x-k8s.io_sandboxwarmpools.yaml#L42-L48) | To have different roles pre-warmed, create one `SandboxWarmPool` per `SandboxTemplate`. |
| (4) gVisor / Kata via `RuntimeClass` | **Shipped via standard pod-spec passthrough** | [`examples/kata-gke-sandbox/sandbox-kata-gke.yaml`](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/examples/kata-gke-sandbox/sandbox-kata-gke.yaml) sets `spec.podTemplate.spec.runtimeClassName: kata-qemu`; CRDs expose `runtimeClassName` on both `Sandbox` and `SandboxTemplate` podTemplate (grep hits) | gVisor: same pattern, `runtimeClassName: gvisor` (per [secure-sandboxtemplate.yaml](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/extensions/examples/secure-sandboxtemplate.yaml)). Documented Kata caveats on GKE: requires N2 Intel machines, Ubuntu nodes; **AMD/ARM/E2 unsupported** ([kata-gke README](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/examples/kata-gke-sandbox/README.md)). No documented Kata-specific incompatibility with warm pools or PVC. |
| (5) Python SDK for create/exec/log/hibernate/delete | **Partial — first four shipped, hibernate is GKE-only** | [`SandboxClient.create_sandbox`](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/clients/python/agentic-sandbox-client/k8s_agent_sandbox/sandbox_client.py), [`sandbox.commands.run` → `ExecutionResult(stdout, stderr, exit_code)`](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/clients/python/agentic-sandbox-client/k8s_agent_sandbox/models.py), [`sandbox.files.write/read`](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/clients/python/agentic-sandbox-client/k8s_agent_sandbox/sandbox.py) | Streaming logs are **not** a first-class SDK method; `commands.run` returns the result after completion (buffered). For streaming you'd hit the in-pod HTTP server directly. Async client exists (`AsyncSandboxClient`, requires `pip install k8s-agent-sandbox[async]`); tunnel mode is sync-only. |
| (5) Auth model (KSA / kubeconfig / both) | **Both** | [`SandboxClient`](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/clients/python/agentic-sandbox-client/k8s_agent_sandbox/sandbox_client.py), [`test_podsnapshot_extension.py` uses `config.load_incluster_config()` with fallback to `config.load_kube_config()`](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/clients/python/agentic-sandbox-client/test_podsnapshot_extension.py) | In-cluster `SandboxInClusterConnectionConfig` (v0.4.3) lets a Centaur workflow pod call sandboxes without deploying the router. |
| (6) PV provisioned per Sandbox, surviving pod restart | **Shipped via `volumeClaimTemplates`** | `Sandbox.spec.volumeClaimTemplates` (CRD), [`SandboxTemplate.spec.volumeClaimTemplates` added in v0.4.3](#sources) | StatefulSet-style PVC semantics: "PVC-backed volumes use StatefulSet-style merge semantics with the pod template" (v0.4.3 release notes). PVC retention on Sandbox delete is governed by **`spec.shutdownPolicy: Delete|Retain` (default `Retain`)** — when the Sandbox is deleted with policy `Retain`, the *Sandbox object stays* (status will show Expired) but the underlying pod/Service are torn down (per [`docs/api.md` `ShutdownPolicy`](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/docs/api.md)). **Inference:** PVCs explicitly survive `replicas: 0` (otherwise the docs would say so); whether they survive a `Delete`-policy shutdown depends on the PVC's own reclaim policy / storage class. Verify before trusting "node working state on Sandbox PV survives reschedule." |
| (7) "agent-sandbox is not a scheduler / does not provision GPUs" | **Confirmed** | No GPU-aware fields in CRDs; `podTemplate.spec` passthrough is the only mechanism (`nodeSelector`, `tolerations`, container `resources.limits` standard) | Use `nodeSelector` or `runtimeClassName` to target a GPU node pool, exactly like a Deployment. |
| (8) NetworkPolicy / egress allowlist | **Shipped (richer than expected)** | `SandboxTemplate.spec.networkPolicy.{ingress,egress}` arrays (standard k8s shape) + `spec.networkPolicyManagement: Managed \| Unmanaged` ([CRD](../../../.centaur/contrib/chart/charts/agent-sandbox/crds/extensions.agents.x-k8s.io_sandboxtemplates.yaml#L38-L227)); [`docs/api.md` `NetworkPolicySpec`](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/docs/api.md) describes the strict Secure Default | Default-deny posture is automatic. PolicyTypes/PodSelector are managed by the controller. *Per-sandbox vs. per-template:* api.md says "single shared NetworkPolicy per template"; the example comment in `secure-sandboxtemplate.yaml` says "unique NetworkPolicy per sandbox" — **flag**. |
| (9) Stable identity / hostname | **Shipped, opt-in since v0.4.6** | `Sandbox.spec.service` boolean (v0.4.6 release notes mark this opt-in); `status.serviceFQDN`; controller flag `--cluster-domain` (default `cluster.local`) | Centaur's existing backend writes `service: false` ([line 113](../../../.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py#L113)). The Python SDK's `SandboxInClusterConnectionConfig` defaults to cluster-DNS routing, which **requires** the headless Service. The alternative `use_pod_ip=True` reads `status.podIPs` and bypasses DNS — also shipped, and the only path that works with `service: false`. |
| (10) Version / stability risks | **High** | 11 releases in 6 months ([releases API](#sources)); breaking changes in v0.4.5 (PodSnapshot v1) and v0.4.6 (service opt-in) | Pin a release. Re-vendor the CRDs from the same tag the controller image uses. |

## CRD reference

All CRDs below are quoted from the **vendored copies** in `.centaur/contrib/chart/charts/agent-sandbox/crds/`. Those still serve `v1alpha1`. The upstream `docs/api.md` already documents `v1beta1` — manifests for that have not been re-vendored into Centaur. Re-vendoring is a one-line PR (change the chart values `image.tag` and re-export from the release's `extensions.yaml`).

### `Sandbox` — `agents.x-k8s.io/v1alpha1`

Top-level `spec` fields (from [`agents.x-k8s.io_sandboxes.yaml`](../../../.centaur/contrib/chart/charts/agent-sandbox/crds/agents.x-k8s.io_sandboxes.yaml)):

- `podTemplate` (required) — `metadata` + full Kubernetes `PodSpec`. Notable passthroughs: `runtimeClassName`, `nodeSelector`, `tolerations`, `priorityClassName`, `hostname`, `subdomain`, container `resources`, `volumeMounts`.
- `replicas` — `int32`, `min:0 max:1 default:1`. **This is the pause/resume control.**
- `service` — `bool`. Controls headless Service creation. Default `false` since **v0.4.6** (breaking).
- `shutdownPolicy` — enum `Delete | Retain`, default `Retain`.
- `shutdownTime` — `date-time`. Absolute expiry.
- `volumeClaimTemplates[]` — standard `PersistentVolumeClaim` templates (accessModes, resources, storageClassName, etc.).

Status fields: `conditions[]`, `podIPs[]`, `replicas`, `selector` (label-selector string), `service`, `serviceFQDN`. The CRD declares **`subresources.scale`** with `specReplicasPath: .spec.replicas` — so any standard k8s scaler can drive pause/resume.

Minimal example (works on the vendored v1alpha1 served version):

```yaml
apiVersion: agents.x-k8s.io/v1alpha1
kind: Sandbox
metadata:
  name: my-sandbox
spec:
  service: true                           # v0.4.6+: opt-in headless Service
  podTemplate:
    spec:
      runtimeClassName: gvisor
      containers:
      - name: agent
        image: my-image:latest
  volumeClaimTemplates:
    - metadata: { name: workspace }
      spec:
        accessModes: [ReadWriteOnce]
        resources: { requests: { storage: 10Gi } }
```

### `SandboxTemplate` — `extensions.agents.x-k8s.io/v1alpha1`

Top-level `spec` fields (from [`extensions.agents.x-k8s.io_sandboxtemplates.yaml`](../../../.centaur/contrib/chart/charts/agent-sandbox/crds/extensions.agents.x-k8s.io_sandboxtemplates.yaml)):

- `podTemplate` (required) — same shape as `Sandbox.spec.podTemplate`.
- `volumeClaimTemplates[]` — propagated to created Sandboxes (added v0.4.3).
- `networkPolicy` — `{ ingress: [...], egress: [...] }` using standard k8s `NetworkPolicyIngressRule`/`NetworkPolicyEgressRule` shapes (NamespaceSelector, PodSelector, IPBlock, ports). `PodSelector` and `PolicyTypes` are managed by the controller and **intentionally excluded** from this surface ([api.md `NetworkPolicySpec`](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/docs/api.md)).
- `networkPolicyManagement` — enum `Managed | Unmanaged`, default `Managed`. `Unmanaged` skips NetworkPolicy creation entirely (use this when running on Cilium / a CNI that owns policy).
- `envVarsInjectionPolicy` — enum `Allowed | Overrides | Disallowed`, default `Disallowed`. If `Disallowed`, a claim that tries to set `env` is rejected.
- `service` — `bool` (same opt-in as `Sandbox`).

Real example with a strict security profile, taken from upstream [`secure-sandboxtemplate.yaml`](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/extensions/examples/secure-sandboxtemplate.yaml):

```yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxTemplate
metadata:
  name: secure-datascience-template
spec:
  podTemplate:
    spec:
      runtimeClassName: gvisor
      securityContext:
        runAsUser: 1000
        runAsNonRoot: true
      containers:
        - name: my-container
          image: busybox
          ports: [{ containerPort: 8888, protocol: TCP }]
  networkPolicy:
    ingress:
      - from:
          - namespaceSelector: { matchLabels: { istio-injection: enabled } }
            podSelector: { matchLabels: { app: istio-ingressgateway } }
    egress:
      - ports:
          - { protocol: UDP, port: 53 }
          - { protocol: TCP, port: 53 }
```

### `SandboxClaim` — `extensions.agents.x-k8s.io/v1alpha1`

Top-level `spec` fields (from [`extensions.agents.x-k8s.io_sandboxclaims.yaml`](../../../.centaur/contrib/chart/charts/agent-sandbox/crds/extensions.agents.x-k8s.io_sandboxclaims.yaml)):

- `sandboxTemplateRef.name` (required) — template to materialize from.
- `lifecycle.shutdownPolicy` — enum `Delete | DeleteForeground | Retain`, default `Retain`.
- `lifecycle.shutdownTime` — absolute expiry (not propagated to the Sandbox; claim controller enforces it).
- `lifecycle.ttlSecondsAfterFinished` — int32, min 0. **Started timer from the `Finished` condition's `LastTransitionTime`** (v0.4.3). This is the right knob for auto-reaping completed evaluation nodes.
- `warmpool` — string, default `"default"`. Values: `"none"` (always cold), `"default"` (any matching pool), or a specific pool name.
- `additionalPodMetadata.{labels,annotations}` — propagated to the underlying pod.
- `env[]` — list of `{ name, value, containerName? }`; subject to the template's `envVarsInjectionPolicy`.

Real example from [`extensions/examples/sandboxclaim.yaml`](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/extensions/examples/sandboxclaim.yaml):

```yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxClaim
metadata: { name: my-claim }
spec:
  sandboxTemplateRef: { name: secure-datascience-template }
  warmpool: python-sdk-warmpool      # or "default" / "none"
  lifecycle:
    ttlSecondsAfterFinished: 600     # reap 10 min after the node's pod exits
    shutdownPolicy: Delete
```

Status: `conditions[]` and `sandbox: { name, podIPs[] }`. The claim's status references the adopted/created `Sandbox` by name; that's what the SDK reads to construct the connection URL.

### `SandboxWarmPool` — `extensions.agents.x-k8s.io/v1alpha1`

Top-level `spec` fields (from [`extensions.agents.x-k8s.io_sandboxwarmpools.yaml`](../../../.centaur/contrib/chart/charts/agent-sandbox/crds/extensions.agents.x-k8s.io_sandboxwarmpools.yaml)):

- `replicas` (required) — int32, min 0.
- `sandboxTemplateRef.name` (required) — **one template per pool**.
- `updateStrategy.type` — enum `Recreate | OnReplenish`, default `OnReplenish`. `Recreate` immediately deletes stale pre-warmed pods on template-spec change; `OnReplenish` waits until each pre-warmed pod is consumed.

Has a **`scale` subresource** so the HPA can target it directly. Working example from [`examples/hpa-swp-scaling`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/examples/hpa-swp-scaling):

```yaml
# warm pool driven by the controller's Prometheus metric:
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxWarmPool
metadata: { name: python-sdk-warmpool }
spec:
  replicas: 10
  sandboxTemplateRef: { name: python-sandbox-template }
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata: { name: agent-warmpool-hpa }
spec:
  scaleTargetRef:
    apiVersion: extensions.agents.x-k8s.io/v1alpha1
    kind: SandboxWarmPool
    name: python-sdk-warmpool
  minReplicas: 10
  maxReplicas: 100
  metrics:
    - type: External
      external:
        metric:
          name: "prometheus.googleapis.com|agent_sandbox_claim_creation_total|counter"
          selector: { matchLabels: { metric.labels.warmpool_name: "python-sdk-warmpool" } }
        target: { type: Value, value: "0.5" }
```

Status: `replicas`, `readyReplicas`, `selector`.

## Python SDK surface

Package: `k8s-agent-sandbox` (on PyPI). Source under [`clients/python/agentic-sandbox-client/`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/clients/python/agentic-sandbox-client). Two top-level classes ([`__init__.py`](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/clients/python/agentic-sandbox-client/k8s_agent_sandbox/__init__.py)): `SandboxClient`, `AsyncSandboxClient`.

### Connection modes (`models.py`)

| Mode | Class | When |
| --- | --- | --- |
| Tunnel (default) | `SandboxLocalTunnelConnectionConfig` | Local/CI; opens `kubectl port-forward` to `sandbox-router-svc`. **Sync only.** |
| Gateway | `SandboxGatewayConnectionConfig(gateway_name=...)` | Prod GKE Gateway. |
| Direct | `SandboxDirectConnectionConfig(api_url=...)` | Custom DNS / explicit router URL. |
| **In-cluster** | `SandboxInClusterConnectionConfig(use_pod_ip=False)` | **The right choice for Centaur**: bypasses the router entirely. Default = cluster DNS (`{sandbox_id}.{namespace}.svc.cluster.local:8888`); `use_pod_ip=True` reads `status.podIPs` and goes direct. |

### `SandboxClient` (`sandbox_client.py`)

```python
client = SandboxClient(connection_config=SandboxInClusterConnectionConfig())

sandbox = client.create_sandbox(
    template="proposer-sandbox-template",   # SandboxTemplate name
    namespace="centaur-research",
    sandbox_ready_timeout=180,
    labels={"centaur.run/run-id": run_id, "centaur.run/node-id": node_id},
    warmpool="proposer-warmpool",           # or "default" / "none"
    shutdown_after_seconds=3600,             # sets lifecycle.shutdownTime + Delete
)

result = sandbox.commands.run("python eval.py", timeout=600)  # ExecutionResult(stdout, stderr, exit_code)
sandbox.files.write("/work/code.py", code_str)
data = sandbox.files.read("/work/metrics.json")

status, msg = sandbox.status()      # ("SandboxReady" | "SandboxNotReady" | "SandboxNotFound", msg)
sandbox.terminate()                  # deletes the SandboxClaim
# OR
sandbox.close_connection()           # frees local resources, leaves remote running

# Re-attach later by claim name (works after a worker restart):
sandbox = client.get_sandbox(claim_name="sandbox-claim-1234abcd")
```

`AsyncSandboxClient` (under `[async]` extras) supports `Direct`, `Gateway`, and `InCluster` configs but **not** `LocalTunnel`. Centaur's workflow worker is async, so this is the path; in-cluster + `use_pod_ip=True` is the lowest-latency option but requires permission to read the `Sandbox` `status.podIPs`.

### Gaps the integration must wrap

- **Suspend/resume is not on `SandboxClient`.** It only exists on `PodSnapshotSandboxClient` from `k8s_agent_sandbox.gke_extensions.snapshots`, which talks to GKE's `PodSnapshot` API. On non-GKE Centaur deployments the equivalent is `kubectl scale sandbox/<id> --replicas=0` (or a `patch` PATCH `/spec/replicas`) — Centaur's existing backend already does this manually with `CustomObjectsApi` ([`kubernetes_agent_sandbox.py`](../../../.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py)). The BFTS controller should expose a `pause_node()` / `resume_node()` that issues these scale PATCHes — not the SDK's `suspend()`.
- **No streaming exec.** `commands.run()` is request/response. For long-running experiments emitting periodic metrics, the workflow should either poll files (`sandbox.files.read("/work/metrics.jsonl")`) or hit the sandbox HTTP server directly.
- **No native `list_*` for SandboxTemplate/SandboxWarmPool.** SDK only manages claims. Template/pool creation is YAML-only, driven by Helm in Centaur.
- **The Python SDK uses `kubernetes-asyncio` indirectly** via `k8s_helper.py`; Centaur's existing `KubernetesAgentSandboxBackend` already uses `kubernetes_asyncio.client.CustomObjectsApi` directly without the SDK ([line 6](../../../.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py#L6)). **For the BFTS workflow, follow Centaur's existing pattern** rather than introducing a second sandbox client — the SDK and the existing backend would race on the same CRD instances.

## Gotchas

1. **API version mismatch.** Vendored CRDs say `v1alpha1`. Upstream `docs/api.md` describes `v1beta1`. Centaur's `kubernetes_agent_sandbox.py` hardcodes `"v1alpha1"` (line 16). When you bump the controller image to a release whose `extensions.yaml` flips to `v1beta1`, the existing Centaur backend will 404 until that constant is updated.
2. **`service: false` is the new default in v0.4.6.** Existing Centaur code already passes `service: False`. If a BFTS template needs a stable hostname for `controller→sandbox` HTTP exec, it must set `service: true` *on the template*, otherwise `SandboxInClusterConnectionConfig()` (DNS mode) will fail and only `use_pod_ip=True` will work.
3. **"Hibernation" is not what the spec implies.** `replicas: 0` deletes the pod. CPython process state is gone. Whatever was on the PVC is what survives. If an experiment relies on in-RAM state surviving "hibernation," it will silently lose work. Either (a) accept disk-only persistence and design the agent's checkpoint loop accordingly, or (b) accept that you can't truly hibernate live training jobs outside GKE + `PodSnapshot`.
4. **PVC retention on Sandbox deletion is *not* explicit.** `shutdownPolicy: Retain` keeps the *Sandbox object*; whether the PVC survives is downstream of the PVC's reclaim policy and the storage class. The Sandbox CRD doesn't expose a `pvcReclaimPolicy` field. Test before relying on PVCs persisting after `Delete` shutdown.
5. **NetworkPolicy shared-per-template vs per-sandbox is ambiguous.** `docs/api.md` says shared per template; `secure-sandboxtemplate.yaml` example comment says unique per sandbox. Either way, you cannot vary egress *per claim* without varying the template — design templates per egress profile (e.g. `proposer-template-anthropic`, `proposer-template-openai`).
6. **`networkPolicyManagement: Managed` default deny is opinionated.** RFC1918 is blocked. If the BFTS evaluation needs to call an in-cluster service (e.g. a vLLM serving pod, a metrics sink, an experiment data source), the egress must be explicitly opened. This is exactly what the spec asks for, but it means the `SandboxTemplate` author needs to enumerate every internal CIDR the agent will reach.
7. **`automountServiceAccountToken` defaults to `false` for SandboxTemplate.** Per [api.md](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/docs/api.md): "If AutomountServiceAccountToken is not specified in the PodSpec, it defaults to false to ensure a secure-by-default environment." If the sandbox needs to call the Kubernetes API (e.g. to read a ConfigMap), that has to be flipped explicitly.
8. **`SandboxWarmPool` has no min/max pool size; it has `replicas` only.** Reactive sizing is HPA's job. The example uses `agent_sandbox_claim_creation_total` (a counter) evaluated as rate-per-second. That metric is only published from v0.4.x.
9. **Heterogeneous warm pools don't exist.** One template per pool. To pre-warm three roles (proposer / debugger / GPU), you need three pools.
10. **PodDisruptionBudget is not built in.** There's a `manual-pdb` example, no PDB CRD. For long-lived nodes you'd add a PDB by hand.
11. **`spec.replicas` is `min:0 max:1`.** The Sandbox is literally singleton. No accidental multi-replica autoscaling.
12. **Controller default concurrency is 1.** [`docs/configuration.md`](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/docs/configuration.md) — `--sandbox-claim-concurrent-workers=1` default. For a BFTS fan-out of dozens of claims per second this will throttle. Bump to 10–50, and consider `--sandbox-warm-pool-max-batch-size` (default 300) and `--kube-api-qps`/`--kube-api-burst` flags. Centaur's vendored chart exposes all of these in `controller.*` values keys ([`agent-sandbox/values.yaml`](../../../.centaur/contrib/chart/charts/agent-sandbox/values.yaml)).
13. **"Strict Sandbox-to-Pod Mapping" is on the roadmap** ([roadmap.md](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/roadmap.md), [#127](https://github.com/kubernetes-sigs/agent-sandbox/issues/127)) — meaning the 1:1 mapping is not yet guaranteed. v0.4.3's "duplicate Sandbox adoption during informer cache lag" fix suggests there were/are race conditions during warm-pool adoption. Pin a recent release.
14. **Memory sharing across sandboxes is aspirational.** The README lists it under "Desired Sandbox Characteristics" / "Exploring possibilities". Not in any CRD. Don't plan on it.
15. **The Centaur integration is real** (it's not just the tweet). The Centaur submodule ships an `agent-sandbox` Helm subchart and a working Python backend (`kubernetes_agent_sandbox.py`) that writes `Sandbox` CRDs directly. The spec's hedge "reported via tweet, not confirmed in published docs" can be tightened: it's confirmed by code in this repo.

## Integration proposal

### CRDs declared at install time (per cluster)

Declared via Helm in `values.local.yaml` (or a sibling chart pointing at the vendored `agent-sandbox` subchart). Pin `image.tag` to a specific release (recommend **v0.4.6** as the current latest; budget a re-vendor when the next release lands). Set the following controller flags via `controller.*` values (which already map to CLI args — see [`_controller-args.tpl`](../../../.centaur/contrib/chart/charts/agent-sandbox/templates/_controller-args.tpl)):

```yaml
# values.local.yaml addition
agent-sandbox:
  image:
    tag: v0.4.6
  controller:
    extensions: true                       # enable Template/Claim/WarmPool controllers
    sandboxConcurrentWorkers: 10
    sandboxClaimConcurrentWorkers: 25      # BFTS fan-out
    sandboxWarmPoolConcurrentWorkers: 5
    sandboxTemplateConcurrentWorkers: 2
    kubeApiQps: 50
    kubeApiBurst: 100
    clusterDomain: cluster.local
```

### `SandboxTemplate` per role

One template per role/isolation profile. Each template embeds: `runtimeClassName`, `nodeSelector`/`tolerations`, container `resources`, the egress allowlist for that role, and a `volumeClaimTemplate` for `/work` state.

- `proposer-template` — `runtimeClassName: gvisor`, CPU-only, default egress = model providers + Centaur's internal experiment-data service.
- `debugger-template` — same isolation profile as proposer but larger `resources.limits.memory`.
- `reviewer-template` — `runtimeClassName: gvisor`, vision model egress allowlisted, ephemeral (no PVC).
- `gpu-experiment-template` — `runtimeClassName: kata-qemu` (for least-trusted code; per spec §"Roles"), `nodeSelector` onto a GPU node pool, `resources.limits["nvidia.com/gpu"]: 1`. **Caveat**: Kata on GKE requires N2-Intel + Ubuntu nodes ([kata-gke README](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/examples/kata-gke-sandbox/README.md)); on other clouds verify Kata's nested-virt support.

Each template sets `spec.service: true` so the workflow can reach the sandbox via stable DNS, and `spec.networkPolicyManagement: Managed` with an explicit `spec.networkPolicy.egress` allowlist.

### `SandboxWarmPool` per role

One pool per role template. HPA-scaled against `agent_sandbox_claim_creation_total` for that pool. For Centaur we'd start with `replicas: 5` per pool and let HPA grow it; the GPU pool can probably stay `replicas: 0` and only burst on demand (or skip the pool entirely and let GPU claims pay cold-start, since GPU nodes are slow to schedule anyway).

### `SandboxClaim` per node, per expansion

The BFTS controller workflow creates one `SandboxClaim` per node it expands. The claim:

- references the right template via `sandboxTemplateRef.name`,
- sets `warmpool: <role-warmpool-name>` (so claims for proposer land in the proposer pool),
- sets `lifecycle.ttlSecondsAfterFinished: <small>` (e.g. 300) so the claim auto-reaps after the agent's experiment-runner container exits,
- sets `lifecycle.shutdownPolicy: Delete` for ephemeral, leaf, or pruned nodes; or `Retain` if the controller intends to revisit the node (i.e., hold the Sandbox open for later).
- labels itself with `centaur.run/run-id`, `centaur.run/node-id`, `centaur.run/role` for observability.

The controller workflow uses Centaur's existing `KubernetesAgentSandboxBackend` (already in this submodule) to PATCH the resulting `Sandbox` to `replicas: 0` when "hibernating" a node between expansions, and PATCH back to `replicas: 1` when it next visits the node. This is the spec's "hibernate/resume" — pod stop/start with PVC reattach — clearly named so the rest of the team isn't surprised when it doesn't preserve in-RAM state.

For GPU work: the workflow does *not* create a `SandboxClaim`. Instead, per spec §"Compute split", it enqueues an external job and blocks on a webhook. A GPU `SandboxTemplate` is still useful as the *callback target* for the external runner if you want a per-job iframe of isolation (each external job creates one short-lived GPU sandbox claim, runs in it, reports out, deletes). But this is an optimization — vanilla "Kubernetes Job on the GPU node pool" is also fine, and the spec already says agent-sandbox doesn't help here.

### NetworkPolicy

Attached to `SandboxTemplate.spec.networkPolicy` per role. Default-deny is automatic. Egress allowlist per role:

- `proposer/debugger` templates: egress to `*.anthropic.com` / `*.openai.com` (model provider) + internal CIDR for the experiment data service + internal CIDR for the GPU-callback API.
- `reviewer` template: egress to vision model provider only.
- `gpu-experiment` template: egress to internal CIDR for the GPU-result-callback only.

Because v0.4.3+ NetworkPolicy is *managed by the controller* at the template level, updates to the policy are picked up by all existing claims using that template (per [api.md](https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/docs/api.md)): "any updates to these rules will be applied to the single shared policy object." That's convenient for incident response (yank an egress rule and it propagates immediately).

### Where GPU work crosses the sandbox boundary

Confirmed by research that agent-sandbox is **not** a scheduler extension. The BFTS controller crosses the boundary by enqueuing an external job. Sandbox-internal CPU experiments stay on the Sandbox PVC. GPU job outputs are written back via webhook → durable event → resume the workflow. No change vs. the spec.

### Required Helm/values changes

The repo already vendors the chart at `.centaur/contrib/chart/charts/agent-sandbox/`. Three changes:

1. **`values.local.yaml`** — add the `agent-sandbox.*` block shown above, pinning `image.tag: v0.4.6`, setting `controller.extensions: true`, and bumping the four concurrency knobs.
2. **A new chart at `overlay/centaur-science/` (or a values overlay)** that ships the four `SandboxTemplate` + four `SandboxWarmPool` CRs alongside their network policies. This is repository content, not a Centaur chart change.
3. **`overlay/centaur-science/templates/runtimeclass.yaml`** — declare or reference the `RuntimeClass`es (`gvisor`, `kata-qemu`). RuntimeClass is a cluster-scoped resource, not provided by agent-sandbox.

### CRD constant in Centaur

Centaur's `kubernetes_agent_sandbox.py` hardcodes `_AGENT_SANDBOX_VERSION = "v1alpha1"`. The BFTS workflow's `Sandbox` PATCH path uses the same backend. **Action item for master plan**: track upstream API graduation and bump this constant in lockstep with the chart's `image.tag`.

## Open questions for the master plan

1. **Centaur cluster target.** Is the BFTS deployment going to GKE (where the GKE `PodSnapshot` extension *is* available, and "true hibernation" is real) or somewhere else (where it's not)? The answer changes how aggressively the controller can "hold the tree open." If non-GKE, the master plan needs to say the loud part: hibernation is pod stop/start with PV reattach; in-RAM state of long experiments is lost on hibernate. The spec currently leaves this ambiguous.
2. **PVC retention semantics.** Verify experimentally whether a `Sandbox` with `shutdownPolicy: Retain` keeps its PVC after the underlying pod/service are torn down. The CRD docs don't make this explicit. If PVCs are lost on Retain-shutdown, "node working state on Sandbox PV" doesn't survive scheduled deletion — only `replicas: 0` survives it.
3. **NetworkPolicy scope.** Pin down whether the policy is shared-per-template (per `docs/api.md`) or unique-per-sandbox (per the `secure-sandboxtemplate.yaml` comment). If shared-per-template, the integration cannot vary egress *per node* without varying the template. Either confirm via a test pod (`kubectl get netpol -l app.kubernetes.io/managed-by=agent-sandbox`) or pin the assumption.
4. **Pool-empty fallback latency.** The spec's "claim a pre-warmed pod in milliseconds" assumption assumes the pool is non-empty. Under burst load (HPA scaling lags real claim rate), what's the actual p50/p99 time-to-Ready when the pool drains? No upstream SLO. The master plan should include a benchmark task before the BFTS controller's expansion fan-out is sized.
5. **Stable hostname vs. pod-IP.** The integration can use `SandboxInClusterConnectionConfig(use_pod_ip=True)` and skip headless Services entirely (saves kube-proxy/CoreDNS overhead at thousands of sandboxes — explicitly the v0.4.6 breaking-change motivation). Decide: pay the DNS/Service overhead for stable identity, or do pod-IP routing and accept that the IP changes on every restart. Recommend pod-IP.
6. **API graduation timing.** When does the upstream flip from `v1alpha1` to `v1beta1` in the served manifests? The README example and the vendored CRDs disagree with `docs/api.md`. A v1beta1 cut would invalidate Centaur's hardcoded API-version constant.
7. **gVisor + GPU.** Spec implies gVisor by default. gVisor + GPU passthrough is **not** supported (gVisor's runsc historically blocks GPU device access). For the GPU experiment template, runtime must be Kata (or plain container) — confirm the BFTS author is OK with the GPU path running under Kata or unsandboxed. Documented constraint, not a bug; flag it.
8. **`automountServiceAccountToken: false` by default.** Does the BFTS sandbox need to call the Kubernetes API (to write its own snapshots, fetch ConfigMaps)? If so, override per-template; otherwise leave the secure default.
9. **Iron-proxy + sandbox NetworkPolicy.** Centaur uses an "iron-proxy" layer for per-interaction credential scoping (mentioned in the spec's non-goals). The sandbox egress allowlist needs to permit the iron-proxy's address, not the model provider directly. Plan needs to specify that ingress to iron-proxy is the only egress path.

## Sources

| URL | What it gave us |
| --- | --- |
| `https://github.com/kubernetes-sigs/agent-sandbox` (cached at `/Users/perhats/.cursor/projects/Users-perhats-Documents-GitHub-centaur-scientist/uploads/agent-sandbox-0.md`) | Project overview, repo layout, README v1alpha1 example, release v0.4.6 banner. |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/README.md` | Live README, confirmed v1alpha1 example still in main, overview of `Sandbox` features list ("stable identity, persistent storage, lifecycle management: creation, scheduled deletion, pausing and resuming"). |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/roadmap.md` | Roadmap items confirming PVC-scale-down is not yet in core, "Strict Sandbox-to-Pod Mapping" still open, memory sharing not shipped, Status Updates / Creation Latency Metrics open. |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/docs/api.md` | Canonical v1beta1 API reference. Source for `Lifecycle`, `NetworkPolicySpec`, `WarmPoolPolicy`, `ShutdownPolicy` values. |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/docs/configuration.md` | Controller CLI flags (concurrency, QPS/burst, cluster-domain). |
| Vendored `.centaur/contrib/chart/charts/agent-sandbox/crds/agents.x-k8s.io_sandboxes.yaml` | Exact `Sandbox` v1alpha1 CRD schema served by current installs. |
| Vendored `.centaur/contrib/chart/charts/agent-sandbox/crds/extensions.agents.x-k8s.io_sandboxtemplates.yaml` | Exact `SandboxTemplate` v1alpha1 schema; source for `networkPolicy`/`networkPolicyManagement`/`envVarsInjectionPolicy` enums. |
| Vendored `.centaur/contrib/chart/charts/agent-sandbox/crds/extensions.agents.x-k8s.io_sandboxclaims.yaml` | Exact `SandboxClaim` v1alpha1 schema (note: v1alpha1 vendored copy does **not** yet have `ttlSecondsAfterFinished` and `Finished` condition — those are v0.4.3 additions; upstream main / v1beta1 has them per api.md). |
| Vendored `.centaur/contrib/chart/charts/agent-sandbox/crds/extensions.agents.x-k8s.io_sandboxwarmpools.yaml` | Exact `SandboxWarmPool` v1alpha1 schema; confirmed `scale` subresource and `updateStrategy` enum. |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/extensions/examples/secure-sandboxtemplate.yaml` | Working example with `runtimeClassName: gvisor`, embedded `networkPolicy`. |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/extensions/examples/sandboxtemplate.yaml` | Working example with `volumeClaimTemplates`. |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/extensions/examples/sandboxwarmpool.yaml` | Minimal warm pool example. |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/examples/kata-gke-sandbox/README.md` | Kata + GKE constraints (N2 Intel, Ubuntu). |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/examples/kata-gke-sandbox/sandbox-kata-gke.yaml` | `runtimeClassName: kata-qemu` example. |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/examples/hpa-swp-scaling/README.md` | HPA-driven warm pool sizing using `agent_sandbox_claim_creation_total`. |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/examples/hpa-swp-scaling/sandboxwarmpool.yaml` + `hpa.yaml` | Concrete HPA targeting `SandboxWarmPool` via its scale subresource. |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/examples/composing-sandbox-nw-policies/README.md` | Confirms that composing additional `NetworkPolicy`/`Service`/`Ingress` around a Sandbox is a recommended pattern (e.g. via KRO). |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/clients/python/agentic-sandbox-client/README.md` | Connection modes (Gateway/Tunnel/InCluster/Direct), router setup, `[async]` extras, `pip install k8s-agent-sandbox`. |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/clients/python/agentic-sandbox-client/k8s_agent_sandbox/sandbox_client.py` | Concrete `SandboxClient.create_sandbox(...)` signature including `warmpool` and `shutdown_after_seconds`; `get_sandbox` reattach; `_create_claim` writes the SandboxClaim. |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/clients/python/agentic-sandbox-client/k8s_agent_sandbox/sandbox.py` | Per-sandbox handle: `commands`, `files`, `status()`, `terminate()`, `close_connection()`, pod-IP refresh on restart. |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/clients/python/agentic-sandbox-client/k8s_agent_sandbox/models.py` | Connection config + `ExecutionResult` shape. |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/clients/python/agentic-sandbox-client/k8s_agent_sandbox/__init__.py` | Top-level exports; `AsyncSandboxClient` requires `[async]` extras. |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/clients/python/agentic-sandbox-client/test_podsnapshot_extension.py` | Shows `PodSnapshotSandboxClient` from `gke_extensions.snapshots` is the *only* source of `suspend()`/`resume()`; core `Sandbox` has none. |
| `https://raw.githubusercontent.com/kubernetes-sigs/agent-sandbox/main/examples/python-sdk-quickstart/README.md` | Minimal SDK example; confirms router prerequisite for tunnel/gateway modes. |
| `https://api.github.com/repos/kubernetes-sigs/agent-sandbox/releases` | Release tag history: 11 releases between 2025-11-07 (v0.1.0) and 2026-05-14 (v0.4.6). |
| Release-body extracts (v0.4.3, v0.4.5, v0.4.6) from the same API call | v0.4.3 added `ttlSecondsAfterFinished` + `Finished` condition + `volumeClaimTemplates` on `SandboxTemplate` + `SandboxInClusterConnectionConfig`. v0.4.5 PodSnapshot graduated `v1alpha1`→`v1`. v0.4.6 `spec.service` opt-in (breaking) + direct pod-IP routing via `X-Sandbox-Pod-IP`. |
| Existing Centaur code: [`.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py`](../../../.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py) | Centaur already has a working `KubernetesAgentSandboxBackend` that writes `Sandbox` CRDs via `kubernetes_asyncio.client.CustomObjectsApi`, hardcoded to `agents.x-k8s.io/v1alpha1`, sends `replicas:1, service:false, shutdownPolicy:Retain`. Confirms the integration is real, not just a tweet. |
| Existing Centaur chart: [`.centaur/contrib/chart/charts/agent-sandbox/values.yaml`](../../../.centaur/contrib/chart/charts/agent-sandbox/values.yaml), [`_controller-args.tpl`](../../../.centaur/contrib/chart/charts/agent-sandbox/templates/_controller-args.tpl) | All controller CLI flags surfaced as Helm values; chart hardcodes `registry.k8s.io/agent-sandbox/agent-sandbox-controller` image, `tag` must be set by the deployment. |
