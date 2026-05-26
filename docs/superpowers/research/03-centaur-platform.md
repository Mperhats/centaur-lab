# Research: Centaur platform for BFTS-on-Centaur

> Source of truth: the vendored Centaur submodule at `.centaur/`. All non-trivial claims cite `path:line` in that tree. Read-only — nothing was modified. Where this conflicts with `.centaur/AGENTS.md` prose, the code wins.

## TL;DR

- The **workflow engine** Centaur ships is a real, in-API, Postgres-backed checkpoint/replay runtime modelled on Cloudflare Workflows. The `ctx.step` / `ctx.sleep` / `ctx.wait_for_event` / `ctx.start_workflow` / `ctx.wait_for_workflow` primitives the spec assumes all exist as named methods on `WorkflowContext` (`services/api/api/workflow_engine.py:220-696`). A BFTS tree controller fits this model cleanly.
- **Sandboxing today** has *two* shipped backends: a plain `Pod` backend (default) and an `agent-sandbox` CRD backend, selected by the `KUBERNETES_SANDBOX_CONTROLLER` env var (`services/api/api/sandbox/registry.py:24-36`). The agent-sandbox integration only uses the **core** `Sandbox` CRD — `SandboxClaim`/`SandboxTemplate`/`SandboxWarmPool` are bundled but the controller's `extensions: false` (`contrib/chart/values.yaml:148-149`). gVisor/Kata are *not* preconfigured; the chart exposes `runtimeClassName` but ships empty.
- **iron-proxy is real and shipped.** It is a per-sandbox MITM HTTPS proxy (vendored as `ironsh/iron-proxy:0.39.0`, `services/iron-proxy/Dockerfile:1`) that substitutes secrets on outbound requests, scoped by host + header/path. The spec's name is right, but its claim that iron-proxy provides "per-interaction credential scoping" is **roadmap, not shipped** (`docs/pages/secrets/advanced-permissioning.mdx:8-13`; today credentials are deployment-scoped).
- **Webhooks for the GPU callback work in two layers**, and the spec conflates them. `/api/webhooks/{slug}` *creates a new workflow run* per request (`services/api/api/routers/webhooks.py:155-229`). To *resume* an already-running workflow that called `ctx.wait_for_event`, the caller must POST to `/workflows/events` with `{event_type, correlation_id}` (`services/api/api/routers/workflows.py:171-187`, `services/api/api/workflow_engine.py:2755-2808`). The BFTS port will need the second path, optionally fronted by a `WEBHOOKS=[...]` slug for signature verification.
- **"Nightly reflection / skills as tunable hyperparameters" is not a Centaur primitive.** Skills are sandbox-loaded Markdown files in `.agents/skills/<name>/SKILL.md` mounted from the overlay (`docs/pages/extend/skills.mdx:1-67`). They are not edited by Centaur, not evaluated, not tied to a scheduler, and not connected to workflow inputs. The "outer loop" in the spec must be built (workflow with `SCHEDULE = {"cron": ...}` that writes new params somewhere the BFTS workflow reads).
- The overlay model and discovery the spec assumes (drop a workflow under `overlay/workflows/`, drop a tool under `overlay/tools/`, image-rebuild + `helm upgrade`, no fork) **is exactly how it works** (`contrib/chart/templates/workloads.yaml:192-215`, `.centaur/AGENTS.md:338-350`). This repo already does it for `overlay/tools/semantic_scholar` and `overlay/workflows/save_papers.py`.

## Workflow programming model

A workflow is a single Python file exporting `WORKFLOW_NAME: str` and `async def handler(params, ctx) -> Any`, optionally `Input` (dataclass) and `SCHEDULE` (dict). The handler IS the workflow; steps are runtime-discovered via `ctx.step(name, fn)`. On crash/suspension the handler re-runs top-to-bottom and `ctx.step` short-circuits on cached results.

**Real handler signature** (built-in agent_turn, `services/api/api/workflows/agent_turn.py:40-56`):

```python
async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    """Spawn -> message -> execute -> wait for a terminal agent result."""
    from api.workflow_engine import do_agent_turn
    thread_key = inp.thread_key.strip() or f"workflow:{ctx.run_id}:agent"
    return await do_agent_turn(ctx, thread_key=thread_key, ...)
```

**`WorkflowContext` API** (defined `services/api/api/workflow_engine.py:220-873`):

| Primitive | Signature | Where |
|---|---|---|
| `step(name, fn, *, retry, timeout)` | execute *fn* exactly once; cached result on replay | `workflow_engine.py:331-399` |
| `sleep(name, duration)` | suspend `duration`, no-op on replay if past | `workflow_engine.py:401-417` |
| `sleep_until(name, when)` | suspend until datetime | `workflow_engine.py:419-436` |
| `wait_for_event(name, *, event_type, correlation_id, timeout)` | suspend until matching event arrives via `POST /workflows/events`; returns the payload | `workflow_engine.py:453-544` |
| `start_workflow(name, *, workflow_name, run_input, trigger_key, eager_start)` | create child run, returns `{run_id, ...}` | `workflow_engine.py:546-578` |
| `wait_for_workflow(name, *, run_id, timeout)` | suspend until child reaches terminal | `workflow_engine.py:580-665` |
| `run_workflow(name, *, workflow_name, run_input, trigger_key, timeout, eager_start)` | `start` + `wait` in one call | `workflow_engine.py:667-696` |
| `start_agent(name, *, text=..., delivery=..., harness=..., persona=..., ...)` | shorthand: child `agent_turn` workflow | `workflow_engine.py:698-737` |
| `run_agent(...)` | start + wait for an `agent_turn` | `workflow_engine.py:739-780` |
| `agent_turn(prompt, **kwargs)` | run an agent turn inline (uses ctx.run_input defaults) | `workflow_engine.py:856-873` |
| `call_tool(tool, method, args)` | invoke an API tool, checkpointed | `workflow_engine.py:829-854` |
| `tools.<tool>.<method>(**kwargs)` | ergonomic proxy over `call_tool` | `workflow_engine.py:819-827, 876-903` |
| `post_to_slack(channel, text, *, thread_ts)` | checkpointed Slack post | `workflow_engine.py:782-817` |
| `log(msg, **kwargs)` | structured log, suppressed during replay | `workflow_engine.py:438-451` |

**Durability guarantees:**
- Each `ctx.step` checkpoint is a row in `workflow_checkpoints` written *atomically with a lease-fence check* against `workflow_runs.worker_id` (`workflow_engine.py:265-319`). Cancelled or lost-lease runs can't write stale checkpoints — they raise `CancelledWorkflow`.
- Workers hold a lease (`WORKFLOW_WORKER_LEASE_S`, default 30s, `workflow_engine.py:160-162`) extended by a heartbeat task every `lease_s/3` seconds (`workflow_engine.py:1064-1090`). On lease expiry another worker re-claims the run via `_requeue_expired_running_runs` (`workflow_engine.py:1107-1121`).
- `SuspendWorkflow` exception is the suspension mechanism. Steps raise it with `available_at`; the worker writes the row's `status` + `available_at` and returns (`workflow_engine.py:2455-2500`).
- Hot-loop guard: minimum `WORKFLOW_RESUSPEND_BACKOFF_S` (default 5s, `workflow_engine.py:178-180`) between re-claims.

**Step name auto-deduplication for loops:** `ctx.step("expand")` called N times resolves to `expand`, `expand#2`, `expand#3`, ... (`workflow_engine.py:251-263`). This means a BFTS loop body can repeatedly call `ctx.step("score_node", ...)` without name-mangling — the engine does it for you.

## Tool programming model

Tools live under a directory listed in `TOOL_DIRS` (colon-separated). Each tool is a directory with `client.py` (a class + `_client()` factory) and `pyproject.toml`. Methods starting with `_` are excluded from registration (`.centaur/AGENTS.md:308-316`).

**Discovery contract:** the chart sets `TOOL_DIRS` based on whether an overlay image is configured (`contrib/chart/templates/workloads.yaml:192-197`):

```
TOOL_DIRS=/app/tools                              # base only
TOOL_DIRS=/app/tools:/app/overlay/org/tools       # with overlay (later wins on name collision)
```

This means dropping a directory under `overlay/tools/bfts_runner/` containing `client.py` + `pyproject.toml` is the entire registration. Already proven by `overlay/tools/semantic_scholar` in this repo.

**Tool method shape** (real example, `tools/infra/demo/client.py:1-17`):

```python
class DemoClient:
    def ping(self) -> dict:
        return {"pong": True, "server_time": ..., "version": 4}

def _client() -> DemoClient:
    return DemoClient()
```

**Auth/secrets:** tools call `secret("KEY")` from `centaur_sdk.tool_sdk` (`centaur_sdk/tool_sdk.py:47-76`). Resolution order: thread-local `ToolContext.secrets` → pluggable backend (env/HTTP sidecar) → default. The real value never sits in the sandbox — iron-proxy substitutes it on outbound HTTPS based on `[tool.centaur].secrets` in `pyproject.toml`:

```toml
[tool.centaur]
module = "client.py"
secrets = [
    {type = "http", name = "EXA_API_KEY", match_headers = ["x-api-key"], hosts = ["api.exa.ai"]},
]
```

(`tools/research/websearch/pyproject.toml:20-25`)

**Calling a tool from a workflow:** `await ctx.call_tool("slack", "send_message", {...})` — checkpointed, exactly-once across replays (`workflow_engine.py:829-854`). Or via the proxy: `await ctx.tools.websearch.search(query="ETH price")`.

**Calling from a sandbox:** the agent shells out — `call <tool> <method> <json>` → `POST /tools/<tool>/<method>` (`.centaur/AGENTS.md:451-455`). Sandbox tokens are auto-issued HMAC tokens with `["agent", "tools:*"]` scope (`.centaur/AGENTS.md:506-510`).

## State & durability storage

Workflow state is **inline JSON in Postgres** — no separate blob store. Schema in `services/api/db/migrations/013_workflow_engine.sql:1-142`:

| Table | Holds | Notes |
|---|---|---|
| `workflow_runs` | one row per run: `input_json JSONB`, `output_json JSONB`, status, parent/root hierarchy, lease, timestamps | `migrations/013:3-25` |
| `workflow_checkpoints` | one row per `ctx.step` call: `state JSONB`, `step_kind`, optional `execution_id` / `child_run_id` foreign keys | `migrations/013:64-73` |
| `workflow_schedules` | cron/interval definitions | `migrations/013:90-112` |
| `workflow_events` | external events for `wait_for_event` correlation | `migrations/013:132-138` |

**Size implications for BFTS:**
- Postgres `JSONB` row hard ceiling is the page-bounded TOAST limit (~1GB compressed per row in practice; well-behaved JSONB tops out around tens of MB before query latency degrades). The engine writes the whole step result as one JSONB blob — `canonical_json(value)` (`workflow_engine.py:291-306`).
- There is **no per-step blob/object store and no streaming append** — each `ctx.step("expand_node", ...)` call writes one fresh row whose `state` is the *whole return value*. For BFTS this means:
  - The **tree itself should NOT live as a single growing JSON blob** stored under one checkpoint that gets re-written each iteration (that would re-serialize the full tree per step). Instead, append one checkpoint per node expansion (auto-numbered: `expand_node`, `expand_node#2`, ...), and store **only the per-node result** in each. Reconstruct the tree at replay by reading all `expand_node*` checkpoints (`workflow_engine.py:2313-2325` already bulk-loads them into a dict).
  - Or: store the tree in a **separate, BFTS-port-owned table** (one row per node, like the existing `muesli_meetings` table in `workflows/muesli_meeting_ingest.py:60-103`) and only checkpoint a `{node_id}` pointer. This is the clean path and matches how `slack_backfill.py:186-461` uses checkpoints as cursors while bulk data lives in dedicated tables.
- Webhook bodies are capped at 1 MB (`_MAX_WEBHOOK_BODY_BYTES = 1024 * 1024`, `services/api/api/routers/webhooks.py:36`).
- Workflow run input/output are JSONB with no explicit cap, but `canonical_json` is hashed for idempotency — keep run inputs small.

**Recommendation for the spec:** the BFTS tree is the state, and a 1000-node tree with embedded code+stdout per node is *not* fine inline. The port should own a `bfts_nodes` table in an overlay migration and checkpoint only IDs.

## Child workflow / fan-out

First-class. The mapping is exactly what the spec assumes:

```python
# Fire-and-forget N children
children = []
for i in range(num_drafts):
    child = await ctx.start_workflow(
        f"draft_{i}",
        workflow_name="bfts_node",
        run_input={"seed_idx": i, ...},
        eager_start=True,
    )
    children.append(child["run_id"])

# Await all
results = []
for i, run_id in enumerate(children):
    res = await ctx.wait_for_workflow(f"draft_{i}.wait", run_id=run_id)
    results.append(res)
```

(`workflow_engine.py:546-696`; the `eager_start=True` flag bypasses the polling delay by calling `_execute_run(pool, run_id)` directly — `workflow_engine.py:2201-2204`.)

**Concurrency caps:**
- `WORKFLOW_WORKER_CONCURRENCY` (default 2, `workflow_engine.py:157-159`) is the global cap on *workflow* runs executing concurrently per API replica. **This is small by default — bump it via the env var for fan-out-heavy BFTS work.**
- `EXECUTION_WORKER_CONCURRENCY` (default 128, `services/api/api/runtime_control.py:79-93`) caps *agent executions* (sandbox-driven), separately. With user-slot reservations, the workflow pool can use up to `128 - EXECUTION_RESERVED_USER_SLOTS`.
- Both are per-API-pod; horizontal-scale the API to scale fan-out.

**Cancel semantics:** cancelling a parent cascades to in-progress children — `cancel_workflow_run` walks all `workflow_checkpoints.execution_id` foreign keys and calls `cancel_execution` for each (`workflow_engine.py:2217-2265`). Child workflow runs created via `child_workflow_start` checkpoints get woken on parent terminal via `notify_workflow_run_terminal` (`workflow_engine.py:2728-2752`).

**Partial-failure semantics:** if a child fails, `wait_for_workflow` returns the child's `{status: "failed", ...}` row — it does NOT raise. The parent decides what to do (`workflow_engine.py:586-624`). Good for BFTS: a failed node-expansion is one row in the tree, not a workflow-level abort.

## Webhooks (`/api/webhooks/{slug}`)

**What it does:** A webhook *creates a new workflow run* every time it fires (signature-verified, idempotent by trigger key). It does NOT resume an existing run. The whole-request envelope (method, path, headers, query, body, raw_body_sha256, source_ip) is the run input under the `"webhook"` key (`services/api/api/routers/webhooks.py:155-229`).

**Wiring** (real example, `workflows/github_issue_triage.py:28-37`):

```python
WEBHOOKS = [
    {
        "slug": "github-issue-triage",
        "provider": "github",
        "auth": {"type": "github", "secret_ref": "GITHUB_WEBHOOK_SECRET"},
        "trigger_key": {"type": "header", "header": "X-GitHub-Delivery"},
        "allowed_methods": ["POST"],
        "allowed_content_types": ["application/json", "application/x-www-form-urlencoded"],
    }
]
```

**Auth supported** (`services/api/api/webhooks.py:22-46`):
- `"none"` (dev only)
- `HmacAuth(...)` — generic sha256 HMAC, configurable header name, hex or base64 encoding
- `HmacAuth.github(secret_ref=...)` — convenience: `X-Hub-Signature-256`, `sha256=` prefix
- Slug is reserved for routing (`slack` is taken); `[a-z0-9][a-z0-9._-]{0,127}` is the format

**Idempotency:** if `trigger_key` is set, repeated deliveries with the same key short-circuit to the existing run (`workflow_engine.py:1842-1894`). Default falls back to `webhook:{slug}:{raw_body_sha256}` (`routers/webhooks.py:94-107`).

**For the BFTS GPU-callback pattern:**
1. The BFTS workflow runs `ctx.wait_for_event(name, event_type="gpu_done", correlation_id=job_id)` (suspends; writes a wait-marker checkpoint, `workflow_engine.py:522-544`).
2. External GPU process POSTs to **`/workflows/events`** (not `/api/webhooks/{slug}`) with `{event_type: "gpu_done", correlation_id: job_id, payload: {...}}`. That endpoint is API-key-gated (requires `agent:execute` or `admin` scope, `routers/workflows.py:171-187`).
3. `send_workflow_event` upserts the row and wakes waiting runs via `available_at = NOW()` (`workflow_engine.py:2755-2808`).

If the BFTS port wants HMAC verification on the GPU callback, the cleanest pattern is a tiny relay workflow with `WEBHOOKS = [...]` whose handler validates `correlation_id` from the body and calls `send_workflow_event` (or directly inserts into `workflow_events`). The two routes serve different purposes — using `/api/webhooks/{slug}` directly to "resume the BFTS run" would create a fresh workflow run, not wake the waiting one.

## Sandboxing — current state of upstream

**Two backends, both shipped, switchable via `KUBERNETES_SANDBOX_CONTROLLER` env var** (`services/api/api/sandbox/registry.py:24-36`):

| Controller value | Backend class | Behavior |
|---|---|---|
| `"pod"` (default) | `KubernetesExecutorBackend` (`services/api/api/sandbox/kubernetes.py:405`) | Creates raw `Pod` per sandbox |
| `"agent-sandbox"` / `"agentsandbox"` | `KubernetesAgentSandboxBackend` (`services/api/api/sandbox/kubernetes_agent_sandbox.py:37`) | Creates `agents.x-k8s.io/v1alpha1 Sandbox` CRs |

Helm: `.Values.sandbox.controller` (default `"pod"`, `contrib/chart/values.yaml:101`) is rendered into the API env at `contrib/chart/templates/workloads.yaml:308-309`. The agent-sandbox controller chart is a subchart that installs only when `agentSandbox.enabled=true` (`contrib/chart/values.yaml:139-149`), pinned at `registry.k8s.io/agent-sandbox/agent-sandbox-controller:v0.4.6`.

**What the agent-sandbox backend actually uses** (`kubernetes_agent_sandbox.py:109-185`):
- `Sandbox` CR with `spec.replicas` (0 or 1), `spec.shutdownPolicy: Retain`, `spec.podTemplate` from the API-built pod spec.
- Optional `volumeClaimTemplates` for a per-sandbox state PVC at `/home/agent/state`, gated by `KUBERNETES_SANDBOX_STATE_VOLUME_ENABLED` and sized by `KUBERNETES_SANDBOX_STATE_VOLUME_SIZE` (default `10Gi`, `kubernetes_agent_sandbox.py:20-31`).
- **Hibernate/resume is implemented**: `pause_by_id` patches `replicas: 0`; `resume_by_id` patches back to `1` and waits ready (`kubernetes_agent_sandbox.py:159-185`).

**What's NOT used:**
- `SandboxClaim`, `SandboxTemplate`, `SandboxWarmPool` CRDs are bundled in `contrib/chart/charts/agent-sandbox/crds/` and shipped, but the chart sets `agentSandbox.controller.extensions: false` (`contrib/chart/values.yaml:148-149`). Centaur's own `KubernetesAgentSandboxBackend` creates raw `Sandbox` CRs directly, not via `SandboxClaim`.
- `runtimeClassName` is plumbed via `KUBERNETES_SANDBOX_RUNTIME_CLASS_NAME` (`kubernetes.py:91-93`) and exposed in the chart as `sandbox.runtimeClassName` (default empty, `contrib/chart/values.yaml:106`). gVisor/Kata are **not pre-wired** — you set this env to `gvisor` or `kata-qemu` only if you've installed those `RuntimeClass`es out-of-band on the cluster.

**Centaur's own warm pool** is independent of `SandboxWarmPool`. It's a Python loop in `services/api/api/warm_pool.py:1-100` that pre-creates `WARM_POOL_SIZE` (default 5) sandboxes for a single `WARM_POOL_HARNESS` and adopts them on claim. This is the "warm pool" the chart's `warmPoolEnabled` flag controls (`values.yaml:59`). Off by default in `values.local.yaml:25`.

**Bottom line for the spec:**
- The maintainer-tweet claim that "Centaur has adopted agent-sandbox" is **confirmed in code** (registry + backend file exist). But the integration is shallow — `Sandbox` CR only, not the extensions. `SandboxTemplate` / `SandboxClaim` / `SandboxWarmPool` patterns the spec hopes to use are **CRDs-on-disk but not wired**; either turn on `extensions: true` and write our own controller glue, or pre-create the resources from the BFTS workflow.
- Hibernate/resume on a `Sandbox` works today (replicas 0/1 toggle).
- gVisor / Kata require operator-level cluster prep before `sandbox.runtimeClassName` can do anything.

## Execution worker

Two worker pools share the API process:

| Pool | What it does | Cap | Source |
|---|---|---|---|
| **Workflow worker** | Drives `handler(...)` for queued/waiting/sleeping runs | `WORKFLOW_WORKER_CONCURRENCY` (default 2) | `workflow_engine.py:2811-2853, 157-159` |
| **Execution worker** | Drives agent sandbox sessions for one `agent_execute` request | `EXECUTION_WORKER_CONCURRENCY` (default 128) | `runtime_control.py:3171-3204, 79-93` |

Both poll Postgres with `FOR UPDATE SKIP LOCKED` claim loops and hold leases (workflow: 30s, execution: 60s).

**`api.executionWorkerEnabled`** Helm value gates the execution worker (`contrib/chart/values.yaml:56`); the workflow worker is gated by `api.workflowWorkerEnabled` (default true, `contrib/chart/values.yaml:58`).

The workflow worker runs **in-API**. There is no separate `execution-worker` deployment — the chart's `workloads.yaml` defines one API workload, and both worker loops run as `asyncio.create_task` inside its FastAPI app startup. To scale workflow throughput, scale the API replica count (`api.replicaCount`, default 1).

## Secrets / iron-proxy

iron-proxy is a **per-sandbox** MITM HTTPS proxy. Architecture:

1. Tool declares `[tool.centaur].secrets = [...]` in `pyproject.toml` with `{type, name, match_headers, hosts}` shape (real example: `tools/research/websearch/pyproject.toml:20-25`).
2. When the API spawns a sandbox, it ALSO spawns a dedicated `centaur-iron-proxy` pod alongside it (`services/api/api/sandbox/kubernetes.py:299-329` builds proxy/policy/configmap resource names per-sandbox; the actual builders are below in the same file).
3. The sandbox's outbound HTTPS goes through `HTTPS_PROXY=http://centaur-centaur-proxy-<sbx>:8080` (`.centaur/AGENTS.md:466`); iron-proxy MITMs, looks up `{host, header}` in its substitution map, swaps placeholder name → real value, forwards (`docs/pages/security.mdx:76-115`).
4. Real secrets resolved by iron-proxy from one of: env vars (`secretSource: env`), 1Password (`secretSource: onepassword`), 1Password Connect, or `op://...` refs (`contrib/chart/values.yaml:32-33`; this repo uses `env`, `values.local.yaml:8`).
5. NetworkPolicy: sandbox pod can only reach API and its own iron-proxy pod; nothing else (`docs/pages/security.mdx:44-57`). Per-sandbox proxies mean cross-sandbox blast radius is zero.
6. `pg_dsn`, `oauth_token`, `gcp_auth` are typed secret variants — for `pg_dsn`, iron-proxy opens a Postgres listener and the sandbox gets a local DSN (`docs/pages/security.mdx:109-114`).

**What it does NOT do today** (spec claim correction):
- Per-user / per-channel scoping is **roadmap**, not shipped. `docs/pages/secrets/advanced-permissioning.mdx:1-13` literally marks it "🚧 WIP". Today every sandbox in a deployment gets the same secret set.
- The egress allowlist defaults to `domains: ["*"]` (`services/iron-proxy/iron-proxy.yaml:17-21`). To lock it down for BFTS (spec's "lock the egress allowlist to the model provider, the experiment data sources, and the external compute callback host"), edit `services/iron-proxy/iron-proxy.yaml:18-21` and replace `"*"` with the explicit host list (`docs/pages/security.mdx:54-74`). This means the BFTS port can't be 100% overlay — locking the allowlist needs either editing the upstream config or a Helm escape hatch that doesn't currently exist in `values.yaml`.

## Outer loop / skills / nightly reflection

**Skills:** Markdown files at `<overlay>/.agents/skills/<name>/SKILL.md` with optional `references/`, `scripts/`, `examples/`. The sandbox entrypoint copies them into the agent workspace at startup; the agent's harness (Claude Code etc.) loads them as workspace skills (`docs/pages/extend/skills.mdx:1-67`; real example: `.centaur/.agents/skills/improve-gap-task/SKILL.md`). Front-matter is `name:` + `description:` only.

**Skills are not editable from inside Centaur**, not scored, not connected to scheduling, not connected to workflow inputs, and not tunable parameters. They are agent-side prompt fragments. The spec's framing of "search hyperparameters as a tunable skill" is **a category mismatch with the shipped skill system**.

**Nightly scheduling exists** as the workflow `SCHEDULE` export — a workflow file can declare `SCHEDULE = {"cron": "45 7 * * *", ...}` and the engine syncs it into `workflow_schedules` and ticks it (real example: `workflows/paradigm_pulse_daily.py:16` uses `CRON = "45 7 * * *"`; engine syncs at `workflow_engine.py:2607-2678` and `workflow_engine.py:2876-2974`). This is the only "nightly" primitive Centaur ships.

**No `self_improve_daily.py` ships in `.centaur/`.** Only the *tests* are present (`services/api/tests/test_self_improve_daily.py:12`); the workflow file itself is an overlay artifact in Paradigm's internal deployment. The pattern (a scheduled workflow that reads recent runs from `workflow_runs`, scores them, opens PRs) is reproducible, but not turn-key.

**For the BFTS outer loop**, the realistic shape is:
- A scheduled workflow `bfts_reflection_nightly` with `SCHEDULE = {"cron": "0 3 * * *"}` that reads recent `bfts_root` runs from `workflow_runs` (filtered by `workflow_name`), summarizes their tree shape and best-node metrics, picks new `{debug_prob, expansion_policy_weights, ...}` values, and writes them to a tiny overlay table (e.g. `bfts_hyperparams`) keyed by effective-from date.
- The main `bfts_root` workflow reads the latest row from that table at start.
- No skills involved — this is workflow-on-workflow.

## Overlay integration contract

The same overlay image is **mounted in two places** with two different roles:

| Mount | Used for | Discovery env vars |
|---|---|---|
| `/app/overlay/org` (API pod) | tool discovery, workflow discovery, API-side prompt assembly | `TOOL_DIRS`, `WORKFLOW_DIRS`, `CENTAUR_OVERLAY_DIR` |
| `/home/agent/overlay/org` (sandbox pod) | skills, persona prompts, sandbox-side `SYSTEM_PROMPT.md` overlay | `CENTAUR_OVERLAY_DIR` |

Both mounts are populated by an init container (`overlay-bootstrap`) that copies `$sourcePath` from the overlay image into an `emptyDir` volume (`contrib/chart/templates/workloads.yaml:134-150`). The chart wires it conditionally on `.Values.overlay.image.repository` being set.

**Concrete layout** (this repo's `overlay/`, see `AGENTS.md:25-46` and `docs/pages/extend/overlay.mdx:19-35`):

```
overlay/
├── Dockerfile           # FROM alpine + WORKDIR /overlay + COPY . /overlay
├── tools/
│   └── <tool_name>/
│       ├── client.py
│       └── pyproject.toml
├── workflows/
│   └── <workflow_name>.py
└── .agents/
    └── skills/
        └── <skill_name>/SKILL.md
```

**Helm values** (`values.local.yaml:71-84`):

```yaml
overlay:
  image:
    repository: centaur-overlay
    tag: latest
    pullPolicy: IfNotPresent
    sourcePath: /overlay      # implicit default; what the init container copies from
```

Then when `overlay.image.repository` is set, chart automatically:
- Appends overlay tools to `TOOL_DIRS` and workflows to `WORKFLOW_DIRS` (`workloads.yaml:192-203`)
- Sets `CENTAUR_OVERLAY_DIR=/app/overlay/org` on API (`workloads.yaml:204-207`)
- Passes `CENTAUR_OVERLAY_IMAGE` + pull policy + source path to the API so the API knows how to mount the same overlay onto each sandbox it spawns (`workloads.yaml:208-215`)

**Later overlays win on name collision** — this is the documented model for shadowing a base tool/workflow (`.centaur/AGENTS.md:350`).

## Local dev loop

**`just up` is a 3-step orchestration** (this repo's `Justfile:18-22`):

1. `just bootstrap-secrets` — runs `.centaur/just bootstrap-secrets` to create the `centaur-infra-env` k8s Secret, then patches in `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `SLACK_ETL_TOKEN`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_TOKEN`, `SEMANTIC_SCHOLAR_API_KEY`, `LOCAL_DEV_API_KEY` from `.env` (`Justfile:36-61`).
2. `cd .centaur && just build` — `docker build` for `centaur-api`, `centaur-iron-proxy`, `centaur-slackbot`, `centaur-agent` in parallel (`.centaur/Justfile:11-56`).
3. `just overlay::build` — `docker build` for `centaur-overlay:latest`.
4. `just deploy` — `helm upgrade --install` with `.centaur/contrib/chart/values.dev.yaml` + `values.local.yaml` (`Justfile:24-33`).

**Iterating on workflows / tools without a full redeploy:**
- Per `.centaur/AGENTS.md:309` and `:336`: tools and workflows are **auto-discovered + hot-reloaded** on file changes (the chart sets `api.pluginWatcherEnabled: true` in `values.dev.yaml:22`). But this hot-reload watches the API container's filesystem; the overlay sits inside an `emptyDir` populated at init time, so editing `overlay/workflows/bfts_root.py` on your host does NOT propagate without `just overlay::build && just deploy` to rebuild the image and re-pod.
- For tools, `.centaur/AGENTS.md:267` says "tools hot-reload, so just verify via curl from inside the API deployment" — but again only if the file is on the running API's filesystem. The development cycle in practice is: edit → `just overlay::build` → `just deploy` (a rolling restart of the API pod).
- Lower-friction alternatives: (1) `kubectl cp` the changed file into the API pod and let the plugin watcher pick it up; (2) make the overlay a hostPath mount instead of an image. Neither is wired today.

For BFTS, the spec's workflow file is the hot edit; expect 30-90s per iteration through the rebuild loop. Faster iteration argues for splitting tree-search logic into a pure-Python module (testable with `pytest` locally) and keeping the workflow handler a thin shell.

## Closest existing analogues

For the BFTS port, the closest workflow templates are:

**1. `workflows/github_issue_triage.py` (`workflows/github_issue_triage.py:1-156`)** — Webhook-triggered workflow with HMAC auth, header trigger key, normalized envelope parsing, conditional skip, and a single `ctx.agent_turn(...)` call. Shows the canonical webhook → workflow → agent pattern. The BFTS GPU-callback should mirror its `WEBHOOKS = [...]` declaration and its envelope-parsing helpers.

**2. `workflows/muesli_meeting_ingest.py` (`workflows/muesli_meeting_ingest.py:1-137`)** — Workflow that owns its **own database table** (`muesli_meetings`) via raw `ctx._pool` SQL, uses `ctx.step("persist_meeting", ...)` for the side-effecting upsert, then `ctx.post_to_slack(...)`. This is the template for the BFTS workflow owning a `bfts_nodes` / `bfts_runs` table and keeping checkpoints small.

**3. `workflows/slack_backfill.py` (`workflows/slack_backfill.py:1-461`)** — A long-running workflow that claims a batch of work items from a queue table, iterates them in a `for` loop, enqueues follow-up jobs, records per-iteration metrics, and survives mid-loop restart by re-claiming jobs from the DB. **This is the closest in shape to a BFTS controller**: it's a `SCHEDULE`-driven scheduler over a table, not over a checkpoint blob, exactly the right pattern for a fan-out search.

For the tool template, the cleanest analogues are:

**1. `tools/research/websearch/` (`tools/research/websearch/client.py:1-120`, `pyproject.toml:1-25`)** — Wraps two external HTTPS APIs (Exa + Anthropic), declares both as scoped HTTP secrets in `pyproject.toml`. This is the model for a BFTS scoring tool that calls an external VLM.

**2. `tools/infra/profslice/` (`tools/infra/profslice/client.py:1-60`)** — Pure-compute tool with no external API: parses a profile, returns structured data. This is the closest to a BFTS "wrap an internal CRD/exec" tool that talks to the Kubernetes API to create a `Sandbox` for a node experiment.

**3. `tools/infra/demo/` (`tools/infra/demo/client.py:1-17`, `pyproject.toml:1-10`)** — Minimal tool with no secrets, no deps. The right starting skeleton.

For the agent-sandbox lifecycle, there is no overlay-side analogue — `api/sandbox/kubernetes_agent_sandbox.py` is the only file in the repo that creates `Sandbox` CRs, and it's an API-internal backend, not a tool or workflow. The BFTS port either drives the existing `sandbox.controller=agent-sandbox` backend through the standard `do_agent_turn` workflow path (each BFTS node spawns an agent turn = one Sandbox via the existing path) OR writes a new tool that talks directly to the `agents.x-k8s.io/v1alpha1` Kubernetes API.

## Capability matrix vs. the spec

| Spec claim | Confirmed / partial / wrong | Source |
|---|---|---|
| Durable workflow with checkpointed state in Postgres | **Confirmed** | `services/api/api/workflow_engine.py:220-417`; `db/migrations/013_workflow_engine.sql:1-142` |
| `ctx.step` / `ctx.wait_for_event` primitives | **Confirmed (exact names)** | `workflow_engine.py:331-399, 453-544` |
| Child workflows + fan-out (`num_drafts` → N children) | **Confirmed** | `workflow_engine.py:546-696` |
| Workflow survives worker restart, resumes from last checkpoint | **Confirmed** | `workflow_engine.py:1107-1121, 2313-2325, 2455-2500` |
| `/api/webhooks/{slug}` resumes a waiting workflow run | **Wrong** — that endpoint *creates* a new run. To resume, use `POST /workflows/events` (which `wait_for_event` listens for). | `routers/webhooks.py:155-229` vs. `routers/workflows.py:171-187`, `workflow_engine.py:2755-2808` |
| Webhook is signed (HMAC) | **Confirmed** — `HmacAuth` + `HmacAuth.github`, sha256 only | `services/api/api/webhooks.py:22-138` |
| Webhook replay protection | **Partial** — idempotency by trigger_key (or body hash), but accepted body up to 1 MB then dropped | `routers/webhooks.py:36-37`, `workflow_engine.py:1862-1894` |
| Centaur adopted agent-sandbox | **Confirmed** — `KubernetesAgentSandboxBackend` is in-tree | `services/api/api/sandbox/kubernetes_agent_sandbox.py:1-217`; `services/api/api/sandbox/registry.py:24-36` |
| `Sandbox` CRD usage | **Confirmed** | `kubernetes_agent_sandbox.py:109-185` |
| `SandboxTemplate` / `SandboxClaim` / `SandboxWarmPool` first-class | **Partial** — CRDs ship in the bundled chart but `extensions: false` by default; code does not use them. Have to flip the flag and build glue, or pre-create resources manually. | `contrib/chart/values.yaml:148-149`; `kubernetes_agent_sandbox.py` (no references to those CRDs) |
| Sandbox hibernate/resume (replicas 0 ↔ 1) | **Confirmed** | `kubernetes_agent_sandbox.py:159-185` |
| Sandbox PVC for per-node state | **Confirmed** — `state-<sandbox_id>` PVC mounted at `/home/agent/state` when `KUBERNETES_SANDBOX_STATE_VOLUME_ENABLED=1` | `kubernetes_agent_sandbox.py:20-31, 87-95, 97-103, 123-136` |
| gVisor / Kata "by default" | **Wrong** — `runtimeClassName` is plumbed but defaults empty. Operator must install gVisor/Kata RuntimeClasses out-of-band. | `kubernetes.py:91-93`; `contrib/chart/values.yaml:106` |
| iron-proxy is a thing | **Confirmed** — vendored as `ironsh/iron-proxy:0.39.0` | `services/iron-proxy/Dockerfile:1` |
| iron-proxy per-interaction credential scoping | **Wrong (today)** — roadmap. Today deployment-scoped. Spec already says "still deployment-scoped" but then attributes the future fix to iron-proxy specifically, which matches the roadmap (`docs/pages/secrets/advanced-permissioning.mdx:1-101`). | `docs/pages/secrets/advanced-permissioning.mdx:8-13`; `docs/pages/security.mdx:128-139` |
| iron-proxy egress allowlist | **Partial** — exists and is enforced, but default is `["*"]`; locking down requires editing `services/iron-proxy/iron-proxy.yaml:17-21`, not a chart value | `services/iron-proxy/iron-proxy.yaml:17-21`; `docs/pages/security.mdx:54-74` |
| Nightly reflection workflow | **Partial** — workflow `SCHEDULE` exists; no built-in "reflection" workflow ships in `.centaur/`. Must be built. | `workflow_engine.py:1621-1754`; absence of `self_improve_daily.py` in `.centaur/workflows/` |
| Skills as tunable hyperparameters | **Wrong (category mismatch)** — skills are static Markdown prompt fragments loaded by the sandbox harness, not parameters editable by reflection | `docs/pages/extend/skills.mdx:1-67` |
| Overlay model with auto-discovery | **Confirmed exactly** — drop file under `overlay/{tools,workflows}/`, rebuild image, `just deploy` | `.centaur/AGENTS.md:338-350`; `contrib/chart/templates/workloads.yaml:192-215` |
| Tool calls in workflows are checkpointed | **Confirmed** | `workflow_engine.py:829-854` (`call_tool` wraps `tool_manager.call_tool` in a `ctx.step`) |
| `WORKFLOW_WORKER_CONCURRENCY` default | **2** — very small. Bump for BFTS. | `workflow_engine.py:157-159` |
| `EXECUTION_WORKER_CONCURRENCY` default | **128** | `runtime_control.py:79-93` |

## Gotchas

- **Workflow worker default concurrency is 2.** A BFTS run that fans out to `num_drafts=5` child workflows will starve itself on a single API replica unless you set `WORKFLOW_WORKER_CONCURRENCY=16` (or higher) in the chart's `api.extraEnv`. The execution worker (sandbox-side) has 128 slots, so the bottleneck is workflow-side.
- **Checkpoint state is a per-step JSONB row.** Do not store the BFTS tree as one growing blob. Either checkpoint per-node-expansion (auto-numbered names like `expand_node`, `expand_node#2`, ...) or own a `bfts_nodes` overlay table and checkpoint only IDs. Avoiding rewrite-the-tree-each-step is a correctness AND perf concern.
- **`/api/webhooks/{slug}` ≠ `wait_for_event` resume.** They are different endpoints with different semantics. The webhook endpoint CREATES a workflow run; the events endpoint WAKES one. The spec phrases the GPU callback as if it's a webhook; it's actually an event POST. If you want HMAC verification, build a tiny relay workflow that owns the webhook slug and forwards into `send_workflow_event`.
- **gVisor / Kata are not preinstalled.** The chart sets `sandbox.runtimeClassName: ""`. The spec's "gVisor by default, Kata for least-trusted" requires installing `RuntimeClass`es on the cluster and editing `values.local.yaml`. On Docker Desktop (this repo's local target) neither is available — for local dev, BFTS will run on shared-kernel namespacing only.
- **iron-proxy egress allowlist defaults open (`"*"`).** The spec's "lock the egress allowlist" requires editing `services/iron-proxy/iron-proxy.yaml` in the upstream submodule, which violates this repo's "never edit `.centaur/`" rule. Options: (a) carry a tiny chart-template patch; (b) get a `values.yaml` knob upstreamed; (c) layer a sidecar config file via overlay. Plan accordingly.
- **`SandboxWarmPool` / `SandboxTemplate` CRDs ship but are unused.** If the BFTS port wants `SandboxClaim`-from-warm-pool latency, it must flip `agentSandbox.controller.extensions: true` in the chart AND extend `KubernetesAgentSandboxBackend` (or write a tool that drives those CRDs). Today, Centaur's own warm pool spawns `Sandbox` resources by ID, not by claim.
- **Skills are not parameters.** The "outer loop tunes a skill" framing in the spec needs to be reformulated as "outer loop writes a `bfts_hyperparams` row that the BFTS workflow reads at start." A skill markdown file can document the *policy*, but cannot be the *parameter store*.
- **Task ordering: overlay image must be built before the first workflow registers.** `just up` already chains `bootstrap-secrets → centaur build → overlay::build → deploy`, but if you `helm upgrade` without rebuilding the overlay, the API will hot-discover a stale workflow file (`workflow_engine.py:1568-1612` re-runs discovery on plugin watcher events). Stick to `just up` to avoid drift.
- **`Input` dataclass coercion is best-effort, not strict.** `_coerce_input` (`workflow_engine.py:1463-1477`) silently logs and falls through to a raw dict on TypeError. Pydantic-style validation requires a `__post_init__` raise inside the dataclass, or hand-rolled checks at top of `handler`. Useful for BFTS to do its own validation early.
- **Webhook body cap is 1 MB.** GPU result payloads with large metric blobs need to be uploaded out-of-band (S3, an attachments tool) and referenced by ID in the event payload.

## Integration proposal

Concrete shape of the BFTS port inside Centaur, layered cleanly into `overlay/`:

```
overlay/
├── Dockerfile                      # existing
├── workflows/
│   ├── bfts_root.py                # SCHEDULE-launchable; fans out num_drafts children
│   ├── bfts_tree.py                # one tree controller; owns the search loop
│   ├── bfts_node.py                # one node-expansion; runs an agent turn in a Sandbox
│   ├── bfts_gpu_callback.py        # WEBHOOKS=[...]; verifies HMAC, sends workflow event
│   └── bfts_reflection_nightly.py  # SCHEDULE={"cron": "0 3 * * *"}; tunes hyperparams
├── tools/
│   ├── bfts_sandbox/               # creates/claims agent-sandbox Sandboxes for nodes (if we go beyond stock agent_turn)
│   │   ├── client.py
│   │   └── pyproject.toml
│   └── bfts_metrics/               # reads node experiment_data, computes objective
│       ├── client.py
│       └── pyproject.toml
└── .agents/
    └── skills/
        ├── bfts-proposer/SKILL.md
        ├── bfts-debugger/SKILL.md
        └── bfts-reviewer/SKILL.md  # documents per-turn personas
```

**Tree-state strategy:** own a `bfts_nodes` table (added via `./scripts/dbmate --set overlay new add_bfts_nodes` per `.centaur/AGENTS.md:32-40`) with columns `(tree_run_id, node_id, parent_id, code, metric, status, created_at)`. Each `ctx.step("expand", ...)` in `bfts_tree.py` does `INSERT` + returns the new node_id. Checkpoints stay tiny.

**GPU callback flow (corrected from spec):**
1. `bfts_tree.py` enqueues a GPU job (call to external scheduler/sidecar), then `await ctx.wait_for_event("gpu_done", event_type="gpu_done", correlation_id=job_id, timeout=timedelta(hours=2))`.
2. External GPU worker finishes, POSTs to `/api/webhooks/bfts-gpu-done` with HMAC signature, body `{job_id, metrics_url, status}`.
3. `bfts_gpu_callback.py` workflow runs, validates payload, calls `send_workflow_event(pool, event_type="gpu_done", correlation_id=job_id, payload={...})`.
4. `bfts_tree.py` wakes up with the payload.

**Helm overlay edits in `values.local.yaml`:**

```yaml
api:
  extraEnv:
    WORKFLOW_WORKER_CONCURRENCY: "16"     # BFTS fan-out

sandbox:
  controller: agent-sandbox               # opt into the CRD backend
  stateVolume:
    enabled: true                         # per-node PVC for experiment_data.npy etc
    size: 20Gi
  runtimeClassName: ""                    # set to "gvisor" once installed cluster-side

agentSandbox:
  enabled: true                           # install the controller subchart
  # controller:
  #   extensions: true                    # only if/when we wire SandboxTemplate/Claim/WarmPool
```

**Outer-loop "skill" reformulation:** drop a `bfts_hyperparams` overlay table:
```sql
CREATE TABLE bfts_hyperparams (
    effective_from TIMESTAMPTZ PRIMARY KEY,
    debug_prob FLOAT NOT NULL,
    max_debug_depth INT NOT NULL,
    expansion_policy_weights JSONB NOT NULL,
    notes TEXT
);
```
`bfts_root.py` reads the latest row at start; `bfts_reflection_nightly.py` writes new rows. The skills directory holds policy documentation for the agent personas, not the parameters.

## Open questions for the master plan

1. **Are we using the existing `agent_turn` workflow for node expansion, or writing a custom Sandbox path?** Stock `agent_turn` is simple and gives us hibernate/resume for free via the `agent-sandbox` backend, but it lifts a full agent harness (Claude Code etc.) per node. A custom `bfts_node` workflow + `bfts_sandbox` tool could spawn a leaner Sandbox running just the experiment code, skipping the harness.
2. **Where does the GPU job actually run?** The spec defers the compute-provisioning problem. The BFTS port needs at minimum: an external service URL + secret to enqueue against, an HMAC secret to validate the callback. Pick one (k8s Job on a GPU nodepool? external API like RunPod/Modal? in-cluster Sandbox with `nodeSelector: nvidia.com/gpu`?).
3. **Do we lock egress at the overlay level (impossible today without editing `.centaur/`) or accept the open default for now?** Punting risks credential exfiltration via adversarial agent output, but locking it down forces a fork-or-patch of `services/iron-proxy/iron-proxy.yaml`.
4. **Are we accepting `WORKFLOW_WORKER_CONCURRENCY=2 → 16` as enough, or do we need horizontal API scaling?** A single replica with 16 workflow slots means at most 16 in-flight nodes per replica. For deeper trees we'll want `api.replicaCount=2..3` plus Postgres connection-pool sizing (not investigated here).
5. **Skill-loading semantics for the 3 personas:** SYSTEM_PROMPT.md says skills are loaded by the harness (Claude Code reads workspace AGENTS.md). The spec talks about "persona + skill applied per agent turn." Confirm whether overriding `agents_md_override` per `ctx.start_agent` call is the right swap point, or whether we need a custom prompt-assembly path.
6. **Do we want a Sakana-style `manager.pkl` analogue** (the curriculum manager across the 4 stages)? The spec only ports phase 5 (the search itself) but the master plan should explicitly state whether we keep multi-stage curriculum or collapse to a single search.

## Sources

- `.centaur/services/api/api/workflow_engine.py:1-2974` — full workflow engine: context, primitives, suspension, scheduler, sync, child runs, events
- `.centaur/services/api/api/workflows/agent_turn.py:1-56` — built-in workflow shape
- `.centaur/services/api/api/routers/webhooks.py:1-229` — `/api/webhooks/{slug}` route, HMAC verification, run-create envelope
- `.centaur/services/api/api/routers/workflows.py:1-188` — `/workflows/runs` + `/workflows/events` API
- `.centaur/services/api/api/webhooks.py:1-198` — `WebhookSpec`, `HmacAuth`, registration
- `.centaur/services/api/api/sandbox/registry.py:1-37` — backend selection
- `.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:1-217` — agent-sandbox CRD backend
- `.centaur/services/api/api/sandbox/kubernetes.py:1-1709` — base Kubernetes backend, iron-proxy per-sandbox provisioning
- `.centaur/services/api/api/warm_pool.py:1-100` — Centaur's own warm pool (independent of `SandboxWarmPool` CRD)
- `.centaur/services/api/api/runtime_control.py:79-93, 3171-3204` — execution worker config + loop
- `.centaur/services/api/db/migrations/013_workflow_engine.sql:1-161` — workflow tables schema
- `.centaur/services/iron-proxy/{Dockerfile,iron-proxy.yaml,entrypoint.sh}` — iron-proxy provenance & default config
- `.centaur/centaur_sdk/tool_sdk.py:1-153` — `secret()`, `ToolContext`, attachment upload
- `.centaur/workflows/github_issue_triage.py:1-156` — webhook → agent template
- `.centaur/workflows/slack_backfill.py:1-461` — long-running scheduled fan-out template
- `.centaur/workflows/muesli_meeting_ingest.py:1-137` — workflow-owns-table template
- `.centaur/workflows/paradigm_pulse_daily.py:1-229` — scheduled-cron + Slack-post template
- `.centaur/tools/research/websearch/{client.py,pyproject.toml}` — secrets-declaring tool template
- `.centaur/tools/infra/demo/{client.py,pyproject.toml}` — minimal tool template
- `.centaur/contrib/chart/values.yaml:1-218` — full chart values surface
- `.centaur/contrib/chart/templates/workloads.yaml:121-429` — overlay mounting + env wiring
- `.centaur/contrib/chart/charts/agent-sandbox/{Chart.yaml,values.yaml,crds/*}` — bundled controller + CRDs
- `.centaur/docs/pages/architecture.mdx:1-119`, `.centaur/docs/pages/security.mdx:1-156`, `.centaur/docs/pages/secrets/advanced-permissioning.mdx:1-13`, `.centaur/docs/pages/extend/{workflows,tools,skills,overlay}.mdx` — published reference docs (prose)
- `.centaur/AGENTS.md:1-657` — canonical conventions (heavy prose, double-checked against code where possible)
- `Justfile:1-143`, `.centaur/Justfile:1-176`, `values.local.yaml:1-121` — this repo's local dev loop + chart overlay
