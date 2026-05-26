# BFTS-on-Centaur Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port Stage 1 of AI Scientist-v2's best-first tree search into Centaur as a durable workflow that drafts experiments, runs each in an isolated `Sandbox` CRD, scores results from a numeric harness, and emits a deterministic best-node checkpoint — survivable across worker restarts.

**Architecture:** A single `bfts_tree` workflow (the controller) owns the search state in an overlay-owned Postgres table (`bfts_nodes`). Per-expansion work fans out as `ctx.step(...)` calls into a `bfts_executor` tool that drives the existing `KubernetesAgentSandboxBackend` (raw `agents.x-k8s.io/v1alpha1 Sandbox` CR, *not* `SandboxClaim`/`SandboxTemplate` — see Spec corrections #3). The executor reproduces Sakana's `Interpreter.run(code, reset_session=True) -> ExecutionResult` contract inside the Sandbox; the controller mirrors Sakana's 5–7 LLM-call + 3 subprocess-exec per-expansion pipeline as individual checkpointed sub-steps so the workflow can resume mid-expansion. Best-first selection is deterministic argmax over `MetricValue.mean()` — *not* the Sakana LLM-judge.

**Tech Stack:** Centaur workflow engine (Python, `WorkflowContext`), `agents.x-k8s.io/v1alpha1` (vendored agent-sandbox v0.4.6 chart), Postgres (`asyncpg`), `kubernetes_asyncio`, `httpx` for OpenAI/Anthropic LLM calls, `dataclasses-json` for `Node` shape parity with Sakana.

**Spec:** `docs/centaur-science.md`

**Research:**
- `docs/superpowers/research/01-agent-sandbox.md`
- `docs/superpowers/research/02-ai-scientist-v2.md`
- `docs/superpowers/research/03-centaur-platform.md`
- `docs/superpowers/research/04-semantic-scholar.md`

**Branch:** create a new branch (e.g. `bfts-on-centaur-phase0`) per phase from the current `main`. Each phase ends with a Justfile recipe + verification, not just a commit.

---

## Spec corrections (post-research)

The spec at `docs/centaur-science.md` was written before the four research reports. These deltas are baked into the plan below; do not re-introduce the spec's original framing.

1. **"agent-sandbox v0.1.x" → v0.4.6.** Spec says project is "v0.1.x" and "early"; per research 01 §TL;DR the latest release is **v0.4.6 (2026-05-14)**, the chart at `.centaur/contrib/chart/charts/agent-sandbox/` already pins `tag: v0.4.6`, and Centaur's `KubernetesAgentSandboxBackend` is shipped in-tree. Plan pins to v0.4.6 explicitly and stages a re-vendor playbook for the v1alpha1 → v1beta1 graduation.
2. **"Hibernation" is pod stop/start with PV reattach, *not* memory checkpoint.** Spec implies hibernation preserves in-RAM state; per research 01 §TL;DR and §Gotcha #3 memory-checkpoint suspend (`PodSnapshot`) is GKE-only. On Docker Desktop / non-GKE the only mechanism is `spec.replicas: 0|1` — pod is deleted, PVC is reattached on resume. Plan names this explicitly ("pause/resume" not "hibernate"), and stores per-node experiment state on the per-Sandbox PVC mounted at `/workspace` (see Spec correction #12 for why this path replaces the originally-considered `/home/agent/state`) so the work survives pause.
3. **`SandboxTemplate` / `SandboxClaim` / `SandboxWarmPool` are bundled but *unused* by Centaur today.** Spec says "back the controller with `SandboxWarmPool`" and "per-role `SandboxTemplate`s"; per research 03 §Sandboxing and research 01 §Capability matrix, the chart sets `agentSandbox.controller.extensions: false` and `KubernetesAgentSandboxBackend` creates raw `Sandbox` CRs directly. Plan chooses **bare `Sandbox` CRs created by the workflow** (Option A in research 01 §Integration proposal) for the MVP — fewer moving parts, no upstream-chart edits — and defers WarmPool/Template/Claim to Phase 4+.
4. **`/api/webhooks/{slug}` creates a new run; it does *not* resume a waiting one.** Spec says GPU completion posts to a `/api/webhooks/{slug}` callback that resumes the workflow; per research 03 §Webhooks and §TL;DR, `/api/webhooks/{slug}` always *creates* a new run. To wake a `ctx.wait_for_event(...)` caller, you POST to `/workflows/events`. Plan defers the full GPU split to Phase 4 but stubs the wait/relay topology in Phase 2's input schema so swapping it in later doesn't reshape the controller.
5. **"Skills as tunable hyperparameters" is a category mismatch.** Spec proposes that the nightly reflection loop tunes "search hyperparameters" as a Centaur skill; per research 03 §Outer-loop and §Skills, skills are static Markdown prompt fragments mounted into the sandbox — not parameters, not edited by Centaur, not connected to workflow inputs. Plan reformulates the outer loop as a `bfts_hyperparams` overlay table; deferred to Phase 4+ and only sketched here.
6. **Best-node selection: deterministic argmax, *not* LLM judge.** Spec is silent; per research 02 §Tree data model `Journal.get_best_node` calls `gpt-4o` to pick the best of multiple `good_nodes` with non-deterministic fallback. Plan does deterministic `argmax(MetricValue.mean())` only.
7. **Per-stage scope: MVP ships *Stage 1 (drafting) only*.** Spec is non-committal; per research 02 §Mapping the 4-stage curriculum (`AgentManager`) is AI-Scientist-curriculum-specific, not BFTS. Plan ships Stage 1 (`num_drafts` independent draft nodes + debug retries + improvement of best draft). Stages 2–4 are deferred to a follow-on plan.
8. **Worker concurrency.** Spec doesn't mention it; per research 03 §Workflow programming model the default `WORKFLOW_WORKER_CONCURRENCY` is 2. Plan bumps to 16 in `values.local.yaml` (room for `num_drafts=3` + intra-tree fan-out of `num_workers=4` per Sakana defaults, with headroom for `bfts_executor` per-step calls and other workflows).
9. **Tree state lives in an overlay table, not in the checkpoint blob.** Spec says "controller owns the search tree in durable Postgres-backed state"; per research 03 §State & durability storage, storing the whole tree as one growing JSONB checkpoint rewrites it every step. Plan creates an overlay-owned `bfts_nodes` table; checkpoints hold IDs only.
10. **Iron-proxy egress allowlist defaults open (`"*"`).** Spec asks to "lock the egress allowlist"; per research 03 §Gotchas and §Secrets/iron-proxy, the allowlist defaults to `"*"` and is configured in `.centaur/services/iron-proxy/iron-proxy.yaml`. iron-proxy is *not* on the BFTS data path — BFTS pods don't call external model providers (the workflow does, from inside the API pod). Egress for BFTS pods is scoped at the K8s NetworkPolicy layer instead — see correction #13. iron-proxy's posture is therefore irrelevant to BFTS; no upstream PR is required for this plan.
11. **Open Q-1 → Decision:** the `bfts_executor` tool creates `agents.x-k8s.io/v1alpha1 Sandbox` CRDs directly via `kubernetes_asyncio.client.CustomObjectsApi`, mirroring the body shape of `KubernetesAgentSandboxBackend._create_workload` (`.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:109-154`) but with a workflow-generated `sandbox_id = f"bfts-{ctx.run_id}-tree-{tree_idx}"`, no `sandbox_sessions` row, no harness, and a `bfts-executor:latest` image whose CMD is `["sleep", "infinity"]`. We do **not** call `ctx.agent_turn(...)` to provision sandboxes — that path is for "spawn → message → execute → wait-for-terminal" agent runs (see `do_agent_turn` at `.centaur/services/api/api/workflow_engine.py:1124`) and pulls in `spawn_assignment`, slackbot session opening, and per-agent-execution event rows that BFTS doesn't need. **Implementation pointer:** Phase 1 Task 1.6 (CRD body + lifecycle), Task 1.8 (pause/resume retention smoke), Task 1.9 (tool registration); Phase 2 Task 2.9 (workflow generates sandbox_id and calls `bfts_executor.create_sandbox` via `ctx.step`, no `agent_turn` warmup).
12. **Open Q-2 → Decision:** state persists on a per-sandbox PVC created via `spec.volumeClaimTemplates` declared **inline inside the BFTS Sandbox CRD body**. We do **not** set `KUBERNETES_SANDBOX_STATE_VOLUME_ENABLED=1` / `sandbox.stateVolume.enabled: true` — both are read globally (`.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:21` and `.centaur/services/api/api/sandbox/kubernetes.py:102`) and would attach a state PVC to every Centaur sandbox spawned for any reason (Slack mentions, agent turns, warm pool), which is out of scope for this plan. The BFTS executor's inline `volumeClaimTemplates` (one `metadata.name=workspace` claim with `ReadWriteOnce` + 10Gi default) is mounted at `/workspace` inside the BFTS pod; `WORKDIR` is `/workspace` so Sakana's `os.path.join(os.getcwd(), 'working')` (research 02 §Code execution contract) resolves to `/workspace/working/`. Retention across pause/resume is shipped by the controller already (`shutdownPolicy: "Retain"` + `replicas: 0|1` patch — see `kubernetes_agent_sandbox.py:114, 159-185`); BFTS mirrors the same `pause_sandbox` / `resume_sandbox` / `stop_sandbox` (delete CRD; PVC reaped by owner refs because we use `volumeClaimTemplates`). **Implementation pointer:** Phase 1 Task 1.6 (inline `volumeClaimTemplates`), Task 1.8 (write-sentinel → pause → resume → read-sentinel retention smoke).
13. **Open Q-3 → Decision:** the BFTS executor, on first use of a Sandbox, idempotently creates a single namespace-scoped `networking.k8s.io/v1 NetworkPolicy` named `bfts-sandbox-egress` that selects pods labelled `centaur.ai/bfts-sandbox: "true"` and allows TCP/8000 to the api pod (status callbacks) + TCP/443 to the public internet (datasets, PyPI). The chart's default-deny (`.centaur/contrib/chart/templates/networkpolicy.yaml:9-13`) and `-allow-dns` (L15-34) handle ingress + DNS, and Kubernetes NetworkPolicy is union-based so this additive `Egress`-only rule is sufficient. We deliberately **do not** add the `centaur.ai/managed: "true"` label to BFTS pods — that label is the podSelector for the chart's `-sandbox` NetworkPolicy at L307-327, which locks egress to api:8000 only and would block PyPI/dataset fetches. RBAC is already granted (`.centaur/contrib/chart/templates/rbac.yaml:39-41` allows `create|delete|get|list|watch` on `networking.k8s.io/networkpolicies`); idempotency is the 409-conflict catch pattern already used in `kubernetes.py` for proxy NetworkPolicies (search `_create_proxy_network_policies` at L696-816 — same pattern: delete-then-create). **Implementation pointer:** Phase 1 Task 1.7 (NetworkPolicy creation, idempotent).

---

## File Structure

All paths are absolute from the repo root. Files under `.centaur/` and `.scientist/` are *never* edited (per AGENTS.md). The overlay image (`centaur-overlay:latest`) is rebuilt by `just overlay::build` and re-deployed by `just deploy` on every iteration.

### Phase 0 — Foundation

| File | Status | Responsibility |
|---|---|---|
| `values.local.yaml` | Modify | Pin agent-sandbox subchart at `v0.4.6` (already correct) and bump `WORKFLOW_WORKER_CONCURRENCY=16` via `api.extraEnv`. The chart-global `sandbox.stateVolume.enabled` knob is intentionally **not** required — BFTS Sandboxes carry their own inline `volumeClaimTemplates` (Spec correction #12) so the global env var (which would attach a PVC to every Centaur sandbox) stays opt-in. |
| `Justfile` | Modify | Add `just bfts-platform-smoke` recipe that uses pure `kubectl` to create a one-off `Sandbox` CRD against the bundled agent-sandbox controller, exec into the pod, and tear it down. No overlay workflow involved — this validates the controller + RBAC layer the BFTS executor will later drive. |
| `docs/superpowers/plans/2026-05-25-bfts-on-centaur.md` | This file | (already exists after Task 0). |

### Phase 1 — Sandbox executor contract

| File | Status | Responsibility |
|---|---|---|
| `overlay/tools/bfts_executor/__init__.py` | Create | Module marker. |
| `overlay/tools/bfts_executor/pyproject.toml` | Create | Tool registration; no external HTTP secrets (the sandbox is internal to the cluster). Deps: `kubernetes_asyncio`, `aiohttp`, `dataclasses-json`. |
| `overlay/Dockerfile.bfts-executor` | Create | Minimal `python:3.11-slim` image with `numpy`/`matplotlib`/`scikit-learn`/`torch` and `coreutils` (for `timeout(1)`). `WORKDIR /workspace`; CMD `["sleep", "infinity"]`. Built locally via `just bfts-build-executor`; consumed by `_KubernetesSandboxAPI.create_sandbox` as `bfts-executor:latest`. |
| `overlay/tools/bfts_executor/client.py` | Create | `BFTSExecutor` class exposing `create_sandbox(sandbox_id, ...)`, `pause_sandbox(sandbox_id)`, `resume_sandbox(sandbox_id)`, `stop_sandbox(sandbox_id)`, `exec_python(sandbox_id, code, timeout_s)` → `ExecutionResult`, and `collect_artifacts(sandbox_id, dest_dir, node_id)`. Drives `agents.x-k8s.io/v1alpha1 Sandbox` directly via `kubernetes_asyncio.client.CustomObjectsApi` (mirroring the body shape of `KubernetesAgentSandboxBackend._create_workload` at `.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:109-154`) — no SDK double-driving (research 01 §Gaps the integration must wrap). |
| `overlay/tools/bfts_executor/network_policy.py` | Create | `ensure_sandbox_egress_policy(networking_api, namespace)` — idempotently creates the namespace-scoped `bfts-sandbox-egress` NetworkPolicy that selects `centaur.ai/bfts-sandbox: "true"` pods and permits TCP/8000 to api + TCP/443 to the internet (Spec correction #13). |
| `overlay/tools/bfts_executor/models.py` | Create | `ExecutionResult` dataclass identical in shape to Sakana's (`term_out: list[str]`, `exec_time: float`, `exc_type: str \| None`, `exc_info: dict \| None`, `exc_stack: list[tuple] \| None`) — research 02 §Code execution contract. |
| `overlay/tools/bfts_executor/tests/__init__.py` | Create | Module marker. |
| `overlay/tools/bfts_executor/tests/test_exec_python_contract.py` | Create | Mocks `kubernetes_asyncio` and asserts the wire shape of an `ExecutionResult`. Verifies SIGINT-then-SIGKILL behavior on timeout via a fake clock. |
| `overlay/tools/bfts_executor/tests/test_artifact_collection.py` | Create | Asserts `collect_artifacts` copies `working/experiment_data.npy` + `working/*.png` out of the sandbox to a Centaur-side path keyed by node id. |
| `overlay/tools/bfts_executor/tests/test_create_sandbox_body.py` | Create | Asserts the BFTS Sandbox CRD body matches the upstream shape (apiVersion `agents.x-k8s.io/v1alpha1`, kind `Sandbox`, `spec.replicas: 1`, `service: false`, `shutdownPolicy: "Retain"`, inline `volumeClaimTemplates`, labels include `centaur.ai/bfts-sandbox: "true"` but **not** `centaur.ai/managed: "true"`). |
| `overlay/tools/bfts_executor/tests/test_network_policy.py` | Create | Asserts `ensure_sandbox_egress_policy` issues `create_namespaced_network_policy` once and silently no-ops on a 409 conflict (idempotency). |
| `Justfile` | Modify (Phase 1) | Add `bfts-build-executor` recipe (Task 1.6) that builds `overlay/Dockerfile.bfts-executor` locally and `bfts-retention-smoke` recipe (Task 1.8) — the end-of-Phase-1 integration smoke that drives `BFTSExecutor` from inside the api pod: create → write `/workspace/sentinel.txt` → pause → resume → read sentinel → stop. Proves PVC retention across `replicas: 0|1` matches `kubernetes_agent_sandbox.py:159-185`. |

### Phase 2 — Tree controller (MVP, Stage 1 only)

| File | Status | Responsibility |
|---|---|---|
| `services/api/db/migrations/20260525000001_add_bfts_tables.sql` | Create | Overlay migration: `bfts_runs`, `bfts_nodes`, `bfts_artifacts`. Applied via `./.centaur/contrib/scripts/dbmate --set overlay up` with `CENTAUR_OVERLAY_HOST_DIR=$(pwd)`. |
| `overlay/services/api/db/migrations/20260525000001_add_bfts_tables.sql` | Create | Symlink or copy of the above (this is the path the overlay-image dbmate wrapper expects at runtime, per research 03 §Local dev loop + the dbmate wrapper at `.centaur/contrib/scripts/dbmate:8-13`). |
| `overlay/Dockerfile` | Modify | Add `COPY services /overlay/services` so the in-pod overlay mount sees the migrations directory at `/app/overlay/org/services/api/db/migrations`. |
| `overlay/workflows/_bfts_state.py` | Create | Private helper module (underscore prefix = workflow loader skips it, per research 03 §Tool programming model). Owns the `bfts_nodes` table SQL: `insert_node`, `update_node_metric`, `list_nodes_for_run`, `mark_buggy_plots`. All inputs validated; no dynamic SQL. |
| `overlay/workflows/_bfts_select.py` | Create | Pure-Python selection function `select_next(nodes, search_cfg, rng) -> list[NodeRef \| None]` — direct port of Sakana's `_select_parallel_nodes` (`parallel_agent.py:1931-2051`). Deterministic given `rng`; no LLM calls; no I/O. |
| `overlay/workflows/_bfts_expand.py` | Create | Pure-Python expansion pipeline driver: given a parent node + branch (draft/debug/improve), returns a list of `ctx.step` calls to make. Carries the prompt templates compiled with `compile_prompt_to_md` (research 02 §Prompt structure). |
| `overlay/workflows/_bfts_prompts.py` | Create | The four prompt fragments mirrored verbatim from `.scientist/ai_scientist/treesearch/parallel_agent.py:273-451` and the four function specs (`review_func_spec`, `metric_parse_spec`, `vlm_feedback_spec`, `plot_selection_spec`). Pure data — no executions. |
| `overlay/workflows/_bfts_metric.py` | Create | `MetricValue` Python type: nested dict shape from research 02 §`MetricValue`; `mean()` collapse + first-metric `lower_is_better`. Documented as known footgun #6 from the master task list (see Phase 4 deferred fix). |
| `overlay/workflows/bfts_tree.py` | Create | The tree controller workflow. `WORKFLOW_NAME = "bfts_tree"`. Owns the per-step loop: `select_next` → `for node in selected: child = await ctx.step("expand", _expand_one, node)` → `wait_all` → write back via `_bfts_state`. Terminates when `≥1 good_node` exists OR `steps >= max_iters` (Sakana stage1 completion rule). |
| `overlay/workflows/bfts_root.py` | Create | Thin entry workflow. Takes `idea: dict` + a flattened `bfts_config` dict, generates a deterministic `sandbox_id = f"bfts-{ctx.run_id}-tree-{i}"` per child tree, calls `ctx.tools.bfts_executor.create_sandbox(...)` via `ctx.step` (no `ctx.agent_turn` warmup — see Spec correction #11), then fans out `num_drafts` independent `bfts_tree` child workflows via `ctx.start_workflow` + `ctx.wait_for_workflow`, and writes a `bfts_runs` row tying them together. |
| `overlay/workflows/tests/test_bfts_select.py` | Create | Property tests of `_bfts_select.select_next` against the Sakana reference: draft-only until `num_drafts` reached, debug-with-prob, improve-best when good nodes exist. |
| `overlay/workflows/tests/test_bfts_expand.py` | Create | Verifies the expansion sub-step list shape (number of LLM calls + exec calls per branch). |
| `overlay/workflows/tests/test_bfts_state.py` | Create | Integration test against a real `asyncpg` pool (skips when `CENTAUR_TEST_DATABASE_URL` unset, matching existing overlay test convention). |
| `Justfile` | Modify | Add `just bfts-run idea=...` recipe that POSTs a `bfts_root` run, then `just bfts-status run_id=...`. |

### Phase 3 — VLM gating + best-checkpoint export

| File | Status | Responsibility |
|---|---|---|
| `overlay/tools/bfts_vlm/__init__.py` | Create | Module marker. |
| `overlay/tools/bfts_vlm/pyproject.toml` | Create | Tool registration. Declares OpenAI `x-api-key` secret via `[tool.centaur].optional_secrets` (mirroring `semantic_scholar/pyproject.toml` shape — research 03 §Tool programming model). |
| `overlay/tools/bfts_vlm/client.py` | Create | `VLMReviewer.analyze_plots(plot_paths, task_desc) -> {is_valid, per_plot_analyses, summary}` — research 02 §VLM review contract. Picks 10-best via feedback model when >10 plots (mirrors `_analyze_plots_with_vlm` at `parallel_agent.py:894-1033`). |
| `overlay/tools/bfts_vlm/tests/test_vlm_contract.py` | Create | Mocks OpenAI; asserts the `{is_valid, per_plot_analyses[], summary}` return shape and the 10-plot cap behavior. |
| `overlay/workflows/_bfts_export.py` | Create | `export_best(ctx, run_id)` — pulls best node by deterministic `argmax(MetricValue.mean())`, writes `best_node_id.txt` + the node's `code` to `bfts_artifacts`, and emits a Centaur structured log. |
| `overlay/workflows/bfts_tree.py` | Modify | After tree termination call `_bfts_export.export_best(...)` in a final `ctx.step("export_best", ...)`. |
| `overlay/workflows/tests/test_bfts_export.py` | Create | Deterministic-argmax test with a mixed buggy/good node fixture. |

### Phase 4+ — deferred (named only, not detailed)

| File | Status | Why deferred |
|---|---|---|
| `overlay/workflows/bfts_gpu_callback.py` | Future | `WEBHOOKS=[...]` relay workflow that HMAC-validates and forwards via `send_workflow_event`. Out-of-scope until a real GPU compute target exists (research 03 OQ #2). |
| `overlay/workflows/bfts_reflection_nightly.py` | Future | Scheduled `SCHEDULE = {"cron": "0 3 * * *"}` workflow that writes new `bfts_hyperparams` rows. Out-of-scope until a real corpus of runs exists. |
| `overlay/services/api/db/migrations/<next>_add_bfts_hyperparams.sql` | Future | `bfts_hyperparams` table for the outer loop. |
| `overlay/workflows/ideation.py` | Future | Phase 1 of the S2 sub-plan (research 04 §Option B). |
| `overlay/workflows/gather_citations.py` | Future | Phase 2 of the S2 sub-plan (research 04 §Option C). |
| `overlay/tools/bfts_executor/sandbox_templates/` | Future | Per-role `SandboxTemplate` + `SandboxWarmPool` CRs once `agentSandbox.controller.extensions: true` is flipped on. |
| **Upstream PRs (separate ownership)** | Future | Bump `_AGENT_SANDBOX_VERSION` constant in `.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:16` once upstream graduates v1alpha1 → v1beta1. The iron-proxy `domains: ["*"]` posture is intentionally **not** on this list — iron-proxy is not on the BFTS data path (Spec correction #10 + #13). |

---

## Phasing

Each phase ends with a recipe + verification step. You can ship after any phase and have working software.

- **Phase 0** — Foundation. Pure-`kubectl` smoke that creates a `Sandbox` CRD against the bundled agent-sandbox controller, execs one command, writes to `/workspace`, and tears down. No overlay workflow involved. Verifies the platform end-to-end.
- **Phase 1** — Sandbox executor contract. The `bfts_executor` tool that exposes `exec_python(...)` returning the Sakana `ExecutionResult` shape. Tested in isolation against a mocked Kubernetes API.
- **Phase 2** — Tree controller (Stage 1 only). The `bfts_tree` + `bfts_root` workflows running on a toy experiment (input idea = "fit a 2-layer MLP on a 1000-sample synthetic regression task; report MSE"). End-to-end: tree builds, debug retries fire, improves happen, terminates on good node.
- **Phase 3** — VLM gating + best-node export. The `bfts_vlm` tool, the `is_buggy_plots` gate wired into `good_nodes`, the final `export_best` step.
- **Phase 4+** — Deferred. Section above.

---

# Phase 0 — Foundation

## Task 0.0: Pin agent-sandbox image tag defensively in `values.local.yaml`

**Why:** The upstream chart at `.centaur/contrib/chart/values.yaml:144-147` currently defaults `agentSandbox.image.tag: v0.4.6` (research 01 §TL;DR — the current latest). Because that default lives in submodule-tracked code, a future submodule bump silently changes the controller version that powers `KubernetesAgentSandboxBackend`. Pin it explicitly in `values.local.yaml` so a centaur-scientist deploy never moves until we move it on purpose. When upstream graduates v1alpha1 → v1beta1 (research 01 §Top open questions #6), this is also where you stage the migration.

**Files:**
- Modify: `values.local.yaml`

- [ ] **Step 1: Define the verification command**

```bash
helm template ${CENTAUR_RELEASE} .centaur/contrib/chart \
    -f .centaur/contrib/chart/values.dev.yaml -f values.local.yaml \
    --show-only charts/agentSandbox/templates/deployment.yaml 2>/dev/null \
  | grep -E 'image: .*agent-sandbox-controller'
```

Expected (post-edit): one line `image: registry.k8s.io/agent-sandbox/agent-sandbox-controller:v0.4.6`.

- [ ] **Step 2: Run before the edit to confirm**

Run the command from Step 1. Expected: a line with `:v0.4.6` (the upstream chart already defaults to it). The reason we pin is *defense against drift* — the verification should still produce `:v0.4.6` after the edit, just sourced from our values file instead of the upstream default.

- [ ] **Step 3: Edit `values.local.yaml`**

Replace the existing `agentSandbox:` block (lines ~83-88, currently:

```yaml
agentSandbox:
  enabled: true
```

) with:

```yaml
# Install the upstream agent-sandbox controller + CRDs into this release.
# Off in the chart default because most deployments bring their own
# controller; for the lab we want the chart to manage it so a single
# `just up` is enough. Pinned to v0.4.6 — the upstream chart default is
# also v0.4.6 today, but we pin defensively so a submodule bump can't
# silently change the controller version that powers BFTS. When v1alpha1
# graduates to v1beta1 upstream (research 01 §Top open questions #6),
# bump this tag in lockstep with the
# .centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:16
# constant (tracked as upstream PR in plan Phase 4f).
agentSandbox:
  enabled: true
  image:
    tag: v0.4.6
```

- [ ] **Step 4: Re-run the verification**

Run the command from Step 1.

Expected: `image: registry.k8s.io/agent-sandbox/agent-sandbox-controller:v0.4.6` (same value, but now traceable to *our* values file via `helm template --debug`).

- [ ] **Step 5: Commit**

```bash
git add values.local.yaml
git commit -m "ops: defensively pin agent-sandbox image tag to v0.4.6"
```

---

## Task 0.1: Bump WORKFLOW_WORKER_CONCURRENCY

**Why:** Per research 03 §Gotchas, the default is **2**. A BFTS run with `num_drafts=3` will starve itself: 1 slot for `bfts_root`, 3 slots for child `bfts_tree`s = 4 slots already and `bfts_tree`'s own intra-tree fan-out gets nothing. 16 gives `bfts_root` (1) + 3 trees + 4 intra-tree expand sub-workflows × 3 trees = 16 with some headroom. Concrete number, concrete rationale.

**Files:**
- Modify: `values.local.yaml`

- [ ] **Step 1: Write the verification command**

The verification is "after `just deploy`, the API pod's env shows `WORKFLOW_WORKER_CONCURRENCY=16`":

```bash
kubectl exec -n centaur-system deploy/centaur-centaur-api -- printenv WORKFLOW_WORKER_CONCURRENCY
```

Expected (before the edit, with default chart values): empty output (env var unset, defaults to 2 at the engine layer per `workflow_engine.py:157-159`).

- [ ] **Step 2: Run verification before the edit to confirm it shows the default**

Run the command from Step 1. Expected: empty string (or `2` if a stale override exists). Either way, **not** `16`.

- [ ] **Step 3: Edit `values.local.yaml`**

Locate the `api:` block (already exists, starts around line 17). Add an `extraEnv` subkey **inside** the `api:` block, keeping all existing keys intact. Insert just before the comment block ending at `image:` (around line 47):

```yaml
  # Bump from chart default 2 → 16 for BFTS fan-out (1 root + num_drafts trees
  # + per-tree intra-tree expand calls). Per docs/superpowers/research/03-centaur-platform.md
  # §Workflow programming model — engine default is 2, which starves any
  # workflow that fans out to children.
  extraEnv:
    WORKFLOW_WORKER_CONCURRENCY: "16"
```

The chart template at `.centaur/contrib/chart/templates/workloads.yaml:364` ranges over `.Values.api.extraEnv` and renders each as a pod env var, so this is the canonical knob.

- [ ] **Step 4: Re-deploy + re-run verification**

```bash
just deploy
# Wait ~30s for the API pod rollout to complete:
kubectl rollout status -n centaur-system deploy/centaur-centaur-api --timeout=120s
kubectl exec -n centaur-system deploy/centaur-centaur-api -- printenv WORKFLOW_WORKER_CONCURRENCY
```

Expected output: `16`.

- [ ] **Step 5: Commit**

```bash
git add values.local.yaml
git commit -m "ops: bump WORKFLOW_WORKER_CONCURRENCY to 16 for BFTS fan-out"
```

---

## Task 0.2: Pure-kubectl platform smoke (Sandbox CRD round trip)

**Why:** Before any BFTS code lands we need ground truth that (a) the bundled agent-sandbox controller is healthy, (b) the api pod's ServiceAccount can create `agents.x-k8s.io/v1alpha1 Sandbox` CRDs (`.centaur/contrib/chart/templates/rbac.yaml:42-44` grants the verbs), (c) a Sandbox CR with inline `volumeClaimTemplates` actually attaches a PVC at `/workspace`, and (d) `pods/exec` works through the api-pod kubeconfig. This is the platform check the rest of the plan is built on — it deliberately bypasses any overlay workflow / `ctx.agent_turn` path (those are agent-execution primitives — see `do_agent_turn` at `.centaur/services/api/api/workflow_engine.py:1124` — and the BFTS executor in Phase 1 does **not** route through them; Spec correction #11). The full `bfts_executor.create_sandbox → write sentinel → pause → resume → read sentinel → stop` retention smoke lives at the end of Phase 1 (Task 1.8) once the tool exists.

**Files:** none created; this task is verification-only.

- [ ] **Step 1: Define the verification command**

The smoke is "kubectl creates a Sandbox CRD via the api-pod's kubeconfig, the agent-sandbox controller reconciles it into a pod, `kubectl exec` writes and reads a file inside `/workspace`, then we tear the CRD down and the PVC is reaped by owner refs". One shell script, no Python.

- [ ] **Step 2: Run before the controller is healthy to confirm it fails**

If `just up` has not been run yet (or the agent-sandbox controller hasn't reconciled), the script below exits non-zero on the `kubectl wait` step. After `just up` it should pass. Use the failure mode as the pre-edit baseline.

- [ ] **Step 3: Write the smoke script inline**

Run, from the repo root, as the user (this is a one-off script we'll Justfile-wrap in Task 0.3):

```bash
set -euo pipefail
ns=$CENTAUR_NAMESPACE
sandbox_id="bfts-platform-smoke-$(date +%s)"
api_sa=$(kubectl -n "$ns" get deploy "${CENTAUR_RELEASE}-centaur-api" -o jsonpath='{.spec.template.spec.serviceAccountName}')
cleanup() {
  kubectl -n "$ns" delete sandbox.agents.x-k8s.io "$sandbox_id" --ignore-not-found --wait=true >/dev/null 2>&1 || true
}
trap cleanup EXIT

cat <<YAML | kubectl -n "$ns" apply -f -
apiVersion: agents.x-k8s.io/v1alpha1
kind: Sandbox
metadata:
  name: ${sandbox_id}
  labels:
    centaur.ai/bfts-sandbox: "true"
spec:
  replicas: 1
  service: false
  shutdownPolicy: Retain
  volumeClaimTemplates:
    - metadata:
        name: workspace
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 1Gi
  podTemplate:
    metadata:
      labels:
        centaur.ai/bfts-sandbox: "true"
    spec:
      containers:
        - name: sandbox
          image: busybox:1.36
          command: ["sleep", "infinity"]
          workingDir: /workspace
          volumeMounts:
            - name: workspace
              mountPath: /workspace
YAML

kubectl -n "$ns" wait --for=condition=Ready pod/"$sandbox_id" --timeout=120s
kubectl -n "$ns" exec "$sandbox_id" -- sh -c \
  'mkdir -p /workspace/smoke && printf "%s" "PLATFORM_OK" > /workspace/smoke/marker && cat /workspace/smoke/marker'
echo "platform smoke: sandbox ${sandbox_id} round-trip OK"
```

Expected stdout: `PLATFORM_OKplatform smoke: sandbox bfts-platform-smoke-<epoch> round-trip OK`. Non-zero exit on any step = platform is not BFTS-ready; investigate before moving on.

Quoted shape pointers (read once, then copy verbatim into the YAML):
- `apiVersion: agents.x-k8s.io/v1alpha1` matches the constant at `.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:15-17`.
- `spec.replicas: 1`, `service: false`, `shutdownPolicy: "Retain"` mirror `_create_workload` at `.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:111-114`.
- `volumeClaimTemplates` shape mirrors `kubernetes_agent_sandbox.py:131-136`.

- [ ] **Step 4: Verify the controller reconciled cleanly**

```bash
kubectl -n $CENTAUR_NAMESPACE get sandbox.agents.x-k8s.io -l centaur.ai/bfts-sandbox=true
```

Expected: empty list (the trap ran cleanup). If you see the sandbox lingering, run `kubectl -n $CENTAUR_NAMESPACE delete sandbox.agents.x-k8s.io <name>` manually.

- [ ] **Step 5: Commit (no code change in this task)**

No files were created by this task. The next task (0.3) wraps the script in a Justfile recipe; commit there.

---

## Task 0.3: Justfile recipe wrapping the pure-kubectl platform smoke

**Why:** Hand-rolling the script from Task 0.2 per test cycle drifts. Codify it as a recipe so the next iteration is `just up && just bfts-platform-smoke`. The recipe deliberately does **not** depend on the overlay image or any workflow — Phase 1's `bfts_executor.create_sandbox` does not exist yet, and the full BFTS round trip lands in Task 1.8.

**Files:**
- Modify: `Justfile`

- [ ] **Step 1: Define the verification command**

The recipe should:
1. Generate a unique `sandbox_id`.
2. `kubectl apply` an inline Sandbox CRD with `centaur.ai/bfts-sandbox: "true"` labels, inline `volumeClaimTemplates`, `image: busybox:1.36`, CMD `["sleep", "infinity"]`, `workingDir: /workspace`.
3. `kubectl wait` for pod ready.
4. `kubectl exec` into the pod, write `PLATFORM_OK` to `/workspace/smoke/marker`, read it back.
5. Delete the Sandbox CR (PVC follows the CR's owner refs via `volumeClaimTemplates`).
6. Exit 0 on success; non-zero on any step.

Verification: `just bfts-platform-smoke` exits 0 and prints `PLATFORM SMOKE OK`.

- [ ] **Step 2: Run before the edit to confirm it fails**

```bash
just bfts-platform-smoke
```

Expected: `error: Justfile does not contain recipe 'bfts-platform-smoke'`.

- [ ] **Step 3: Add the recipe**

Append to `Justfile` (at the bottom, after the existing `dev` recipe ending around line 143):

```just
# Phase 0 platform smoke: confirms the agent-sandbox controller + api SA RBAC
# can create a Sandbox CRD with the BFTS shape (labels, inline volumeClaim,
# workspace mount path) and that pods/exec works. No overlay workflow
# involved; this is a pure-kubectl check against the bundled controller.
# See docs/superpowers/plans/2026-05-25-bfts-on-centaur.md (Phase 0 Task 0.2).
[group('bfts')]
bfts-platform-smoke:
    #!/usr/bin/env bash
    set -euo pipefail
    ns=$CENTAUR_NAMESPACE
    sandbox_id="bfts-platform-smoke-$(date +%s)"
    cleanup() {
      kubectl -n "$ns" delete sandbox.agents.x-k8s.io "$sandbox_id" \
        --ignore-not-found --cascade=foreground --wait=true || true
    }
    trap cleanup EXIT
    cat <<YAML | kubectl -n "$ns" apply -f -
    apiVersion: agents.x-k8s.io/v1alpha1
    kind: Sandbox
    metadata:
      name: ${sandbox_id}
      labels:
        centaur.ai/bfts-sandbox: "true"
    spec:
      replicas: 1
      service: false
      shutdownPolicy: Retain
      volumeClaimTemplates:
        - metadata:
            name: workspace
          spec:
            accessModes: ["ReadWriteOnce"]
            resources:
              requests:
                storage: 1Gi
      podTemplate:
        metadata:
          labels:
            centaur.ai/bfts-sandbox: "true"
        spec:
          containers:
            - name: sandbox
              image: busybox:1.36
              command: ["sleep", "infinity"]
              workingDir: /workspace
              volumeMounts:
                - name: workspace
                  mountPath: /workspace
    YAML
    kubectl -n "$ns" wait --for=condition=Ready pod/"$sandbox_id" --timeout=120s
    out=$(kubectl -n "$ns" exec "$sandbox_id" -- sh -c \
      'mkdir -p /workspace/smoke && printf "%s" "PLATFORM_OK" > /workspace/smoke/marker && cat /workspace/smoke/marker')
    if [ "$out" = "PLATFORM_OK" ]; then
      echo "PLATFORM SMOKE OK (sandbox ${sandbox_id})"
      exit 0
    fi
    echo "unexpected exec output: '${out}'" >&2
    exit 1
```

- [ ] **Step 4: Build + deploy + run the smoke**

```bash
just up
kubectl rollout status -n centaur-system deploy/centaur-centaur-api --timeout=120s
just bfts-platform-smoke
```

Expected output: `PLATFORM SMOKE OK (sandbox bfts-platform-smoke-<epoch>)` and exit 0.

If `kubectl wait` times out, inspect:

```bash
kubectl -n $CENTAUR_NAMESPACE describe sandbox.agents.x-k8s.io -l centaur.ai/bfts-sandbox=true
kubectl -n $CENTAUR_NAMESPACE logs deploy/agent-sandbox-controller --tail=200
```

The most common cause is the agent-sandbox controller subchart not installed; confirm `agentSandbox.enabled: true` in `values.local.yaml` (already correct) and re-run `just deploy`.

- [ ] **Step 5: Commit**

```bash
git add Justfile
git commit -m "feat(bfts): add just bfts-platform-smoke recipe for Phase 0 verification"
```

---

## Task 0.4: Document Phase 0 verification

**Why:** The smoke recipe should be referenced from the README so a fresh contributor knows the platform-health check exists.

**Files:**
- Modify: `README.md` (add one section)

- [ ] **Step 1: Confirm what's missing**

```bash
grep -n "bfts-platform-smoke" README.md || echo "NOT FOUND"
```

Expected: `NOT FOUND`.

- [ ] **Step 2: Add the section**

Append to `README.md` (after whatever "Smoke test" section exists; if none, after the install section):

```markdown
## BFTS platform smoke

After `just up` completes, verify the agent-sandbox controller + api SA
RBAC + inline `volumeClaimTemplates` mount path used by BFTS with:

```bash
just bfts-platform-smoke
```

Expected output: `PLATFORM SMOKE OK (sandbox bfts-platform-smoke-<epoch>)`.
The recipe creates a one-off `agents.x-k8s.io/v1alpha1 Sandbox` CRD with
the same labels and `/workspace` PVC layout the BFTS executor will use,
execs into the pod, writes/reads a marker file, and tears the CRD down.
If it fails, see
`docs/superpowers/plans/2026-05-25-bfts-on-centaur.md` (Phase 0, Task 0.3
Step 4 troubleshooting). This recipe is the platform-health contract
every later BFTS phase depends on. The full
`bfts_executor.create_sandbox → pause → resume → stop` round trip is
exercised separately at the end of Phase 1 (`just bfts-retention-smoke`).
```

- [ ] **Step 3: Verify**

```bash
grep -n "bfts-platform-smoke" README.md
```

Expected: at least 2 lines (the recipe name appears in the code block and prose).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(bfts): document Phase 0 platform smoke recipe"
```

---

# Phase 1 — Sandbox executor contract

The `bfts_executor` tool is the bridge between the durable workflow and unvetted agent-generated experiment code. It exposes one primary method, `exec_python(sandbox_id, code, timeout_s) -> ExecutionResult`, reproducing the Sakana contract from research 02 §Code execution contract. Everything in this phase is testable in isolation against a mocked `kubernetes_asyncio` client; no Sandboxes are spawned until Phase 2.

## Task 1.1: Scaffolding (pyproject + module marker)

**Files:**
- Create: `overlay/tools/bfts_executor/__init__.py`
- Create: `overlay/tools/bfts_executor/pyproject.toml`
- Create: `overlay/tools/bfts_executor/tests/__init__.py`

- [ ] **Step 1: Write the verification command**

The tool registration is correct iff (a) the file exists, (b) `[tool.centaur].module = "client.py"` is set, (c) there are no `optional_secrets` declared (no external HTTPS — the sandbox is internal). Verification:

```bash
python - <<'PY'
import tomllib, pathlib
p = pathlib.Path("overlay/tools/bfts_executor/pyproject.toml")
data = tomllib.loads(p.read_text())
assert data["project"]["name"] == "bfts_executor"
assert data["tool"]["centaur"]["module"] == "client.py"
assert "optional_secrets" not in data["tool"]["centaur"]
print("OK")
PY
```

- [ ] **Step 2: Run before creating to confirm it fails**

Expected: `FileNotFoundError` on the read.

- [ ] **Step 3: Create the files**

`overlay/tools/bfts_executor/__init__.py`:

```python
"""BFTS executor tool: drives Sandbox CRs for experiment exec.

See docs/superpowers/plans/2026-05-25-bfts-on-centaur.md, Phase 1.
"""
```

`overlay/tools/bfts_executor/tests/__init__.py`:

```python
```

`overlay/tools/bfts_executor/pyproject.toml`:

```toml
[project]
name = "bfts_executor"
description = "Drive agent-sandbox Sandbox CRs for BFTS experiment execution"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "kubernetes-asyncio>=29.0.0",
    "httpx>=0.27.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
package = false

[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[tool.pytest.ini_options]
asyncio_mode = "strict"

[tool.centaur]
module = "client.py"
# No optional_secrets: this tool only talks to the in-cluster Kubernetes
# API (read via the API pod's mounted service account token) and to the
# sandbox-internal HTTP exec endpoint. No external HTTPS, so iron-proxy
# has nothing to substitute.
```

- [ ] **Step 4: Run the verification**

Run the Python check from Step 1. Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add overlay/tools/bfts_executor/__init__.py \
        overlay/tools/bfts_executor/pyproject.toml \
        overlay/tools/bfts_executor/tests/__init__.py
git commit -m "feat(bfts): scaffold bfts_executor tool package"
```

---

## Task 1.2: `ExecutionResult` model (Sakana shape parity)

**Why:** Every per-node prompt downstream of `exec_python` consumes this exact shape (research 02 §`Interpreter` and §What the port's Sandbox `exec` wrapper must reproduce). Centralize the dataclass so the tool, the workflow tests, and the helper modules all import the same definition.

**Files:**
- Create: `overlay/tools/bfts_executor/models.py`
- Create: `overlay/tools/bfts_executor/tests/test_execution_result_shape.py`

- [ ] **Step 1: Write the failing test**

`overlay/tools/bfts_executor/tests/test_execution_result_shape.py`:

```python
"""Test: ExecutionResult preserves Sakana's wire shape exactly."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import ExecutionResult


def test_execution_result_minimal_construction() -> None:
    r = ExecutionResult(term_out=["hi\n"], exec_time=0.1, exc_type=None)
    assert r.term_out == ["hi\n"]
    assert r.exec_time == 0.1
    assert r.exc_type is None
    assert r.exc_info is None
    assert r.exc_stack is None


def test_execution_result_with_exception() -> None:
    r = ExecutionResult(
        term_out=["Traceback...\n"],
        exec_time=0.5,
        exc_type="ValueError",
        exc_info={"args": ["bad input"]},
        exc_stack=[("/work/runfile.py", 12, "<module>", "raise ValueError('bad input')")],
    )
    assert r.exc_type == "ValueError"
    assert r.exc_info == {"args": ["bad input"]}
    assert r.exc_stack[0][0] == "/work/runfile.py"


def test_execution_result_roundtrip_json() -> None:
    r = ExecutionResult(term_out=["hi\n"], exec_time=0.1, exc_type=None)
    blob = r.to_dict()
    assert blob == {
        "term_out": ["hi\n"],
        "exec_time": 0.1,
        "exc_type": None,
        "exc_info": None,
        "exc_stack": None,
    }
    r2 = ExecutionResult.from_dict(blob)
    assert r2 == r
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd overlay/tools/bfts_executor && uv run --python 3.11 --with pytest --with pytest-asyncio --with dataclasses-json pytest tests/test_execution_result_shape.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'models'`.

- [ ] **Step 3: Write the implementation**

`overlay/tools/bfts_executor/models.py`:

```python
"""Result shape for one Sandbox-side code execution.

Mirrors .scientist/ai_scientist/treesearch/interpreter.py:26-37 verbatim
so existing Sakana-shape prompts and metric-parse scripts work unchanged
inside the Centaur workflow.
"""
from __future__ import annotations

from dataclasses import dataclass

from dataclasses_json import DataClassJsonMixin


@dataclass
class ExecutionResult(DataClassJsonMixin):
    """One code-execution result (stdout/stderr, timing, exception)."""

    term_out: list[str]
    exec_time: float
    exc_type: str | None
    exc_info: dict | None = None
    exc_stack: list[tuple] | None = None
```

Add `dataclasses-json>=0.6.0` to `pyproject.toml` dependencies (insert after `httpx>=0.27.0`):

```toml
    "dataclasses-json>=0.6.0",
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd overlay/tools/bfts_executor && uv run --python 3.11 --with pytest --with pytest-asyncio --with dataclasses-json pytest tests/test_execution_result_shape.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add overlay/tools/bfts_executor/models.py \
        overlay/tools/bfts_executor/pyproject.toml \
        overlay/tools/bfts_executor/tests/test_execution_result_shape.py
git commit -m "feat(bfts): add ExecutionResult model (Sakana shape parity)"
```

---

## Task 1.3: `exec_python` driver — happy path

**Why:** The workflow needs one async function it can call from inside `ctx.step(...)` that (a) writes the code to the sandbox at `working/runfile.py`, (b) runs it via the sandbox's command exec path, (c) returns an `ExecutionResult`. Build the happy path first; timeouts (Task 1.4) and artifact collection (Task 1.5) follow.

**Files:**
- Create: `overlay/tools/bfts_executor/client.py`
- Create: `overlay/tools/bfts_executor/tests/test_exec_python_happy.py`

- [ ] **Step 1: Write the failing test**

`overlay/tools/bfts_executor/tests/test_exec_python_happy.py`:

```python
"""Test: exec_python happy path returns Sakana-shape ExecutionResult."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import BFTSExecutor
from models import ExecutionResult


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
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd overlay/tools/bfts_executor && uv run --python 3.11 --with pytest --with pytest-asyncio --with dataclasses-json pytest tests/test_exec_python_happy.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'client'`.

- [ ] **Step 3: Write the implementation**

`overlay/tools/bfts_executor/client.py`:

```python
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
        # The caller-visible ``timeout_s`` is the inner ``timeout(1)``
        # deadline; the +90s wire-level buffer (SIGINT at T → +60s SIGKILL →
        # +30s reply slack) lives inside :class:`_KubernetesSandboxAPI`
        # (Task 1.6) so the Protocol stays clean for tests.
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
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd overlay/tools/bfts_executor && uv run --python 3.11 --with pytest --with pytest-asyncio --with dataclasses-json pytest tests/test_exec_python_happy.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add overlay/tools/bfts_executor/client.py \
        overlay/tools/bfts_executor/tests/test_exec_python_happy.py
git commit -m "feat(bfts): add exec_python happy path on BFTSExecutor"
```

---

## Task 1.4: Timeout semantics (SIGINT then SIGKILL)

**Why:** Per research 02 §Code execution contract, Sakana sends SIGINT at T, SIGKILL at T+60. We delegate the actual signals to coreutils `timeout(1)` *inside* the sandbox (in `BFTSExecutor.exec_python` above, the `--signal=INT --kill-after=60` flags). The contract we expose to the workflow is: a timed-out exec returns `exc_type="TimeoutError"`. Build the assertion that this round-trips.

**Files:**
- Create: `overlay/tools/bfts_executor/tests/test_exec_python_timeout.py`

- [ ] **Step 1: Write the failing test**

`overlay/tools/bfts_executor/tests/test_exec_python_timeout.py`:

```python
"""Test: exec_python surfaces coreutils timeout(1) exit 124 as exc_type='TimeoutError'."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import BFTSExecutor


@dataclass
class _FakePodExecResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_s: float


class _TimeoutAPI:
    async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
        return None

    async def run_command(self, sandbox_id: str, command: str, *, timeout_s: float) -> _FakePodExecResult:
        # coreutils timeout(1) returns 124 on timeout-then-clean-exit;
        # 137 if SIGKILL fired (128+9). We surface both as TimeoutError —
        # the workflow doesn't need to distinguish.
        return _FakePodExecResult(stdout="partial\n", stderr="", exit_code=124, duration_s=10.0)


@pytest.mark.asyncio
async def test_exec_python_timeout_surfaces_TimeoutError() -> None:
    executor = BFTSExecutor(sandbox_api=_TimeoutAPI())
    result = await executor.exec_python(sandbox_id="sbx-t", code="import time; time.sleep(60)", timeout_s=10.0)
    assert result.exc_type == "TimeoutError"
    assert result.exc_info == {"exit_code": 124, "timeout_s": 10.0}
    assert result.term_out == ["partial\n"]


@pytest.mark.asyncio
async def test_exec_python_command_passes_kill_after_60() -> None:
    """Verify the SIGINT-then-SIGKILL-at-T+60 pattern is in the command string."""
    captured: list[str] = []

    class _CapturingAPI:
        async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
            return None

        async def run_command(self, sandbox_id: str, command: str, *, timeout_s: float) -> _FakePodExecResult:
            captured.append(command)
            return _FakePodExecResult(stdout="", stderr="", exit_code=0, duration_s=0.0)

    executor = BFTSExecutor(sandbox_api=_CapturingAPI())
    await executor.exec_python(sandbox_id="sbx-t2", code="pass", timeout_s=42.0)
    assert any("timeout --signal=INT --kill-after=60 42 python -u" in c for c in captured)
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd overlay/tools/bfts_executor && uv run --python 3.11 --with pytest --with pytest-asyncio --with dataclasses-json pytest tests/test_exec_python_timeout.py -v
```

Expected: with the current `client.py` from Task 1.3, both tests should already PASS — Task 1.3's implementation already covers this. If you ran in strict TDD order, swap Steps 2 and 3: write Task 1.4's tests *before* Task 1.3's implementation and they fail first.

If both pass: skip Step 3 (no implementation change needed) and go straight to commit.

- [ ] **Step 3: (Skipped if Step 2 already passes.)**

If the tests failed because Task 1.3 was minimal, extend `client.py` `exec_python` to inject `--signal=INT --kill-after=60` and the `124 → TimeoutError` mapping. The Task 1.3 reference implementation above already includes both — no edit needed in this branch of the plan.

- [ ] **Step 4: Re-run the test to confirm it passes**

```bash
cd overlay/tools/bfts_executor && uv run --python 3.11 --with pytest --with pytest-asyncio --with dataclasses-json pytest tests/test_exec_python_timeout.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add overlay/tools/bfts_executor/tests/test_exec_python_timeout.py
git commit -m "test(bfts): pin SIGINT-then-SIGKILL timeout contract"
```

---

## Task 1.5: Artifact collection (`collect_artifacts`)

**Why:** After exec, the workflow needs to move `working/experiment_data.npy` and `working/*.png` out of the sandbox to a Centaur-side path keyed by node id (research 02 §Workspace layout). The agent prompt downstream of metric-parse and plotting requires these files to exist at predictable per-node paths.

**Files:**
- Modify: `overlay/tools/bfts_executor/client.py`
- Create: `overlay/tools/bfts_executor/tests/test_collect_artifacts.py`

- [ ] **Step 1: Write the failing test**

`overlay/tools/bfts_executor/tests/test_collect_artifacts.py`:

```python
"""Test: collect_artifacts moves .npy + .png out of the Sandbox PVC."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import BFTSExecutor


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
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd overlay/tools/bfts_executor && uv run --python 3.11 --with pytest --with pytest-asyncio --with dataclasses-json pytest tests/test_collect_artifacts.py -v
```

Expected: FAIL with `AttributeError: 'BFTSExecutor' object has no attribute 'collect_artifacts'`.

- [ ] **Step 3: Extend `client.py`**

Add at the end of `BFTSExecutor`:

```python
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
```

Also add `from pathlib import Path` to the imports at the top of `client.py` and widen the `_SandboxAPI` Protocol to declare `list_dir` and `read_file_bytes`:

```python
class _SandboxAPI(Protocol):
    async def write_file(self, sandbox_id: str, path: str, content: str) -> None: ...

    async def run_command(
        self, sandbox_id: str, command: str, *, timeout_s: float
    ) -> _PodExecResult: ...

    async def list_dir(self, sandbox_id: str, path: str) -> list[str]: ...

    async def read_file_bytes(self, sandbox_id: str, path: str) -> bytes: ...
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd overlay/tools/bfts_executor && uv run --python 3.11 --with pytest --with pytest-asyncio --with dataclasses-json pytest tests/test_collect_artifacts.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add overlay/tools/bfts_executor/client.py \
        overlay/tools/bfts_executor/tests/test_collect_artifacts.py
git commit -m "feat(bfts): add collect_artifacts to BFTSExecutor"
```

---

## Task 1.6: Real Kubernetes-backed `_SandboxAPI` — CRD lifecycle + WsApiClient exec

**Why:** Tasks 1.3–1.5 used a fake `_SandboxAPI`. For Phase 2 we need the real one. Per Spec correction #11 the BFTS executor **creates its own `agents.x-k8s.io/v1alpha1 Sandbox` CRDs directly** (mirroring `KubernetesAgentSandboxBackend._create_workload` at `.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:109-154`); it does **not** call `ctx.agent_turn(...)` to provision them (that path runs the spawn → message → execute → wait loop in `do_agent_turn` at `.centaur/services/api/api/workflow_engine.py:1124` — out of scope for unscored code-exec). Per Spec correction #12 the per-sandbox PVC is declared **inline** as `spec.volumeClaimTemplates` so the global `KUBERNETES_SANDBOX_STATE_VOLUME_ENABLED` env var stays opt-in. Exec is reproduced from the upstream `WsApiClient` pattern at `.centaur/services/api/api/sandbox/kubernetes.py:1503-1551` so we get a real exit code through `ERROR_CHANNEL` (no `__BFTS_EXIT__` marker hack) and structured stdout/stderr through `STDOUT_CHANNEL`/`STDERR_CHANNEL`.

**Files:**
- Create: `overlay/Dockerfile.bfts-executor`
- Modify: `Justfile` (add `bfts-build-executor` recipe)
- Modify: `overlay/tools/bfts_executor/client.py`
- Modify: `overlay/tools/bfts_executor/pyproject.toml` (add `aiohttp` dep)
- Create: `overlay/tools/bfts_executor/tests/test_create_sandbox_body.py`
- Create: `overlay/tools/bfts_executor/tests/test_sandbox_lifecycle.py`
- Create: `overlay/tools/bfts_executor/tests/test_kubernetes_api_calls.py`

- [ ] **Step 1: Build the executor pod image**

The BFTS executor runs experiment Python code inside a Sandbox pod that we own (overlay-side). It needs Python 3.11, `numpy`, `matplotlib`, `coreutils` (for `timeout(1)`), and `base64`. Create `overlay/Dockerfile.bfts-executor`:

```dockerfile
# Image used by Sandbox pods that the bfts_executor tool creates.
# Sleeps forever; the tool drives all work via pods/exec.
# Mirrors the workspace contract from Sakana's Interpreter.run:
# WORKDIR /workspace so os.path.join(os.getcwd(), 'working') resolves to
# /workspace/working — the path Sakana's prompts write to (research 02
# §Code execution contract).
FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends coreutils \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        "numpy>=1.26" \
        "matplotlib>=3.8" \
        "scikit-learn>=1.4" \
        "torch>=2.2 ; platform_machine=='x86_64'"

WORKDIR /workspace
CMD ["sleep", "infinity"]
```

Append the build recipe to `Justfile` (after the existing `dev` recipe ending around line 143):

```just
# Build the bfts-executor:latest image used by Sandbox pods the BFTS
# tool spawns. Docker Desktop's k8s shares the host image cache so
# pullPolicy: IfNotPresent finds the local tag without a registry.
# See docs/superpowers/plans/2026-05-25-bfts-on-centaur.md (Phase 1).
[group('bfts')]
bfts-build-executor:
    docker build -f overlay/Dockerfile.bfts-executor -t bfts-executor:latest overlay
```

Verify:

```bash
just bfts-build-executor
docker images bfts-executor:latest
```

Expected: one line showing the image with a recent `CREATED` timestamp.

- [ ] **Step 2: Write the failing CRD-body shape test**

`overlay/tools/bfts_executor/tests/test_create_sandbox_body.py`:

```python
"""Test: BFTSExecutor.create_sandbox emits the right Sandbox CRD body.

Asserts the body shape mirrors the upstream pattern in
.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:109-154
while substituting BFTS-specific labels (centaur.ai/bfts-sandbox=true,
NOT centaur.ai/managed=true — Spec correction #13 in the plan) and an
inline volumeClaimTemplates entry mounted at /workspace.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import _KubernetesSandboxAPI


@pytest.mark.asyncio
async def test_create_sandbox_emits_expected_body() -> None:
    custom_api = MagicMock()
    custom_api.create_namespaced_custom_object = AsyncMock(return_value=None)
    networking_api = MagicMock()
    networking_api.create_namespaced_network_policy = AsyncMock(return_value=None)
    api = _KubernetesSandboxAPI(
        custom_api=custom_api,
        networking_api=networking_api,
        namespace="centaur-system",
    )
    await api.create_sandbox(
        sandbox_id="bfts-run-abc-tree-0",
        run_id="run-abc",
        image="bfts-executor:latest",
        storage_size="10Gi",
        storage_class=None,
    )
    custom_api.create_namespaced_custom_object.assert_awaited_once()
    args, kwargs = custom_api.create_namespaced_custom_object.call_args
    group, version, ns, plural, body = args
    assert group == "agents.x-k8s.io"
    assert version == "v1alpha1"
    assert ns == "centaur-system"
    assert plural == "sandboxes"
    assert body["apiVersion"] == "agents.x-k8s.io/v1alpha1"
    assert body["kind"] == "Sandbox"
    assert body["metadata"]["name"] == "bfts-run-abc-tree-0"
    labels = body["metadata"]["labels"]
    assert labels["centaur.ai/bfts-sandbox"] == "true"
    assert labels["centaur.ai/bfts-run"] == "run-abc"
    # Critical: do NOT inherit the chart's centaur.ai/managed=true selector
    # (.centaur/contrib/chart/templates/networkpolicy.yaml:307-327 would
    # then lock egress to api:8000 only).
    assert "centaur.ai/managed" not in labels
    spec = body["spec"]
    assert spec["replicas"] == 1
    assert spec["service"] is False
    assert spec["shutdownPolicy"] == "Retain"
    # Inline volumeClaimTemplates (Spec correction #12) — do NOT rely on
    # the global KUBERNETES_SANDBOX_STATE_VOLUME_ENABLED env var.
    assert len(spec["volumeClaimTemplates"]) == 1
    pvc = spec["volumeClaimTemplates"][0]
    assert pvc["metadata"]["name"] == "workspace"
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert pvc["spec"]["resources"]["requests"]["storage"] == "10Gi"
    assert "storageClassName" not in pvc["spec"]
    pod_spec = spec["podTemplate"]["spec"]
    container = pod_spec["containers"][0]
    assert container["image"] == "bfts-executor:latest"
    assert container["command"] == ["sleep", "infinity"]
    assert container["workingDir"] == "/workspace"
    mounts = container["volumeMounts"]
    assert any(m["name"] == "workspace" and m["mountPath"] == "/workspace" for m in mounts)


@pytest.mark.asyncio
async def test_create_sandbox_passes_storage_class_when_given() -> None:
    custom_api = MagicMock()
    custom_api.create_namespaced_custom_object = AsyncMock(return_value=None)
    networking_api = MagicMock()
    networking_api.create_namespaced_network_policy = AsyncMock(return_value=None)
    api = _KubernetesSandboxAPI(
        custom_api=custom_api,
        networking_api=networking_api,
        namespace="centaur-system",
    )
    await api.create_sandbox(
        sandbox_id="bfts-x",
        run_id="r1",
        image="bfts-executor:latest",
        storage_size="20Gi",
        storage_class="standard",
    )
    body = custom_api.create_namespaced_custom_object.call_args.args[4]
    pvc = body["spec"]["volumeClaimTemplates"][0]
    assert pvc["spec"]["storageClassName"] == "standard"
```

- [ ] **Step 3: Write the failing lifecycle test**

`overlay/tools/bfts_executor/tests/test_sandbox_lifecycle.py`:

```python
"""Test: pause/resume/stop hit the right CustomObjectsApi calls.

Mirrors KubernetesAgentSandboxBackend.pause_by_id / resume_by_id /
stop_by_id at .centaur/services/api/api/sandbox/kubernetes_agent_sandbox
.py:159-217.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import _KubernetesSandboxAPI


def _mk_api() -> tuple[_KubernetesSandboxAPI, MagicMock]:
    custom = MagicMock()
    custom.patch_namespaced_custom_object = AsyncMock(return_value=None)
    custom.delete_namespaced_custom_object = AsyncMock(return_value=None)
    custom.get_namespaced_custom_object = AsyncMock(
        return_value={"spec": {"replicas": 1}}
    )
    core = MagicMock()
    core.read_namespaced_pod = AsyncMock(return_value=type(
        "P", (), {"status": type("S", (), {"phase": "Running"})()}
    )())
    api = _KubernetesSandboxAPI(
        custom_api=custom,
        core_api=core,
        namespace="centaur-system",
    )
    return api, custom


@pytest.mark.asyncio
async def test_pause_patches_replicas_zero() -> None:
    api, custom = _mk_api()
    await api.pause_sandbox("sbx-1")
    custom.patch_namespaced_custom_object.assert_awaited_once()
    args = custom.patch_namespaced_custom_object.call_args.args
    assert args[0] == "agents.x-k8s.io"
    assert args[1] == "v1alpha1"
    assert args[2] == "centaur-system"
    assert args[3] == "sandboxes"
    assert args[4] == "sbx-1"
    assert args[5] == {"spec": {"replicas": 0}}
    # Merge-patch content type per upstream (kubernetes_agent_sandbox.py:171).
    assert custom.patch_namespaced_custom_object.call_args.kwargs == {
        "_content_type": "application/merge-patch+json",
    }


@pytest.mark.asyncio
async def test_resume_patches_replicas_one() -> None:
    api, custom = _mk_api()
    await api.resume_sandbox("sbx-1")
    custom.patch_namespaced_custom_object.assert_awaited_once()
    body = custom.patch_namespaced_custom_object.call_args.args[5]
    assert body == {"spec": {"replicas": 1}}


@pytest.mark.asyncio
async def test_stop_deletes_crd_and_swallows_404() -> None:
    api, custom = _mk_api()
    await api.stop_sandbox("sbx-1")
    custom.delete_namespaced_custom_object.assert_awaited_once()
    args = custom.delete_namespaced_custom_object.call_args.args
    assert args[:4] == ("agents.x-k8s.io", "v1alpha1", "centaur-system", "sandboxes")
    assert args[4] == "sbx-1"


@pytest.mark.asyncio
async def test_stop_is_idempotent_on_404() -> None:
    api, custom = _mk_api()
    exc = type("E", (Exception,), {"status": 404})
    custom.delete_namespaced_custom_object.side_effect = exc()
    # Must not raise.
    await api.stop_sandbox("sbx-1")
```

- [ ] **Step 4: Write the failing exec test**

`overlay/tools/bfts_executor/tests/test_kubernetes_api_calls.py`:

```python
"""Test: run_command drives the WsApiClient exec pattern correctly.

We mock the websocket loop so the test exercises every channel branch
(STDOUT_CHANNEL, STDERR_CHANNEL, ERROR_CHANNEL) and the exit-code
parse via parse_error_data. Mirrors .centaur/services/api/api/sandbox/
kubernetes.py:1525-1551.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import _KubernetesSandboxAPI


class _FakeMsg:
    def __init__(self, type_: int, data: bytes) -> None:
        self.type = type_
        self.data = data


class _FakeWS:
    def __init__(self, frames: list[_FakeMsg]) -> None:
        self._frames = list(frames)

    async def __aenter__(self) -> "_FakeWS":
        return self

    async def __aexit__(self, *a) -> None:
        return None

    async def receive(self) -> _FakeMsg:
        if not self._frames:
            from aiohttp import WSMsgType
            return _FakeMsg(WSMsgType.CLOSED, b"")
        return self._frames.pop(0)


@pytest.mark.asyncio
async def test_run_command_aggregates_stdout_and_returns_exit_zero() -> None:
    from aiohttp import WSMsgType
    from kubernetes_asyncio.stream.ws_client import STDOUT_CHANNEL

    ws_core = MagicMock()

    async def _connect(*args, **kwargs):
        return _FakeWS([
            _FakeMsg(WSMsgType.BINARY, bytes([STDOUT_CHANNEL]) + b"hello\n"),
        ])

    ws_core.connect_get_namespaced_pod_exec = _connect
    ws_api = MagicMock()
    ws_api.parse_error_data = MagicMock(return_value=0)
    api = _KubernetesSandboxAPI(
        ws_core_api=ws_core,
        ws_api_client=ws_api,
        namespace="centaur-system",
    )
    res = await api.run_command("sbx-1", "echo hello", timeout_s=10.0)
    assert res.stdout == "hello\n"
    assert res.stderr == ""
    assert res.exit_code == 0


@pytest.mark.asyncio
async def test_run_command_captures_stderr_channel() -> None:
    from aiohttp import WSMsgType
    from kubernetes_asyncio.stream.ws_client import STDERR_CHANNEL

    ws_core = MagicMock()

    async def _connect(*args, **kwargs):
        return _FakeWS([
            _FakeMsg(WSMsgType.BINARY, bytes([STDERR_CHANNEL]) + b"boom\n"),
        ])

    ws_core.connect_get_namespaced_pod_exec = _connect
    ws_api = MagicMock()
    ws_api.parse_error_data = MagicMock(return_value=0)
    api = _KubernetesSandboxAPI(
        ws_core_api=ws_core,
        ws_api_client=ws_api,
        namespace="centaur-system",
    )
    res = await api.run_command("sbx-1", "false", timeout_s=10.0)
    assert res.stderr == "boom\n"


@pytest.mark.asyncio
async def test_run_command_extracts_exit_code_from_error_channel() -> None:
    from aiohttp import WSMsgType
    from kubernetes_asyncio.stream.ws_client import ERROR_CHANNEL

    ws_core = MagicMock()
    payload = b'{"status":"Failure","reason":"NonZeroExitCode","details":{"causes":[{"reason":"ExitCode","message":"42"}]}}'

    async def _connect(*args, **kwargs):
        return _FakeWS([
            _FakeMsg(WSMsgType.BINARY, bytes([ERROR_CHANNEL]) + payload),
        ])

    ws_core.connect_get_namespaced_pod_exec = _connect
    ws_api = MagicMock()
    ws_api.parse_error_data = MagicMock(return_value=42)
    api = _KubernetesSandboxAPI(
        ws_core_api=ws_core,
        ws_api_client=ws_api,
        namespace="centaur-system",
    )
    res = await api.run_command("sbx-1", "exit 42", timeout_s=10.0)
    assert res.exit_code == 42
    ws_api.parse_error_data.assert_called_once()


@pytest.mark.asyncio
async def test_namespace_defaults_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUBERNETES_NAMESPACE", "centaur-test")
    api = _KubernetesSandboxAPI()
    assert api.namespace == "centaur-test"
```

- [ ] **Step 5: Run the tests to confirm they fail**

Update `overlay/tools/bfts_executor/pyproject.toml`: add `aiohttp` to the dependencies list (the dependency block already declared in Task 1.1):

```toml
dependencies = [
    "kubernetes-asyncio>=29.0.0",
    "aiohttp>=3.9.0",
    "dataclasses-json>=0.6.0",
]
```

Then:

```bash
cd overlay/tools/bfts_executor && uv run --python 3.11 --with pytest --with pytest-asyncio --with dataclasses-json --with kubernetes-asyncio --with aiohttp pytest tests/test_create_sandbox_body.py tests/test_sandbox_lifecycle.py tests/test_kubernetes_api_calls.py -v
```

Expected: FAIL with `ImportError: cannot import name '_KubernetesSandboxAPI' from 'client'`.

- [ ] **Step 6: Implement `_KubernetesSandboxAPI`**

Replace (do not append — fully replace any prior simple-exec implementation) the bottom of `overlay/tools/bfts_executor/client.py` with the following block. Lifted-from-upstream structures are flagged in comment headers so reviewers know what to compare against.

```python
import os
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class _RealPodExecResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_s: float


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
```

Also extend `BFTSExecutor` (defined in Task 1.3) so workflow code can call `executor.create_sandbox(...)` / `pause_sandbox(...)` / `resume_sandbox(...)` / `stop_sandbox(...)` directly. Add these methods inside `BFTSExecutor` after `exec_python`:

```python
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
```

Update the `_SandboxAPI` Protocol at the top of `client.py` to include the new methods (otherwise type checkers complain when the workflow calls `executor.create_sandbox`). Replace the existing Protocol block:

```python
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
```

Confirm the Task 1.3 `WORKING_DIR` constant at the top of `client.py` is already `"/workspace/working"` (set in Task 1.3 — the bfts-executor image's `WORKDIR /workspace` makes Sakana's `os.path.join(os.getcwd(), 'working')` resolve here). Then re-run the Task 1.3/1.5 tests as a regression check:

```bash
cd overlay/tools/bfts_executor && uv run --python 3.11 --with pytest --with pytest-asyncio --with dataclasses-json pytest tests/test_exec_python_happy.py tests/test_collect_artifacts.py -v
```

Expected: still 3 + 1 passing.

- [ ] **Step 7: Run the new tests to verify they pass**

```bash
cd overlay/tools/bfts_executor && uv run --python 3.11 --with pytest --with pytest-asyncio --with dataclasses-json --with kubernetes-asyncio --with aiohttp pytest tests/ -v
```

Expected: every test in the tool's `tests/` directory passes — `test_create_sandbox_body.py` (2), `test_sandbox_lifecycle.py` (4), `test_kubernetes_api_calls.py` (4), `test_execution_result_shape.py` (3), `test_exec_python_happy.py` (3), `test_exec_python_timeout.py` (2), `test_collect_artifacts.py` (1).

- [ ] **Step 8: Commit**

```bash
git add overlay/Dockerfile.bfts-executor Justfile \
        overlay/tools/bfts_executor/client.py \
        overlay/tools/bfts_executor/pyproject.toml \
        overlay/tools/bfts_executor/tests/test_create_sandbox_body.py \
        overlay/tools/bfts_executor/tests/test_sandbox_lifecycle.py \
        overlay/tools/bfts_executor/tests/test_kubernetes_api_calls.py \
        overlay/tools/bfts_executor/tests/test_exec_python_happy.py \
        overlay/tools/bfts_executor/tests/test_collect_artifacts.py
git commit -m "feat(bfts): real Kubernetes-backed sandbox API — CRD lifecycle + WsApiClient exec"
```

---

## Task 1.7: Idempotent `bfts-sandbox-egress` NetworkPolicy

**Why:** Spec correction #13 — BFTS pods need additive egress allow rules (TCP/8000 to api for status callbacks; TCP/443 to internet for datasets + PyPI) layered onto the chart's existing default-deny (`.centaur/contrib/chart/templates/networkpolicy.yaml:9-13`) and `-allow-dns` (L15-34). K8s NetworkPolicy is union-based, so a single namespace-scoped `Egress`-only rule selecting `centaur.ai/bfts-sandbox: "true"` is sufficient and additive. We deliberately do NOT add `centaur.ai/managed: "true"` to BFTS pods because that label is the podSelector for the chart's `-sandbox` policy at L307-327 which locks egress to api:8000 only. Idempotency via 409-catch follows the same pattern Centaur uses elsewhere (`.centaur/services/api/api/sandbox/kubernetes.py` proxy NetworkPolicy creation at L696-816 uses delete-then-create; we use try-create-catch-409 because BFTS is single-namespace and many concurrent runs will try to create the same policy).

**Files:**
- Create: `overlay/tools/bfts_executor/network_policy.py`
- Create: `overlay/tools/bfts_executor/tests/test_network_policy.py`
- Modify: `overlay/tools/bfts_executor/client.py` (call `ensure_sandbox_egress_policy` from `create_sandbox`)

- [ ] **Step 1: Write the failing test**

`overlay/tools/bfts_executor/tests/test_network_policy.py`:

```python
"""Test: ensure_sandbox_egress_policy is idempotent + carries correct rules."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from network_policy import (
    POLICY_NAME,
    ensure_sandbox_egress_policy,
)


@pytest.mark.asyncio
async def test_creates_policy_with_bfts_selector_and_additive_egress() -> None:
    api = MagicMock()
    api.create_namespaced_network_policy = AsyncMock(return_value=None)
    await ensure_sandbox_egress_policy(api, namespace="centaur-system")
    api.create_namespaced_network_policy.assert_awaited_once()
    args = api.create_namespaced_network_policy.call_args.args
    assert args[0] == "centaur-system"
    body = args[1]
    assert body["apiVersion"] == "networking.k8s.io/v1"
    assert body["kind"] == "NetworkPolicy"
    assert body["metadata"]["name"] == POLICY_NAME == "bfts-sandbox-egress"
    spec = body["spec"]
    assert spec["podSelector"]["matchLabels"] == {"centaur.ai/bfts-sandbox": "true"}
    # Egress-only — Ingress + DNS are covered by the chart's default-deny
    # + -allow-dns policies (.centaur/contrib/chart/templates/
    # networkpolicy.yaml:9-34).
    assert spec["policyTypes"] == ["Egress"]
    # Two rules: api:8000 + internet:443.
    rules = spec["egress"]
    assert len(rules) == 2
    api_rule = rules[0]
    assert api_rule["ports"] == [{"protocol": "TCP", "port": 8000}]
    assert any(
        peer.get("podSelector", {}).get("matchLabels", {}).get(
            "app.kubernetes.io/component"
        )
        == "api"
        for peer in api_rule["to"]
    )
    internet_rule = rules[1]
    assert internet_rule["ports"] == [{"protocol": "TCP", "port": 443}]
    assert "to" not in internet_rule or internet_rule["to"] == []


@pytest.mark.asyncio
async def test_409_conflict_is_silent_idempotent() -> None:
    api = MagicMock()
    conflict = type("E", (Exception,), {"status": 409})()
    api.create_namespaced_network_policy = AsyncMock(side_effect=conflict)
    # Must not raise.
    await ensure_sandbox_egress_policy(api, namespace="centaur-system")


@pytest.mark.asyncio
async def test_other_status_codes_propagate() -> None:
    api = MagicMock()
    err = type("E", (Exception,), {"status": 500})()
    api.create_namespaced_network_policy = AsyncMock(side_effect=err)
    with pytest.raises(Exception):
        await ensure_sandbox_egress_policy(api, namespace="centaur-system")
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd overlay/tools/bfts_executor && uv run --python 3.11 --with pytest --with pytest-asyncio --with kubernetes-asyncio --with aiohttp pytest tests/test_network_policy.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'network_policy'`.

- [ ] **Step 3: Implement `network_policy.py`**

`overlay/tools/bfts_executor/network_policy.py`:

```python
"""Idempotent `bfts-sandbox-egress` NetworkPolicy.

The chart-shipped default-deny (.centaur/contrib/chart/templates/
networkpolicy.yaml:9-13) blocks all traffic, and `-allow-dns` (L15-34)
re-allows kube-dns. K8s NetworkPolicies are union-based, so adding this
namespace-scoped Egress-only rule on top is additive: pods labeled
`centaur.ai/bfts-sandbox: "true"` get api:8000 + internet:443 in
addition to DNS, while ingress remains denied.

We deliberately do NOT add `centaur.ai/managed: "true"` to BFTS pods —
that label is the podSelector for the chart's `-sandbox` policy at L307-
327 which restricts egress to api:8000 only and would block PyPI /
dataset fetches.

RBAC: `.centaur/contrib/chart/templates/rbac.yaml:39-41` already grants
the api service account create/delete/get/list/watch on
networking.k8s.io/networkpolicies.
"""
from __future__ import annotations

from typing import Any

POLICY_NAME = "bfts-sandbox-egress"


def _is_conflict(exc: BaseException) -> bool:
    return getattr(exc, "status", None) == 409


def _build_body() -> dict[str, Any]:
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": POLICY_NAME,
            "labels": {"centaur.ai/bfts": "true"},
        },
        "spec": {
            "podSelector": {"matchLabels": {"centaur.ai/bfts-sandbox": "true"}},
            "policyTypes": ["Egress"],
            "egress": [
                {
                    "to": [
                        {
                            "podSelector": {
                                "matchLabels": {
                                    "app.kubernetes.io/component": "api",
                                }
                            }
                        }
                    ],
                    "ports": [{"protocol": "TCP", "port": 8000}],
                },
                {
                    "ports": [{"protocol": "TCP", "port": 443}],
                },
            ],
        },
    }


async def ensure_sandbox_egress_policy(
    networking_api: Any, *, namespace: str
) -> None:
    """Create the policy if missing; swallow 409 if it already exists."""
    try:
        await networking_api.create_namespaced_network_policy(namespace, _build_body())
    except Exception as exc:
        if not _is_conflict(exc):
            raise
```

Wire it into `create_sandbox` (modify `overlay/tools/bfts_executor/client.py`). Inside `_KubernetesSandboxAPI.create_sandbox`, **immediately after** the `await self._ensure_clients()` call, insert:

```python
        from network_policy import ensure_sandbox_egress_policy

        await ensure_sandbox_egress_policy(
            self.networking_api, namespace=self.namespace
        )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd overlay/tools/bfts_executor && uv run --python 3.11 --with pytest --with pytest-asyncio --with kubernetes-asyncio --with aiohttp pytest tests/test_network_policy.py tests/test_create_sandbox_body.py -v
```

Expected: 3 + 2 = 5 passed.

- [ ] **Step 5: Commit**

```bash
git add overlay/tools/bfts_executor/network_policy.py \
        overlay/tools/bfts_executor/tests/test_network_policy.py \
        overlay/tools/bfts_executor/client.py
git commit -m "feat(bfts): idempotent bfts-sandbox-egress NetworkPolicy"
```

---

## Task 1.8: Pause/resume retention round-trip smoke

**Why:** Spec correction #12 says PVC retention across pause/resume is shipped already (`shutdownPolicy: "Retain"` + `replicas: 0|1` patch — `.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:114, 159-185`); this task PROVES it end-to-end against the real cluster with the BFTS executor we just built. Without this smoke we have unit tests of the CRD body but no evidence that BFTS state actually survives a pause cycle, which is the entire reason we use the agent-sandbox controller.

**Files:**
- Modify: `Justfile` (add `bfts-retention-smoke` recipe)

- [ ] **Step 1: Define the verification command**

The recipe should, from inside the api pod where `BFTSExecutor` and its `_KubernetesSandboxAPI` already have a kubeconfig:
1. Create a Sandbox via `await ctx.tools.bfts_executor.create_sandbox(...)` — but the workflow doesn't exist yet, so we run the script directly via `kubectl exec deploy/<api> -- python -c '<script>'` to drive the tool out-of-band.
2. Write `RETENTION_OK` to `/workspace/sentinel.txt`.
3. Pause the sandbox (`replicas: 0`).
4. Resume the sandbox (`replicas: 1` + wait-ready).
5. Read `/workspace/sentinel.txt` back and assert it still says `RETENTION_OK`.
6. Stop the sandbox (delete CRD, PVC reaped via owner refs).

Verification: `just bfts-retention-smoke` exits 0 and prints `RETENTION SMOKE OK`.

- [ ] **Step 2: Run before the edit to confirm it fails**

```bash
just bfts-retention-smoke
```

Expected: `error: Justfile does not contain recipe 'bfts-retention-smoke'`.

- [ ] **Step 3: Add the recipe**

Append to `Justfile`:

```just
# Phase 1 end-to-end: prove that BFTS sandbox PVC retention works
# across pause/resume. Drives BFTSExecutor (already deployed in the
# overlay image) from inside the api pod via `kubectl exec`. See
# docs/superpowers/plans/2026-05-25-bfts-on-centaur.md (Phase 1 Task 1.8).
[group('bfts')]
bfts-retention-smoke:
    #!/usr/bin/env bash
    set -euo pipefail
    api_deploy="deploy/${CENTAUR_RELEASE}-centaur-api"
    sandbox_id="bfts-retention-smoke-$(date +%s)"
    py="$(cat <<'PY'
    import asyncio, os, sys
    sys.path.insert(0, "/app/overlay/org/tools")
    from bfts_executor.client import BFTSExecutor, _KubernetesSandboxAPI

    async def main(sandbox_id: str) -> None:
        api = _KubernetesSandboxAPI()
        executor = BFTSExecutor(sandbox_api=api)
        try:
            await executor.create_sandbox(
                sandbox_id, run_id="retention-smoke"
            )
            await api.write_file(
                sandbox_id, "/workspace/sentinel.txt", "RETENTION_OK"
            )
            await executor.pause_sandbox(sandbox_id)
            await executor.resume_sandbox(sandbox_id)
            res = await api.run_command(
                sandbox_id, "cat /workspace/sentinel.txt", timeout_s=10.0
            )
            if res.stdout.strip() != "RETENTION_OK":
                raise SystemExit(
                    f"sentinel mismatch: '{res.stdout!r}' exit={res.exit_code}"
                )
            print("RETENTION SMOKE OK")
        finally:
            await executor.stop_sandbox(sandbox_id)

    asyncio.run(main(os.environ["SANDBOX_ID"]))
    PY
    )"
    kubectl -n $CENTAUR_NAMESPACE exec "$api_deploy" -c api \
        -- env SANDBOX_ID="$sandbox_id" python -c "$py"
```

- [ ] **Step 4: Build + deploy + run the smoke**

```bash
just bfts-build-executor
just overlay::build
just deploy
kubectl rollout status -n centaur-system deploy/centaur-centaur-api --timeout=120s
just bfts-retention-smoke
```

Expected output (final line): `RETENTION SMOKE OK`. If pause/resume hangs, check `kubectl -n $CENTAUR_NAMESPACE get sandbox.agents.x-k8s.io -l centaur.ai/bfts-sandbox=true -o yaml | grep -A2 replicas` to see what state the CR is in.

- [ ] **Step 5: Commit**

```bash
git add Justfile
git commit -m "feat(bfts): add bfts-retention-smoke recipe (pause/resume round-trip)"
```

---

## Task 1.9: Wire `BFTSExecutor` as a real Centaur tool

**Why:** Tasks 1.1–1.8 produced a Python class with unit tests and a passing pause/resume retention smoke; for the workflow to call it as `await ctx.tools.bfts_executor.create_sandbox(...)` / `exec_python(...)` / etc., we need the module-level `_client()` factory that Centaur's tool loader expects (research 03 §Tool programming model). This is a 3-line glue addition.

**Files:**
- Modify: `overlay/tools/bfts_executor/client.py`

- [ ] **Step 1: Verification command**

Centaur's tool discovery imports the module and looks for a callable `_client` (per `.centaur/AGENTS.md` §Tool programming model and the `tools/infra/demo/client.py:14-17` pattern). After deploy, the API logs should show `tool_registered name=bfts_executor`:

```bash
just overlay::build && just deploy
kubectl rollout status -n centaur-system deploy/centaur-centaur-api --timeout=120s
kubectl logs -n centaur-system deploy/centaur-centaur-api --tail=300 | grep -i bfts_executor
```

- [ ] **Step 2: Run before edit to confirm**

Expected: no `tool_registered name=bfts_executor` log line — the module imports fine but has no `_client()`.

- [ ] **Step 3: Add `_client()`**

Append to `overlay/tools/bfts_executor/client.py`:

```python
def _client() -> BFTSExecutor:
    """Centaur tool factory: invoked once per API pod at discovery time."""
    return BFTSExecutor(sandbox_api=_KubernetesSandboxAPI())
```

- [ ] **Step 4: Re-deploy + verify**

```bash
just overlay::build && just deploy
kubectl rollout status -n centaur-system deploy/centaur-centaur-api --timeout=120s
kubectl logs -n centaur-system deploy/centaur-centaur-api --tail=300 | grep -i bfts_executor
```

Expected: at least one log line including `bfts_executor` (the exact event name depends on Centaur's tool registration logging; if no log line, run `kubectl exec deploy/centaur-centaur-api -- curl -s http://localhost:8000/tools | jq '.tools[] | select(.name=="bfts_executor")'` and expect a non-empty object).

- [ ] **Step 5: Commit**

```bash
git add overlay/tools/bfts_executor/client.py
git commit -m "feat(bfts): register BFTSExecutor as a Centaur tool"
```

---

# Phase 2 — Tree controller (MVP, Stage 1 only)

This phase ships the durable workflow that ports Sakana's Stage 1 search. The controller is `bfts_tree.py`; the entrypoint that fans out `num_drafts` trees is `bfts_root.py`. Tree state lives in a `bfts_nodes` table (overlay-owned). Per-expansion work is a series of `ctx.step(...)` calls into LLM-call helpers and the `bfts_executor` tool — each step is its own checkpoint so workflow restart resumes mid-expansion (research 02 §Mapping to the Centaur workflow + research 03 §State & durability storage).

## Task 2.1: Overlay migration for `bfts_runs`, `bfts_nodes`, `bfts_artifacts`

**Why:** Per research 03 §Gotchas — do **not** store the tree as one growing JSONB checkpoint. Own a table; checkpoint IDs only.

**Files:**
- Create: `services/api/db/migrations/20260525000001_add_bfts_tables.sql` (the host-side path the dbmate wrapper writes to when `CENTAUR_OVERLAY_HOST_DIR=$(pwd)`)
- Create: `overlay/services/api/db/migrations/20260525000001_add_bfts_tables.sql` (the in-image path the overlay mount serves at runtime; ship as a symlink)
- Modify: `overlay/Dockerfile` to `COPY services /overlay/services`

- [ ] **Step 1: Define the verification command**

```bash
./.centaur/contrib/scripts/dbmate --set overlay status 2>&1
```

Expected (before the migration): the wrapper bails with `overlay migrations dir not found: <path>/services/api/db/migrations` (per `.centaur/contrib/scripts/dbmate:63`).

- [ ] **Step 2: Run to confirm**

```bash
CENTAUR_OVERLAY_HOST_DIR=$(pwd) ./.centaur/contrib/scripts/dbmate --set overlay status
```

Expected: the error above.

- [ ] **Step 3: Create directories + migration file**

```bash
mkdir -p services/api/db/migrations
mkdir -p overlay/services/api/db/migrations
```

`services/api/db/migrations/20260525000001_add_bfts_tables.sql`:

```sql
-- migrate:up
-- BFTS-on-Centaur tree state (Phase 2).
--
-- The tree itself lives here; the workflow's checkpoints hold only IDs
-- pointing into these tables. This keeps each workflow_checkpoints row
-- tiny (one JSON object per ctx.step, per research 03 §State & durability
-- storage).

CREATE TABLE bfts_runs (
    run_id          TEXT PRIMARY KEY,                      -- == Centaur workflow_runs.run_id
    parent_run_id   TEXT,                                   -- bfts_root run if this is a tree
    idea_json       JSONB NOT NULL,
    config_json     JSONB NOT NULL,                         -- flattened bfts_config
    stage_name      TEXT NOT NULL DEFAULT 'stage_1',
    seed            INT NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'running',
    best_node_id    TEXT,                                   -- set by export_best on terminal
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE bfts_nodes (
    node_id              TEXT PRIMARY KEY,                  -- uuid4().hex
    run_id               TEXT NOT NULL REFERENCES bfts_runs(run_id) ON DELETE CASCADE,
    parent_node_id       TEXT REFERENCES bfts_nodes(node_id) ON DELETE CASCADE,
    step                 INT NOT NULL,                       -- assigned-on-append (Sakana Journal semantics)
    stage_name           TEXT NOT NULL,                      -- 'draft' | 'debug' | 'improve'
    plan                 TEXT NOT NULL DEFAULT '',
    code                 TEXT NOT NULL DEFAULT '',
    plot_code            TEXT,
    term_out_json        JSONB,                              -- list[str]
    exec_time_seconds    DOUBLE PRECISION,
    exc_type             TEXT,
    exc_info_json        JSONB,
    exc_stack_json       JSONB,
    parse_metrics_code   TEXT NOT NULL DEFAULT '',
    parse_term_out_json  JSONB,
    parse_exc_type       TEXT,
    plot_term_out_json   JSONB,
    plot_exec_time_seconds DOUBLE PRECISION,
    plot_exc_type        TEXT,
    analysis             TEXT,                               -- LLM bug summary
    metric_json          JSONB,                              -- nested MetricValue dict (research 02 §MetricValue)
    is_buggy             BOOLEAN,                            -- NULL = not yet executed
    is_buggy_plots       BOOLEAN,                            -- VLM gate (Phase 3 writes this)
    plot_analyses_json   JSONB,
    vlm_feedback_summary TEXT,
    debug_depth          INT NOT NULL DEFAULT 0,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX bfts_nodes_run_idx ON bfts_nodes(run_id, step);
CREATE INDEX bfts_nodes_parent_idx ON bfts_nodes(parent_node_id);

CREATE TABLE bfts_artifacts (
    artifact_id   TEXT PRIMARY KEY,
    node_id       TEXT NOT NULL REFERENCES bfts_nodes(node_id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,                              -- 'experiment_data' | 'plot' | 'code'
    relative_path TEXT NOT NULL,                              -- e.g. 'experiment_<id>/loss_curve.png'
    bytes         BYTEA NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (node_id, relative_path)
);

-- migrate:down
DROP TABLE bfts_artifacts;
DROP TABLE bfts_nodes;
DROP TABLE bfts_runs;
```

Then symlink so the overlay image has the same file at the in-pod path the dbmate wrapper expects:

```bash
ln -s ../../../../../services/api/db/migrations/20260525000001_add_bfts_tables.sql \
      overlay/services/api/db/migrations/20260525000001_add_bfts_tables.sql
```

(Confirm the symlink target resolves: `ls -la overlay/services/api/db/migrations/`.)

- [ ] **Step 4: Update `overlay/Dockerfile`**

Append after the existing `COPY .agents /overlay/.agents` line:

```dockerfile
COPY services /overlay/services
```

- [ ] **Step 5: Apply the migration**

```bash
just overlay::build
just deploy
kubectl rollout status -n centaur-system deploy/centaur-centaur-api --timeout=120s
CENTAUR_OVERLAY_HOST_DIR=$(pwd) ./.centaur/contrib/scripts/dbmate --set overlay up
```

Expected output (final line): `Applied: 20260525000001_add_bfts_tables.sql`.

Confirm tables exist:

```bash
kubectl exec -n centaur-system deploy/centaur-centaur-api -- \
  psql "$DATABASE_URL" -c "\dt bfts_*"
```

Expected: three rows — `bfts_runs`, `bfts_nodes`, `bfts_artifacts`.

- [ ] **Step 6: Commit**

```bash
git add services/api/db/migrations/20260525000001_add_bfts_tables.sql \
        overlay/services/api/db/migrations/20260525000001_add_bfts_tables.sql \
        overlay/Dockerfile
git commit -m "feat(bfts): add bfts_runs/bfts_nodes/bfts_artifacts tables"
```

---

## Task 2.2: `MetricValue` helper (pure Python)

**Why:** The `metric_json` column stores Sakana's nested-dict shape. The workflow needs a tiny pure-Python helper to compute `mean()` for the deterministic best-node argmax — and the same helper documents the known footgun (`lower_is_better` from the first metric only) per research 02 §`MetricValue`.

**Files:**
- Create: `overlay/workflows/_bfts_metric.py`
- Create: `overlay/workflows/tests/test_bfts_metric.py`

- [ ] **Step 1: Write the failing test**

`overlay/workflows/tests/test_bfts_metric.py`:

```python
"""Test: _bfts_metric.mean() collapses Sakana nested-dict metrics correctly."""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_metric import (
    WORST_METRIC,
    direction_lower_is_better,
    is_worst,
    mean,
)


def test_mean_single_metric_single_dataset() -> None:
    m = {
        "metric_names": [
            {
                "metric_name": "val_loss",
                "lower_is_better": True,
                "description": "validation loss",
                "data": [{"dataset_name": "ds", "final_value": 0.4, "best_value": 0.3}],
            }
        ]
    }
    assert mean(m) == 0.4


def test_mean_collapses_across_datasets() -> None:
    m = {
        "metric_names": [
            {
                "metric_name": "val_loss",
                "lower_is_better": True,
                "description": "",
                "data": [
                    {"dataset_name": "ds1", "final_value": 0.2, "best_value": 0.1},
                    {"dataset_name": "ds2", "final_value": 0.4, "best_value": 0.3},
                ],
            }
        ]
    }
    assert math.isclose(mean(m), 0.3)


def test_direction_taken_from_first_metric_only() -> None:
    """Known footgun (research 02 §MetricValue): first metric's
    lower_is_better governs comparison across ALL metrics."""
    m = {
        "metric_names": [
            {"metric_name": "val_loss", "lower_is_better": True, "description": "", "data": []},
            {"metric_name": "val_acc", "lower_is_better": False, "description": "", "data": []},
        ]
    }
    assert direction_lower_is_better(m) is True


def test_worst_metric_compares_worst() -> None:
    real = {"metric_names": [{"metric_name": "x", "lower_is_better": True, "description": "", "data": [{"dataset_name": "d", "final_value": 0.5, "best_value": 0.5}]}]}
    assert is_worst(WORST_METRIC)
    assert not is_worst(real)
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio pytest tests/test_bfts_metric.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named '_bfts_metric'`.

- [ ] **Step 3: Implement `_bfts_metric.py`**

`overlay/workflows/_bfts_metric.py`:

```python
"""Pure-Python MetricValue helpers (Sakana nested-dict shape).

KNOWN FOOTGUN (deferred fix tracked in Phase 4): the direction of
comparison is taken from the FIRST metric's ``lower_is_better`` and
applied to all metrics + datasets. A node returning
``[val_loss↓, val_acc↑]`` will compare accuracy as if lower were better.
Mirrored 1:1 from .scientist/ai_scientist/treesearch/utils/metric.py
:191-203 to preserve Sakana behavior at the MVP boundary; research 02
§Tree data model and §Gotcha #7.

Underscore-prefixed module name so the API's workflow loader skips it
(research 03 §Tool programming model).
"""
from __future__ import annotations

from typing import Any


WORST_METRIC: dict[str, Any] = {"_worst": True}
"""Sentinel that compares worse than any real metric.

Assigned on any failure path (buggy exec, metric-parse failure, plot
failure). Equivalent to Sakana's WorstMetricValue
(.scientist/ai_scientist/treesearch/utils/metric.py:327-341)."""


def is_worst(metric: dict[str, Any] | None) -> bool:
    return metric is None or bool(metric.get("_worst"))


def direction_lower_is_better(metric: dict[str, Any]) -> bool:
    names = metric.get("metric_names") or []
    if not names:
        return True
    return bool(names[0].get("lower_is_better", True))


def mean(metric: dict[str, Any]) -> float:
    """Mean of all final_values across all metrics and datasets.

    Returns +inf for is_worst() so argmax(-mean) never picks it.
    """
    if is_worst(metric):
        return float("inf")
    values: list[float] = []
    for entry in metric.get("metric_names") or []:
        for ds in entry.get("data") or []:
            v = ds.get("final_value")
            if isinstance(v, (int, float)):
                values.append(float(v))
    if not values:
        return float("inf")
    return sum(values) / len(values)


def score(metric: dict[str, Any]) -> float:
    """Sortable score: lower is better => return mean; higher is better => return -mean.

    The best node is the one with the LOWEST score(); use argmin.
    """
    if is_worst(metric):
        return float("inf")
    m = mean(metric)
    return m if direction_lower_is_better(metric) else -m
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio pytest tests/test_bfts_metric.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add overlay/workflows/_bfts_metric.py overlay/workflows/tests/test_bfts_metric.py
git commit -m "feat(bfts): add MetricValue helper (Sakana shape + known footgun)"
```

---

## Task 2.3: Selection function `_bfts_select.select_next`

**Why:** Sakana's `_select_parallel_nodes` (`parallel_agent.py:1931-2051`) is the inner BFTS scheduler. As a pure function it can be unit-tested without a workflow or sandbox.

**Files:**
- Create: `overlay/workflows/_bfts_select.py`
- Create: `overlay/workflows/tests/test_bfts_select.py`

- [ ] **Step 1: Write the failing test**

`overlay/workflows/tests/test_bfts_select.py`:

```python
"""Property tests for _bfts_select.select_next (Sakana parity)."""
from __future__ import annotations

import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_select import NodeRef, SearchConfig, select_next


@dataclass
class _N:
    """Minimal node shape mirroring the bfts_nodes row columns the
    selector reads. Keeps the test isolated from the real DAO."""

    node_id: str
    parent_id: Optional[str]
    is_buggy: Optional[bool]
    is_buggy_plots: Optional[bool]
    debug_depth: int
    metric_score: float                                # _bfts_metric.score(...)
    stage_name: str = "draft"
    is_leaf: bool = True

    def to_ref(self) -> NodeRef:
        return NodeRef(
            node_id=self.node_id,
            parent_id=self.parent_id,
            root_id=self.node_id if self.parent_id is None else "ROOT",
            is_buggy=self.is_buggy,
            is_buggy_plots=self.is_buggy_plots,
            debug_depth=self.debug_depth,
            metric_score=self.metric_score,
            stage_name=self.stage_name,
            is_leaf=self.is_leaf,
        )


def test_drafts_until_num_drafts_reached() -> None:
    cfg = SearchConfig(num_drafts=3, num_workers=4, max_debug_depth=3, debug_prob=0.0)
    rng = random.Random(0)
    # No nodes yet: selector must produce 3 None entries (each = "new draft"),
    # plus a 4th entry that's also None (still drafting until num_drafts).
    selected = select_next(nodes=[], cfg=cfg, rng=rng)
    assert selected == [None, None, None, None]


def test_no_debug_when_prob_is_zero_and_good_node_exists() -> None:
    cfg = SearchConfig(num_drafts=2, num_workers=2, max_debug_depth=3, debug_prob=0.0)
    rng = random.Random(0)
    nodes = [
        _N("d1", None, is_buggy=False, is_buggy_plots=False, debug_depth=0, metric_score=0.5).to_ref(),
        _N("d2", None, is_buggy=False, is_buggy_plots=False, debug_depth=0, metric_score=0.7).to_ref(),
    ]
    selected = select_next(nodes=nodes, cfg=cfg, rng=rng)
    # Expectation: improve the best of each tree (one slot per root, per
    # Sakana's "one node per tree per step").
    ids = [n.node_id if n else None for n in selected]
    assert set(ids) == {"d1", "d2"}


def test_debug_chosen_when_prob_is_one_and_buggy_leaf_exists() -> None:
    cfg = SearchConfig(num_drafts=1, num_workers=1, max_debug_depth=3, debug_prob=1.0)
    rng = random.Random(0)
    nodes = [
        _N("d1", None, is_buggy=True, is_buggy_plots=None, debug_depth=0, metric_score=float("inf")).to_ref(),
    ]
    selected = select_next(nodes=nodes, cfg=cfg, rng=rng)
    assert [n.node_id for n in selected if n] == ["d1"]


def test_max_debug_depth_excludes_node() -> None:
    cfg = SearchConfig(num_drafts=1, num_workers=1, max_debug_depth=3, debug_prob=1.0)
    rng = random.Random(0)
    # debug_depth at the cap (3) → ineligible for further debugging.
    n = _N("dx", "parent", is_buggy=True, is_buggy_plots=None, debug_depth=4, metric_score=float("inf")).to_ref()
    selected = select_next(nodes=[n], cfg=cfg, rng=rng)
    # No debuggable, no good_nodes → fall back to drafting (None).
    assert selected == [None]


def test_seed_determinism() -> None:
    """Same seed + same nodes => same selection."""
    cfg = SearchConfig(num_drafts=2, num_workers=3, max_debug_depth=3, debug_prob=0.5)
    nodes = [
        _N("d1", None, is_buggy=False, is_buggy_plots=False, debug_depth=0, metric_score=0.5).to_ref(),
        _N("d2", None, is_buggy=True, is_buggy_plots=None, debug_depth=0, metric_score=float("inf")).to_ref(),
    ]
    a = select_next(nodes=nodes, cfg=cfg, rng=random.Random(42))
    b = select_next(nodes=nodes, cfg=cfg, rng=random.Random(42))
    assert [n.node_id if n else None for n in a] == [n.node_id if n else None for n in b]
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio pytest tests/test_bfts_select.py -v
```

Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement `_bfts_select.py`**

`overlay/workflows/_bfts_select.py`:

```python
"""Pure-Python BFTS selector (port of Sakana's _select_parallel_nodes).

Selection policy is best-first with debug retries (research 02 §Best-first
expansion algorithm, §Inner loop). Exploration knob is ``debug_prob``;
diversification knob is one-node-per-tree-per-step. Deterministic given
``rng`` — the workflow seeds rng from durable state for replay safety
(research 02 OQ #9).

Underscore-prefixed: workflow loader skips it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from random import Random
from typing import Optional


@dataclass(frozen=True)
class NodeRef:
    node_id: str
    parent_id: Optional[str]
    root_id: str                       # id of the root (draft) ancestor
    is_buggy: Optional[bool]            # None == not yet executed
    is_buggy_plots: Optional[bool]      # None == VLM not yet run
    debug_depth: int
    metric_score: float                 # _bfts_metric.score; lower is better
    stage_name: str
    is_leaf: bool


@dataclass(frozen=True)
class SearchConfig:
    num_drafts: int
    num_workers: int
    max_debug_depth: int
    debug_prob: float


def _draft_nodes(nodes: list[NodeRef]) -> list[NodeRef]:
    return [n for n in nodes if n.parent_id is None]


def _good_nodes(nodes: list[NodeRef]) -> list[NodeRef]:
    return [n for n in nodes if n.is_buggy is False and n.is_buggy_plots is not True]


def _buggy_leaf_nodes(nodes: list[NodeRef], max_depth: int) -> list[NodeRef]:
    return [
        n for n in nodes
        if n.is_buggy is True and n.is_leaf and n.debug_depth <= max_depth
    ]


def select_next(
    *,
    nodes: list[NodeRef],
    cfg: SearchConfig,
    rng: Random,
) -> list[Optional[NodeRef]]:
    """Return ``cfg.num_workers`` selections.

    Each entry is either:
      - ``None``  → instruct the caller to create a new draft node
      - ``NodeRef`` → expand THIS node next (debug or improve depending on
        the node's ``is_buggy``)
    """
    selected: list[Optional[NodeRef]] = []
    processed_roots: set[str] = set()

    drafts = _draft_nodes(nodes)
    viable_roots = {
        d.root_id for d in drafts
        if any(_node_is_viable_leaf(n, d.root_id) for n in nodes)
    }

    while len(selected) < cfg.num_workers:
        if len(drafts) < cfg.num_drafts:
            selected.append(None)
            drafts = drafts + [_phantom_draft(len(drafts))]
            continue

        buggy_leaves = _buggy_leaf_nodes(nodes, cfg.max_debug_depth)
        if buggy_leaves and rng.random() < cfg.debug_prob:
            candidate = rng.choice(buggy_leaves)
            if (
                candidate.root_id not in processed_roots
                or len(processed_roots) >= len(viable_roots)
            ):
                selected.append(candidate)
                processed_roots.add(candidate.root_id)
                continue

        good = _good_nodes(nodes)
        if not good:
            selected.append(None)
            continue

        good_sorted = sorted(good, key=lambda n: n.metric_score)
        # Try to pick best per untaken root.
        picked = None
        for cand in good_sorted:
            if cand.root_id not in processed_roots or len(processed_roots) >= len(viable_roots):
                picked = cand
                break
        if picked is None:
            # No more viable picks for this scheduling pass; emit a draft
            # to fill the slot (matches Sakana's selector fallback to None).
            selected.append(None)
            continue
        selected.append(picked)
        processed_roots.add(picked.root_id)

    return selected


def _phantom_draft(idx: int) -> NodeRef:
    """A placeholder used only by the selector's internal counting; never
    returned to the caller."""
    return NodeRef(
        node_id=f"__phantom_{idx}",
        parent_id=None,
        root_id=f"__phantom_{idx}",
        is_buggy=None,
        is_buggy_plots=None,
        debug_depth=0,
        metric_score=math.inf,
        stage_name="draft",
        is_leaf=True,
    )


def _node_is_viable_leaf(node: NodeRef, root_id: str) -> bool:
    """A root is viable if at least one leaf in its subtree is not buggy."""
    return node.root_id == root_id and (node.is_buggy is False or node.is_buggy is None)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio pytest tests/test_bfts_select.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add overlay/workflows/_bfts_select.py overlay/workflows/tests/test_bfts_select.py
git commit -m "feat(bfts): add pure-Python BFTS selector with seed determinism"
```

---

## Task 2.4: Prompt fragments + function specs

**Why:** Sakana's prompts are nested dicts compiled to Markdown — keeping them as data structures (not f-strings) preserves the compilation contract (research 02 §Prompt structure). For MVP we need: the four function specs (`review_func_spec`, `metric_parse_spec`, `vlm_feedback_spec`, `plot_selection_spec`) and the three prompt fragments (`environment`, `impl_guideline`, `resp_fmt`). Copied near-verbatim from `.scientist/ai_scientist/treesearch/parallel_agent.py:81-451`.

**Files:**
- Create: `overlay/workflows/_bfts_prompts.py`
- Create: `overlay/workflows/tests/test_bfts_prompts.py`

- [ ] **Step 1: Write the failing test**

`overlay/workflows/tests/test_bfts_prompts.py`:

```python
"""Test: prompt fragments compile, function specs round-trip."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_prompts import (
    METRIC_PARSE_SPEC,
    PLOT_SELECTION_SPEC,
    PROMPT_IMPL_GUIDELINE,
    PROMPT_RESP_FMT,
    REVIEW_FUNC_SPEC,
    VLM_FEEDBACK_SPEC,
    compile_prompt_to_md,
)


def test_compile_simple_dict() -> None:
    out = compile_prompt_to_md({"Header": "value"}, depth=1)
    assert "# Header" in out
    assert "value" in out


def test_compile_nested_dict() -> None:
    out = compile_prompt_to_md({"Outer": {"Inner": "v"}}, depth=1)
    assert "# Outer" in out
    assert "## Inner" in out
    assert "v" in out


def test_compile_list_becomes_bullets() -> None:
    out = compile_prompt_to_md({"Items": ["a", "b"]}, depth=1)
    assert "# Items" in out
    assert "- a" in out
    assert "- b" in out


def test_review_func_spec_has_required_fields() -> None:
    props = REVIEW_FUNC_SPEC["function"]["parameters"]["properties"]
    assert "is_bug" in props
    assert props["is_bug"]["type"] == "boolean"
    assert "summary" in props
    assert props["summary"]["type"] == "string"


def test_vlm_feedback_spec_returns_validity_flag() -> None:
    props = VLM_FEEDBACK_SPEC["function"]["parameters"]["properties"]
    assert "valid_plots_received" in props
    assert "vlm_feedback_summary" in props
    assert "plot_analyses" in props


def test_metric_parse_spec_shape() -> None:
    props = METRIC_PARSE_SPEC["function"]["parameters"]["properties"]
    assert "metric_names" in props


def test_plot_selection_spec_present() -> None:
    assert PLOT_SELECTION_SPEC["function"]["name"] == "select_top_plots"


def test_impl_guideline_mentions_experiment_data_npy() -> None:
    assert "experiment_data.npy" in PROMPT_IMPL_GUIDELINE
    assert "working" in PROMPT_IMPL_GUIDELINE


def test_resp_fmt_mentions_single_codeblock() -> None:
    assert "python" in PROMPT_RESP_FMT.lower()
    assert "codeblock" in PROMPT_RESP_FMT.lower() or "code block" in PROMPT_RESP_FMT.lower()
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio pytest tests/test_bfts_prompts.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `_bfts_prompts.py`**

`overlay/workflows/_bfts_prompts.py`:

```python
"""Prompt fragments + OpenAI function specs for BFTS expansion calls.

All data is mirrored from .scientist/ai_scientist/treesearch/
parallel_agent.py:81-451 (research 02 §Agent turn shape, §Prompt
structure). Treat as a contract — every wire-shape downstream
(particularly the metric_parse_spec output) feeds directly into the
metric ingestion path on _bfts_state.

Underscore-prefixed: workflow loader skips it.
"""
from __future__ import annotations

from typing import Any, Iterable, Union

PromptType = Union[str, dict, list]


def compile_prompt_to_md(prompt: PromptType, depth: int = 1) -> str:
    """Compile a nested dict/list/str prompt into markdown.

    Mirrors Sakana's compile_prompt_to_md (backend/utils.py:44-102):
    dict keys become ``#``-headers at the given depth; lists become
    bullet items; strings are emitted as-is. Verbatim parity matters
    because every prompt downstream is built as nested dicts and the
    LLM-prompt-engineering work assumes this exact rendering.
    """
    if isinstance(prompt, str):
        return prompt + "\n"
    if isinstance(prompt, list):
        return "\n".join(f"- {compile_prompt_to_md(p, depth + 1).rstrip()}" for p in prompt) + "\n"
    if isinstance(prompt, dict):
        parts: list[str] = []
        for key, value in prompt.items():
            header = "#" * depth + " " + str(key)
            parts.append(header + "\n" + compile_prompt_to_md(value, depth + 1))
        return "\n".join(parts) + "\n"
    return str(prompt) + "\n"


REVIEW_FUNC_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_review",
        "description": "Summarize whether the experiment ran successfully.",
        "parameters": {
            "type": "object",
            "properties": {
                "is_bug": {"type": "boolean", "description": "True if execution failed or returned nonsense."},
                "summary": {"type": "string", "description": "One-paragraph summary."},
            },
            "required": ["is_bug", "summary"],
        },
    },
}

METRIC_PARSE_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_metrics",
        "description": "Emit the parsed metric values from the metric-parse exec stdout.",
        "parameters": {
            "type": "object",
            "properties": {
                "metric_names": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "metric_name": {"type": "string"},
                            "lower_is_better": {"type": "boolean"},
                            "description": {"type": "string"},
                            "data": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "dataset_name": {"type": "string"},
                                        "final_value": {"type": "number"},
                                        "best_value": {"type": "number"},
                                    },
                                    "required": ["dataset_name", "final_value", "best_value"],
                                },
                            },
                        },
                        "required": ["metric_name", "lower_is_better", "description", "data"],
                    },
                }
            },
            "required": ["metric_names"],
        },
    },
}

VLM_FEEDBACK_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_vlm_feedback",
        "description": "Review the plots and judge whether they are valid and informative.",
        "parameters": {
            "type": "object",
            "properties": {
                "plot_analyses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"analysis": {"type": "string"}},
                        "required": ["analysis"],
                    },
                },
                "valid_plots_received": {"type": "boolean"},
                "vlm_feedback_summary": {"type": "string"},
            },
            "required": ["plot_analyses", "valid_plots_received", "vlm_feedback_summary"],
        },
    },
}

PLOT_SELECTION_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "select_top_plots",
        "description": "Select up to 10 most relevant plots for VLM review.",
        "parameters": {
            "type": "object",
            "properties": {
                "selected_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "maxItems": 10,
                }
            },
            "required": ["selected_indices"],
        },
    },
}

PROMPT_IMPL_GUIDELINE: str = """## Implementation guideline

Save intermediate results to ``working/`` under your current working
directory (the runner has already chdir'd you there). Specifically:

- ``np.save(os.path.join('working', 'experiment_data.npy'), <data>)`` —
  every metric and per-dataset value you want graded.
- ``working/*.png`` — every plot you want reviewed.

Use ``device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')``;
fall back to CPU silently. Don't print large blobs to stdout — the
workflow caps captured output at ~5KB.

Mirrored from .scientist/ai_scientist/treesearch/parallel_agent.py:
296-394 (research 02 §Agent turn shape)."""

PROMPT_RESP_FMT: str = """## Response format

Respond in natural language, then a SINGLE Python codeblock (triple
backticks, ``python`` language tag). The runner extracts the codeblock
and writes it as ``runfile.py``."""


def render_prompts(*fragments: PromptType) -> str:
    """Concatenate fragments through ``compile_prompt_to_md`` for an LLM call."""
    return "\n".join(compile_prompt_to_md(f) for f in fragments)


__all__: Iterable[str] = (
    "compile_prompt_to_md",
    "render_prompts",
    "REVIEW_FUNC_SPEC",
    "METRIC_PARSE_SPEC",
    "VLM_FEEDBACK_SPEC",
    "PLOT_SELECTION_SPEC",
    "PROMPT_IMPL_GUIDELINE",
    "PROMPT_RESP_FMT",
)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio pytest tests/test_bfts_prompts.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add overlay/workflows/_bfts_prompts.py overlay/workflows/tests/test_bfts_prompts.py
git commit -m "feat(bfts): port Sakana prompt fragments and function specs"
```

---

## Task 2.5: `_bfts_state` DAO

**Why:** Workflow-side persistence layer keyed off `bfts_runs`/`bfts_nodes`. Mirrors the table-owning pattern from `.centaur/workflows/muesli_meeting_ingest.py:60-103` (research 03 §Closest existing analogues #2).

**Files:**
- Create: `overlay/workflows/_bfts_state.py`
- Create: `overlay/workflows/tests/integration/__init__.py`
- Create: `overlay/workflows/tests/integration/test_bfts_state.py`

- [ ] **Step 1: Write the failing integration test**

`overlay/workflows/tests/integration/test_bfts_state.py`:

```python
"""Integration: _bfts_state DAO against a real asyncpg pool.

Skips when CENTAUR_TEST_DATABASE_URL is unset (matches existing overlay
convention; see overlay/Justfile recipe test-workflows-integration).
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _bfts_state import insert_node, insert_run, list_nodes_for_run, update_node_metric

pytestmark = pytest.mark.skipif(
    not os.getenv("CENTAUR_TEST_DATABASE_URL"),
    reason="set CENTAUR_TEST_DATABASE_URL to run (see db/README.md)",
)


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(os.environ["CENTAUR_TEST_DATABASE_URL"])
    yield p
    await p.close()


@pytest.mark.asyncio
async def test_insert_and_list(pool: asyncpg.Pool) -> None:
    run_id = f"test-{uuid.uuid4().hex}"
    await insert_run(
        pool,
        run_id=run_id,
        parent_run_id=None,
        idea={"name": "test"},
        config={"num_drafts": 1, "num_workers": 1, "max_debug_depth": 3, "debug_prob": 0.0},
        seed=0,
    )

    node_id = uuid.uuid4().hex
    await insert_node(
        pool,
        node_id=node_id,
        run_id=run_id,
        parent_node_id=None,
        step=0,
        stage_name="draft",
        plan="initial plan",
        code="print(1)",
    )

    metric = {
        "metric_names": [{
            "metric_name": "loss",
            "lower_is_better": True,
            "description": "",
            "data": [{"dataset_name": "d", "final_value": 0.5, "best_value": 0.5}],
        }]
    }
    await update_node_metric(
        pool,
        node_id=node_id,
        term_out=["hi\n"],
        exec_time_seconds=0.1,
        exc_type=None,
        exc_info=None,
        exc_stack=None,
        metric=metric,
        is_buggy=False,
        analysis="ran clean",
    )

    nodes = await list_nodes_for_run(pool, run_id=run_id)
    assert len(nodes) == 1
    n = nodes[0]
    assert n["node_id"] == node_id
    assert n["is_buggy"] is False
    assert json.loads(n["metric_json"])["metric_names"][0]["metric_name"] == "loss"
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio --with asyncpg pytest tests/integration/test_bfts_state.py -v
```

Expected: FAIL — module not found (or SKIPPED if `CENTAUR_TEST_DATABASE_URL` unset; export it first using the recipe in `db/README.md`).

- [ ] **Step 3: Implement `_bfts_state.py`**

`overlay/workflows/_bfts_state.py`:

```python
"""DAO for bfts_runs / bfts_nodes / bfts_artifacts.

All SQL is fixed; no string interpolation. Parameters are passed as
positional asyncpg arguments. Underscore-prefixed so the workflow loader
skips it.
"""
from __future__ import annotations

import json
from typing import Any

import asyncpg


async def insert_run(
    pool: asyncpg.Pool,
    *,
    run_id: str,
    parent_run_id: str | None,
    idea: dict[str, Any],
    config: dict[str, Any],
    seed: int,
    stage_name: str = "stage_1",
) -> None:
    await pool.execute(
        """
        INSERT INTO bfts_runs (run_id, parent_run_id, idea_json, config_json,
                               stage_name, seed)
        VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6)
        ON CONFLICT (run_id) DO NOTHING
        """,
        run_id, parent_run_id, json.dumps(idea), json.dumps(config), stage_name, seed,
    )


async def insert_node(
    pool: asyncpg.Pool,
    *,
    node_id: str,
    run_id: str,
    parent_node_id: str | None,
    step: int,
    stage_name: str,
    plan: str,
    code: str,
    debug_depth: int = 0,
) -> None:
    await pool.execute(
        """
        INSERT INTO bfts_nodes
            (node_id, run_id, parent_node_id, step, stage_name, plan, code, debug_depth)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        node_id, run_id, parent_node_id, step, stage_name, plan, code, debug_depth,
    )


async def update_node_metric(
    pool: asyncpg.Pool,
    *,
    node_id: str,
    term_out: list[str],
    exec_time_seconds: float,
    exc_type: str | None,
    exc_info: dict[str, Any] | None,
    exc_stack: list[Any] | None,
    metric: dict[str, Any] | None,
    is_buggy: bool,
    analysis: str | None,
) -> None:
    await pool.execute(
        """
        UPDATE bfts_nodes SET
            term_out_json = $2::jsonb,
            exec_time_seconds = $3,
            exc_type = $4,
            exc_info_json = $5::jsonb,
            exc_stack_json = $6::jsonb,
            metric_json = $7::jsonb,
            is_buggy = $8,
            analysis = $9,
            updated_at = NOW()
        WHERE node_id = $1
        """,
        node_id,
        json.dumps(term_out),
        exec_time_seconds,
        exc_type,
        json.dumps(exc_info) if exc_info is not None else None,
        json.dumps(exc_stack) if exc_stack is not None else None,
        json.dumps(metric) if metric is not None else None,
        is_buggy,
        analysis,
    )


async def mark_buggy_plots(
    pool: asyncpg.Pool, *, node_id: str, is_buggy_plots: bool,
    plot_analyses: list[dict[str, Any]] | None,
    vlm_feedback_summary: str | None,
) -> None:
    await pool.execute(
        """
        UPDATE bfts_nodes SET
            is_buggy_plots = $2,
            plot_analyses_json = $3::jsonb,
            vlm_feedback_summary = $4,
            updated_at = NOW()
        WHERE node_id = $1
        """,
        node_id,
        is_buggy_plots,
        json.dumps(plot_analyses) if plot_analyses is not None else None,
        vlm_feedback_summary,
    )


async def list_nodes_for_run(
    pool: asyncpg.Pool, *, run_id: str
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT node_id, run_id, parent_node_id, step, stage_name, plan, code,
               term_out_json, exec_time_seconds, exc_type, exc_info_json,
               metric_json, is_buggy, is_buggy_plots, debug_depth, analysis,
               vlm_feedback_summary
        FROM bfts_nodes
        WHERE run_id = $1
        ORDER BY step ASC
        """,
        run_id,
    )
    return [dict(r) for r in rows]


async def set_best_node(
    pool: asyncpg.Pool, *, run_id: str, best_node_id: str
) -> None:
    await pool.execute(
        "UPDATE bfts_runs SET best_node_id = $2, status = 'completed', updated_at = NOW() WHERE run_id = $1",
        run_id, best_node_id,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
# Set CENTAUR_TEST_DATABASE_URL first (see db/README.md), then:
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio --with asyncpg pytest tests/integration/test_bfts_state.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add overlay/workflows/_bfts_state.py \
        overlay/workflows/tests/integration/__init__.py \
        overlay/workflows/tests/integration/test_bfts_state.py
git commit -m "feat(bfts): add _bfts_state DAO + integration test"
```

---

## Task 2.6: LLM call helper (one place that calls Anthropic/OpenAI)

**Why:** The expansion pipeline makes 5–7 LLM calls per node. Each call must be wrapped in `ctx.step(...)` so a worker restart resumes mid-pipeline (research 02 §Concurrency model mapping, research 03 §Workflow programming model `ctx.step`). Centralize the HTTP call so every step looks identical.

**Files:**
- Create: `overlay/workflows/_bfts_llm.py`
- Create: `overlay/workflows/tests/test_bfts_llm.py`

- [ ] **Step 1: Write the failing test**

`overlay/workflows/tests/test_bfts_llm.py`:

```python
"""Test: _bfts_llm wraps OpenAI chat-completions with function-call extraction."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_llm import LLMCall, call_with_function


@pytest.mark.asyncio
async def test_function_call_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_post(self, url, json=None, headers=None, **_):
        captured["url"] = url
        captured["body"] = json
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {
                        "tool_calls": [{
                            "id": "x",
                            "type": "function",
                            "function": {
                                "name": "submit_review",
                                "arguments": json.dumps({"is_bug": False, "summary": "ok"}),
                            },
                        }]
                    }
                }]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    out = await call_with_function(
        LLMCall(
            model="gpt-4o-2024-11-20",
            temperature=0.5,
            api_key="sk-test",
            prompt="judge",
        ),
        function_spec={
            "type": "function",
            "function": {
                "name": "submit_review",
                "description": "judge",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "is_bug": {"type": "boolean"},
                        "summary": {"type": "string"},
                    },
                    "required": ["is_bug", "summary"],
                },
            },
        },
    )
    assert out == {"is_bug": False, "summary": "ok"}
    assert captured["url"].endswith("/v1/chat/completions")
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio --with httpx pytest tests/test_bfts_llm.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `_bfts_llm.py`**

`overlay/workflows/_bfts_llm.py`:

```python
"""Single OpenAI/Anthropic call helper.

Why this exists: every LLM call in the expansion pipeline is its own
ctx.step checkpoint. Routing all of them through one function keeps the
HTTP shape uniform and makes ctx.step's idempotency guarantees obvious
(research 02 §Agent turn shape lists all 5–7 calls; research 03
§Durability guarantees).

The provider is implied by the model string:
  - ``gpt-*``      → OpenAI (the only path Phase 2 exercises; Sakana's
    defaults for both agent.code and agent.feedback are gpt-4o, research
    02 §(c) Model / provider params).
  - ``anthropic.*`` / ``claude-*`` → Anthropic. Deferred to Phase 4g (the
    multi-provider switch is one extra branch in :func:`call_for_text`).

iron-proxy handles outbound: when this code runs inside the API pod the
``OPENAI_API_KEY`` placeholder is substituted by iron-proxy at the
header layer (research 03 §Secrets / iron-proxy).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class LLMCall:
    model: str
    temperature: float
    api_key: str
    prompt: str
    max_tokens: int = 8192


async def call_with_function(
    call: LLMCall, *, function_spec: dict[str, Any]
) -> dict[str, Any]:
    """Issue one chat-completions call forced to invoke ``function_spec``.

    Returns the *arguments* JSON the model passed to the function.
    Raises RuntimeError on any non-2xx or missing tool_calls.
    """
    body = {
        "model": call.model,
        "temperature": call.temperature,
        "max_tokens": call.max_tokens,
        "messages": [{"role": "user", "content": call.prompt}],
        "tools": [function_spec],
        "tool_choice": {
            "type": "function",
            "function": {"name": function_spec["function"]["name"]},
        },
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {call.api_key}"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"LLM call failed: {resp.status_code} {resp.text[:500]}")
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("LLM returned no choices")
    tool_calls = choices[0]["message"].get("tool_calls") or []
    if not tool_calls:
        raise RuntimeError("LLM did not invoke the tool")
    args_str = tool_calls[0]["function"]["arguments"]
    return json.loads(args_str)


async def call_for_text(call: LLMCall) -> str:
    """Issue one chat-completions call expecting a plain-text reply.

    Used by the draft/debug/improve prompts that ask for natural language
    followed by a single python codeblock — the caller extracts the
    codeblock with ``_extract_code`` below.
    """
    body = {
        "model": call.model,
        "temperature": call.temperature,
        "max_tokens": call.max_tokens,
        "messages": [{"role": "user", "content": call.prompt}],
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {call.api_key}"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"LLM call failed: {resp.status_code} {resp.text[:500]}")
    data = resp.json()
    return data["choices"][0]["message"]["content"] or ""


def extract_code(text: str) -> tuple[str, str]:
    """Extract (plan, python_code) from natural-language + codeblock reply.

    Mirrors Sakana's response.extract_text_up_to_code +
    response.extract_code (utils/response.py:55-83).
    """
    fence = "```python"
    idx = text.find(fence)
    if idx == -1:
        # Fall back to plain ``` fence.
        idx = text.find("```")
        if idx == -1:
            return text.strip(), ""
        fence = "```"
    plan = text[:idx].rstrip()
    rest = text[idx + len(fence):]
    end = rest.find("```")
    if end == -1:
        return plan, rest.strip()
    return plan, rest[:end].strip()
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio --with httpx pytest tests/test_bfts_llm.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add overlay/workflows/_bfts_llm.py overlay/workflows/tests/test_bfts_llm.py
git commit -m "feat(bfts): add _bfts_llm helper for chat-completions calls"
```

---

## Task 2.7: Expansion driver `_bfts_expand.expand_node`

**Why:** This is the per-node pipeline that runs the 5–7 LLM + 3 exec calls (research 02 §Agent turn shape). Each call is a separate `ctx.step` so the workflow resumes mid-expansion. The function returns the new node's row data (to write back via `_bfts_state.update_node_metric`).

**Files:**
- Create: `overlay/workflows/_bfts_expand.py`
- Create: `overlay/workflows/tests/test_bfts_expand.py`

- [ ] **Step 1: Write the failing test (sub-step sequence shape)**

`overlay/workflows/tests/test_bfts_expand.py`:

```python
"""Test: _bfts_expand.expand_node issues the right ctx.step sequence."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_expand import ExpandContext, expand_node


class _RecordingCtx:
    """Stub WorkflowContext that records each ctx.step name + returns
    canned values so we can assert the call order without I/O."""

    def __init__(self, canned: dict[str, object]) -> None:
        self._canned = canned
        self.calls: list[str] = []

    async def step(self, name, fn):
        self.calls.append(name)
        if name in self._canned:
            return self._canned[name]
        return await fn() if callable(fn) else None

    def log(self, *args, **kwargs):
        pass


@pytest.mark.asyncio
async def test_draft_expansion_calls_in_order() -> None:
    canned = {
        "draft_propose": {"plan": "p", "code": "print(1)"},
        "draft_exec": {"term_out": ["hi\n"], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "bug_judge": {"is_bug": False, "summary": "ok"},
        "metric_parse_propose": "print('m')",
        "metric_parse_exec": {"term_out": ["m\n"], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "metric_extract": {"metric_names": []},
        "plot_propose": "import matplotlib",
        "plot_exec": {"term_out": [], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
    }
    ctx = _RecordingCtx(canned)
    expand_ctx = ExpandContext(
        sandbox_id="sbx-1", parent_node=None, idea={}, openai_api_key="sk-test", node_id="n-1"
    )
    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
    # Sakana's pipeline order, every entry one ctx.step:
    assert ctx.calls == [
        "draft_propose",
        "draft_exec",
        "bug_judge",
        "metric_parse_propose",
        "metric_parse_exec",
        "metric_extract",
        "plot_propose",
        "plot_exec",
    ]
    assert result["code"] == "print(1)"
    assert result["is_buggy"] is False
    assert result["term_out"] == ["hi\n"]


@pytest.mark.asyncio
async def test_buggy_exec_skips_plotting() -> None:
    canned = {
        "draft_propose": {"plan": "p", "code": "raise RuntimeError()"},
        "draft_exec": {"term_out": ["err\n"], "exec_time": 0.1, "exc_type": "SubprocessError", "exc_info": {"exit_code": 1}, "exc_stack": None},
        "bug_judge": {"is_bug": True, "summary": "raised"},
    }
    ctx = _RecordingCtx(canned)
    expand_ctx = ExpandContext(sandbox_id="sbx-1", parent_node=None, idea={}, openai_api_key="sk-test", node_id="n-2")
    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
    # On buggy exec, plotting + metric_extract are skipped.
    assert ctx.calls == ["draft_propose", "draft_exec", "bug_judge"]
    assert result["is_buggy"] is True
    assert result["metric"] is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio pytest tests/test_bfts_expand.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `_bfts_expand.py`**

`overlay/workflows/_bfts_expand.py`:

```python
"""Per-node expansion pipeline.

One call to expand_node() runs the 5–7 LLM-call + 3 exec-call pipeline
from research 02 §Agent turn shape:

  draft_propose / debug_propose / improve_propose  (LLM call #1)
  *_exec                                            (sandbox exec #1)
  bug_judge                                         (LLM call #2)
  metric_parse_propose                              (LLM call #3)
  metric_parse_exec                                 (sandbox exec #2)
  metric_extract                                    (LLM call #4)
  plot_propose                                      (LLM call #5, skipped if buggy)
  plot_exec                                         (sandbox exec #3, skipped if buggy)

Each call is its own ctx.step so workflow restart resumes mid-pipeline.

VLM analysis (LLM call #6) lives in Phase 3 (_bfts_export wires it).

Underscore-prefixed: workflow loader skips it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from _bfts_llm import LLMCall, call_for_text, call_with_function, extract_code
from _bfts_prompts import METRIC_PARSE_SPEC, REVIEW_FUNC_SPEC, render_prompts


_DRAFT_MODEL = "gpt-4o-2024-11-20"        # Sakana default for agent.code (research 02 §c)
_FEEDBACK_MODEL = "gpt-4o-2024-11-20"     # Sakana default for agent.feedback
_DRAFT_TEMP = 1.0
_FEEDBACK_TEMP = 0.5


@dataclass
class ExpandContext:
    sandbox_id: str
    parent_node: Optional[dict[str, Any]]   # row dict from bfts_nodes; None = new draft
    idea: dict[str, Any]
    openai_api_key: str
    node_id: str


def _branch(parent: Optional[dict[str, Any]]) -> str:
    if parent is None:
        return "draft"
    return "debug" if parent.get("is_buggy") else "improve"


def _propose_prompt(expand_ctx: ExpandContext) -> str:
    branch = _branch(expand_ctx.parent_node)
    if branch == "draft":
        return render_prompts(
            {"Idea": expand_ctx.idea},
            {"Task": "Write Python code that runs the experiment described above."},
        )
    if branch == "debug":
        parent = expand_ctx.parent_node or {}
        return render_prompts(
            {"Idea": expand_ctx.idea},
            {"Failed code": f"```python\n{parent.get('code','')}\n```"},
            {"stderr": (parent.get("term_out_json") or "")[-2000:] if isinstance(parent.get("term_out_json"), str) else ""},
            {"Task": "Fix the bug in the failed code above and re-run."},
        )
    parent = expand_ctx.parent_node or {}
    return render_prompts(
        {"Idea": expand_ctx.idea},
        {"Previous good code": f"```python\n{parent.get('code','')}\n```"},
        {"Task": "Improve on the previous code above."},
    )


def _metric_parse_prompt(code: str, term_out: list[str]) -> str:
    return render_prompts(
        {"Original experiment code": f"```python\n{code}\n```"},
        {"Experiment stdout": "\n".join(term_out)[-3000:]},
        {"Task": "Write a Python script that reads working/experiment_data.npy and PRINTS the metric values."},
    )


def _plot_prompt(code: str, metric: dict[str, Any]) -> str:
    return render_prompts(
        {"Experiment code": f"```python\n{code}\n```"},
        {"Metrics": metric},
        {"Task": "Write matplotlib code that loads working/experiment_data.npy and saves *.png plots to working/."},
    )


async def _propose_code(expand_ctx: ExpandContext) -> dict[str, Any]:
    text = await call_for_text(
        LLMCall(
            model=_DRAFT_MODEL,
            temperature=_DRAFT_TEMP,
            api_key=expand_ctx.openai_api_key,
            prompt=_propose_prompt(expand_ctx),
        )
    )
    plan, code = extract_code(text)
    return {"plan": plan, "code": code}


async def _bug_judge(text_blobs: list[str], openai_api_key: str) -> dict[str, Any]:
    return await call_with_function(
        LLMCall(
            model=_FEEDBACK_MODEL,
            temperature=_FEEDBACK_TEMP,
            api_key=openai_api_key,
            prompt="Judge whether this experiment succeeded:\n\n" + "\n\n".join(text_blobs),
        ),
        function_spec=REVIEW_FUNC_SPEC,
    )


async def _metric_extract(parse_term_out: list[str], openai_api_key: str) -> dict[str, Any]:
    return await call_with_function(
        LLMCall(
            model=_FEEDBACK_MODEL,
            temperature=_FEEDBACK_TEMP,
            api_key=openai_api_key,
            prompt="Extract metrics from this stdout:\n\n" + "\n".join(parse_term_out)[-3000:],
        ),
        function_spec=METRIC_PARSE_SPEC,
    )


async def expand_node(*, ctx: Any, expand_ctx: ExpandContext) -> dict[str, Any]:
    """Run one full expansion. Returns a dict suitable for update_node_metric."""

    branch = _branch(expand_ctx.parent_node)

    proposed = await ctx.step(
        f"{branch}_propose", lambda: _propose_code(expand_ctx)
    )

    exec_res = await ctx.step(
        f"{branch}_exec",
        lambda: ctx.tools.bfts_executor.exec_python(
            sandbox_id=expand_ctx.sandbox_id,
            code=proposed["code"],
            timeout_s=3600,
        ),
    )

    judge = await ctx.step(
        "bug_judge",
        lambda: _bug_judge(
            [proposed["code"], "\n".join(exec_res["term_out"])],
            expand_ctx.openai_api_key,
        ),
    )
    is_buggy = bool(judge["is_bug"]) or exec_res["exc_type"] is not None

    if is_buggy:
        return {
            "plan": proposed["plan"],
            "code": proposed["code"],
            "term_out": exec_res["term_out"],
            "exec_time_seconds": exec_res["exec_time"],
            "exc_type": exec_res["exc_type"],
            "exc_info": exec_res["exc_info"],
            "exc_stack": exec_res["exc_stack"],
            "metric": None,
            "is_buggy": True,
            "analysis": judge["summary"],
            "stage_name": branch,
        }

    parse_code = await ctx.step(
        "metric_parse_propose",
        lambda: _metric_parse_inline(expand_ctx, proposed, exec_res),
    )

    parse_exec = await ctx.step(
        "metric_parse_exec",
        lambda: ctx.tools.bfts_executor.exec_python(
            sandbox_id=expand_ctx.sandbox_id, code=parse_code, timeout_s=300,
        ),
    )

    metric = await ctx.step(
        "metric_extract",
        lambda: _metric_extract(parse_exec["term_out"], expand_ctx.openai_api_key),
    )

    plot_code = await ctx.step(
        "plot_propose",
        lambda: _plot_propose_inline(expand_ctx, proposed, metric),
    )

    plot_exec = await ctx.step(
        "plot_exec",
        lambda: ctx.tools.bfts_executor.exec_python(
            sandbox_id=expand_ctx.sandbox_id, code=plot_code, timeout_s=300,
        ),
    )

    return {
        "plan": proposed["plan"],
        "code": proposed["code"],
        "term_out": exec_res["term_out"],
        "exec_time_seconds": exec_res["exec_time"],
        "exc_type": exec_res["exc_type"],
        "exc_info": exec_res["exc_info"],
        "exc_stack": exec_res["exc_stack"],
        "metric": metric,
        "is_buggy": False,
        "analysis": judge["summary"],
        "stage_name": branch,
        "parse_metrics_code": parse_code,
        "parse_term_out": parse_exec["term_out"],
        "plot_code": plot_code,
        "plot_term_out": plot_exec["term_out"],
    }


async def _metric_parse_inline(
    expand_ctx: ExpandContext, proposed: dict[str, Any], exec_res: dict[str, Any]
) -> str:
    text = await call_for_text(
        LLMCall(
            model=_DRAFT_MODEL,
            temperature=_DRAFT_TEMP,
            api_key=expand_ctx.openai_api_key,
            prompt=_metric_parse_prompt(proposed["code"], exec_res["term_out"]),
        )
    )
    _plan, code = extract_code(text)
    return code


async def _plot_propose_inline(
    expand_ctx: ExpandContext, proposed: dict[str, Any], metric: dict[str, Any]
) -> str:
    text = await call_for_text(
        LLMCall(
            model=_DRAFT_MODEL,
            temperature=_DRAFT_TEMP,
            api_key=expand_ctx.openai_api_key,
            prompt=_plot_prompt(proposed["code"], metric),
        )
    )
    _plan, code = extract_code(text)
    return code
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio --with httpx pytest tests/test_bfts_expand.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add overlay/workflows/_bfts_expand.py overlay/workflows/tests/test_bfts_expand.py
git commit -m "feat(bfts): add per-node expansion pipeline (ctx.step per LLM/exec)"
```

---

## Task 2.8: Tree controller workflow `bfts_tree.py`

**Why:** This is the durable workflow body. Loops: `select_next` → fan out `expand_node` per selection → wait_all → write nodes back → check terminate.

**Files:**
- Create: `overlay/workflows/bfts_tree.py`
- Create: `overlay/workflows/tests/test_bfts_tree_handler.py`

- [ ] **Step 1: Write the failing test (handler signature + terminate condition)**

`overlay/workflows/tests/test_bfts_tree_handler.py`:

```python
"""Test: bfts_tree handler input parsing + terminate condition."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bfts_tree import Input, WORKFLOW_NAME, _should_terminate


def test_workflow_name() -> None:
    assert WORKFLOW_NAME == "bfts_tree"


def test_input_defaults() -> None:
    inp = Input(run_id="r1", parent_run_id=None, idea={"name": "x"})
    assert inp.num_drafts == 3
    assert inp.num_workers == 4
    assert inp.max_debug_depth == 3
    assert inp.debug_prob == 0.5
    assert inp.max_iters == 20
    assert inp.seed == 0


def test_terminate_on_good_node() -> None:
    nodes = [
        {"is_buggy": False, "is_buggy_plots": False},
        {"is_buggy": True, "is_buggy_plots": None},
    ]
    assert _should_terminate(nodes, iters_used=5, max_iters=20) is True


def test_terminate_on_max_iters_with_no_good_node() -> None:
    nodes = [{"is_buggy": True, "is_buggy_plots": None}]
    assert _should_terminate(nodes, iters_used=20, max_iters=20) is True


def test_no_terminate_yet() -> None:
    nodes = [{"is_buggy": True, "is_buggy_plots": None}]
    assert _should_terminate(nodes, iters_used=5, max_iters=20) is False
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio pytest tests/test_bfts_tree_handler.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `bfts_tree.py`**

`overlay/workflows/bfts_tree.py`:

```python
"""Workflow: BFTS tree controller (Stage 1 only).

Loops:
  select_next → for each selection, ctx.step("expand_node", ...) → wait_all
  → write nodes → check terminate.

Terminate when ≥1 good_node exists (Sakana stage-1 completion rule,
agent_manager.py:434-442) OR iters_used >= max_iters.

See docs/superpowers/plans/2026-05-25-bfts-on-centaur.md (Phase 2).
"""
from __future__ import annotations

import os
import random
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from _bfts_expand import ExpandContext, expand_node
from _bfts_metric import score
from _bfts_select import NodeRef, SearchConfig, select_next
from _bfts_state import (
    insert_node,
    insert_run,
    list_nodes_for_run,
    set_best_node,
    update_node_metric,
)

WORKFLOW_NAME = "bfts_tree"


@dataclass
class Input:
    run_id: str                       # this tree's run_id (matches workflow's own run_id)
    parent_run_id: str | None         # bfts_root run that started us
    idea: dict[str, Any] = field(default_factory=dict)
    num_drafts: int = 3
    num_workers: int = 4
    max_debug_depth: int = 3
    debug_prob: float = 0.5
    max_iters: int = 20
    seed: int = 0
    sandbox_id: str = ""              # pre-provisioned by bfts_root
    openai_api_key_secret: str = "OPENAI_API_KEY"   # iron-proxy substitutes


def _should_terminate(nodes: list[dict[str, Any]], iters_used: int, max_iters: int) -> bool:
    has_good = any(n.get("is_buggy") is False and n.get("is_buggy_plots") is not True for n in nodes)
    return has_good or iters_used >= max_iters


def _to_noderef(row: dict[str, Any]) -> NodeRef:
    metric = row.get("metric_json") or {"_worst": True}
    return NodeRef(
        node_id=row["node_id"],
        parent_id=row.get("parent_node_id"),
        root_id=_root_id(row),
        is_buggy=row.get("is_buggy"),
        is_buggy_plots=row.get("is_buggy_plots"),
        debug_depth=int(row.get("debug_depth") or 0),
        metric_score=score(metric if isinstance(metric, dict) else {"_worst": True}),
        stage_name=row.get("stage_name", "draft"),
        is_leaf=True,
    )


def _root_id(row: dict[str, Any]) -> str:
    return row["node_id"] if row.get("parent_node_id") is None else (row.get("parent_node_id") or "ROOT")


async def handler(inp: Input, ctx: "WorkflowContext") -> dict[str, Any]:
    rng = random.Random(inp.seed)
    pool = ctx._pool

    await ctx.step(
        "insert_run",
        lambda: insert_run(
            pool,
            run_id=inp.run_id,
            parent_run_id=inp.parent_run_id,
            idea=inp.idea,
            config={
                "num_drafts": inp.num_drafts,
                "num_workers": inp.num_workers,
                "max_debug_depth": inp.max_debug_depth,
                "debug_prob": inp.debug_prob,
                "max_iters": inp.max_iters,
                "seed": inp.seed,
            },
            seed=inp.seed,
        ),
    )

    cfg = SearchConfig(
        num_drafts=inp.num_drafts,
        num_workers=inp.num_workers,
        max_debug_depth=inp.max_debug_depth,
        debug_prob=inp.debug_prob,
    )

    openai_api_key = os.getenv(inp.openai_api_key_secret) or ""

    iters_used = 0
    while iters_used < inp.max_iters:
        nodes = await ctx.step("list_nodes", lambda: list_nodes_for_run(pool, run_id=inp.run_id))
        if _should_terminate(nodes, iters_used, inp.max_iters):
            break

        noderefs = [_to_noderef(n) for n in nodes]
        selections = select_next(nodes=noderefs, cfg=cfg, rng=rng)

        # Insert one bfts_nodes row per selection up-front (so node_id is
        # stable across expansion sub-steps even after restart).
        prepared: list[tuple[str, NodeRef | None]] = []
        for sel in selections:
            node_id = uuid.uuid4().hex
            parent_id = sel.node_id if sel is not None else None
            parent_row = next((n for n in nodes if n["node_id"] == parent_id), None) if parent_id else None
            stage = "draft" if sel is None else ("debug" if parent_row and parent_row.get("is_buggy") else "improve")
            debug_depth = 0
            if sel is not None and parent_row and parent_row.get("is_buggy"):
                debug_depth = int(parent_row.get("debug_depth") or 0) + 1
            await ctx.step(
                "insert_node",
                lambda nid=node_id, pid=parent_id, st=stage, dd=debug_depth: insert_node(
                    pool,
                    node_id=nid,
                    run_id=inp.run_id,
                    parent_node_id=pid,
                    step=iters_used,
                    stage_name=st,
                    plan="",
                    code="",
                    debug_depth=dd,
                ),
            )
            prepared.append((node_id, sel))

        # Expand each selected node sequentially within this controller step.
        # (Intra-step fan-out via child workflows is a Phase 3+ optimization;
        # for MVP a sequential loop keeps the workflow self-contained and
        # is bounded by num_workers anyway.)
        for node_id, sel in prepared:
            parent_row = (
                next((n for n in nodes if n["node_id"] == sel.node_id), None)
                if sel is not None else None
            )
            expand_ctx = ExpandContext(
                sandbox_id=inp.sandbox_id,
                parent_node=parent_row,
                idea=inp.idea,
                openai_api_key=openai_api_key,
                node_id=node_id,
            )
            result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
            await ctx.step(
                "update_node",
                lambda nid=node_id, r=result: update_node_metric(
                    pool,
                    node_id=nid,
                    term_out=r["term_out"],
                    exec_time_seconds=r["exec_time_seconds"],
                    exc_type=r["exc_type"],
                    exc_info=r["exc_info"],
                    exc_stack=r["exc_stack"],
                    metric=r["metric"],
                    is_buggy=r["is_buggy"],
                    analysis=r["analysis"],
                ),
            )
        iters_used += 1

    final_nodes = await ctx.step(
        "list_nodes_final", lambda: list_nodes_for_run(pool, run_id=inp.run_id)
    )
    good = [n for n in final_nodes if n.get("is_buggy") is False and n.get("is_buggy_plots") is not True]
    best = min(good, key=lambda n: score(n.get("metric_json") or {"_worst": True})) if good else None
    if best is not None:
        await ctx.step(
            "set_best", lambda: set_best_node(pool, run_id=inp.run_id, best_node_id=best["node_id"])
        )

    return {
        "run_id": inp.run_id,
        "iters_used": iters_used,
        "node_count": len(final_nodes),
        "best_node_id": best["node_id"] if best else None,
    }
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio --with httpx --with asyncpg pytest tests/test_bfts_tree_handler.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add overlay/workflows/bfts_tree.py overlay/workflows/tests/test_bfts_tree_handler.py
git commit -m "feat(bfts): add bfts_tree controller workflow (Stage 1)"
```

---

## Task 2.9: Root workflow `bfts_root.py`

**Why:** Spawns `num_drafts` independent `bfts_tree` child workflows and waits for all (research 03 §Child workflow / fan-out). Pre-provisions one Sandbox per tree (one Sandbox = one tree; working state lives on the Sandbox PVC and persists across pause/resume, research 01 §Hibernation correction). Per Spec correction #11 we **do not** call `ctx.agent_turn(...)` to provision the Sandbox — that path runs the spawn → message → execute → wait-for-terminal loop in `do_agent_turn` (`.centaur/services/api/api/workflow_engine.py:1124`) and drags in `spawn_assignment`, slackbot session opening, and agent-execution event rows. Instead, the workflow generates a deterministic `sandbox_id = f"bfts-{ctx.run_id}-tree-{i}"` and calls `bfts_executor.create_sandbox` via `ctx.step` (Task 1.6 implementation).

**Files:**
- Create: `overlay/workflows/bfts_root.py`
- Create: `overlay/workflows/tests/test_bfts_root_handler.py`

- [ ] **Step 1: Write the failing test**

`overlay/workflows/tests/test_bfts_root_handler.py`:

```python
"""Test: bfts_root handler input parsing + deterministic sandbox_id format."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bfts_root import Input, WORKFLOW_NAME, _sandbox_id


def test_workflow_name() -> None:
    assert WORKFLOW_NAME == "bfts_root"


def test_input_required_idea() -> None:
    inp = Input(idea={"name": "test", "Title": "X"})
    assert inp.idea["name"] == "test"
    assert inp.num_drafts == 3
    assert inp.max_iters == 20


def test_sandbox_id_is_deterministic_and_run_scoped() -> None:
    assert _sandbox_id(run_id="run-abc", tree_idx=0) == "bfts-run-abc-tree-0"
    assert _sandbox_id(run_id="run-abc", tree_idx=2) == "bfts-run-abc-tree-2"
    # Different run -> different sandbox_id.
    assert _sandbox_id(run_id="run-def", tree_idx=0) == "bfts-run-def-tree-0"
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio pytest tests/test_bfts_root_handler.py -v
```

Expected: module not found.

- [ ] **Step 3: Implement `bfts_root.py`**

`overlay/workflows/bfts_root.py`:

```python
"""Workflow: BFTS root — fans out num_drafts independent bfts_tree children.

Each child gets a Sandbox provisioned by `bfts_executor.create_sandbox`
(Task 1.6 / 1.9 in plan Phase 1). We do NOT call `ctx.agent_turn` to
provision — Spec correction #11: do_agent_turn (.centaur/services/api/api
/workflow_engine.py:1124) is for spawn→message→execute→wait-for-terminal
agent runs and drags in spawn_assignment, slackbot session opening, and
agent-execution event rows that BFTS does not need (BFTS sandboxes have
no harness; the executor's CMD is `sleep infinity`).

See docs/superpowers/plans/2026-05-25-bfts-on-centaur.md (Phase 2).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

WORKFLOW_NAME = "bfts_root"


@dataclass
class Input:
    idea: dict[str, Any] = field(default_factory=dict)
    num_drafts: int = 3
    num_workers: int = 4
    max_debug_depth: int = 3
    debug_prob: float = 0.5
    max_iters: int = 20
    seed_base: int = 0


def _sandbox_id(*, run_id: str, tree_idx: int) -> str:
    """Deterministic per-tree sandbox id.

    Format chosen so the BFTS executor's Sandbox CRDs are easy to scope
    by run_id (label `centaur.ai/bfts-run`) and easy to clean up by
    prefix. Stable across workflow restarts because `ctx.run_id` is
    durable.
    """
    return f"bfts-{run_id}-tree-{tree_idx}"


async def handler(inp: Input, ctx: "WorkflowContext") -> dict[str, Any]:
    children: list[dict[str, Any]] = []
    for i in range(inp.num_drafts):
        sandbox_id = _sandbox_id(run_id=ctx.run_id, tree_idx=i)
        await ctx.step(
            f"create_sandbox_{i}",
            lambda sid=sandbox_id: ctx.tools.bfts_executor.create_sandbox(
                sandbox_id=sid,
                run_id=ctx.run_id,
            ),
        )
        child_run_id = f"{ctx.run_id}:tree:{i}"
        child = await ctx.start_workflow(
            f"start_tree_{i}",
            workflow_name="bfts_tree",
            run_input={
                "run_id": child_run_id,
                "parent_run_id": ctx.run_id,
                "idea": inp.idea,
                "num_drafts": 1,    # each child tree has 1 root; root-level num_drafts = num trees
                "num_workers": inp.num_workers,
                "max_debug_depth": inp.max_debug_depth,
                "debug_prob": inp.debug_prob,
                "max_iters": inp.max_iters,
                "seed": inp.seed_base + i,
                "sandbox_id": sandbox_id,
            },
            trigger_key=child_run_id,
            eager_start=True,
        )
        children.append(
            {"run_id": child["run_id"], "tree_index": i, "sandbox_id": sandbox_id}
        )

    results: list[dict[str, Any]] = []
    for child in children:
        res = await ctx.wait_for_workflow(
            f"wait_tree_{child['tree_index']}", run_id=child["run_id"]
        )
        results.append(res)

    # Tear down each per-tree Sandbox now that all children are terminal.
    # PVC follows owner refs (Spec correction #12 + agent-sandbox
    # `shutdownPolicy: "Retain"` is overridden by an explicit delete).
    for child in children:
        await ctx.step(
            f"stop_sandbox_{child['tree_index']}",
            lambda sid=child["sandbox_id"]: ctx.tools.bfts_executor.stop_sandbox(
                sandbox_id=sid
            ),
        )

    return {"trees": children, "results": results}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio pytest tests/test_bfts_root_handler.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add overlay/workflows/bfts_root.py overlay/workflows/tests/test_bfts_root_handler.py
git commit -m "feat(bfts): add bfts_root workflow (deterministic sandbox_id, no agent_turn warmup)"
```

---

## Task 2.10: End-to-end smoke recipe + toy experiment run

**Why:** Phase 2 isn't done until a real `bfts_root` run with a small `num_drafts=1, max_iters=2` configuration succeeds end-to-end against the local cluster. The toy experiment: "fit a `LinearRegression` to 200 synthetic samples; report MSE."

**Files:**
- Modify: `Justfile`
- Create: `docs/superpowers/plans/2026-05-25-bfts-on-centaur-phase2-smoke.md` (verification log)

- [ ] **Step 1: Add Justfile recipe**

Append to `Justfile`:

```just
# Phase 2 smoke: kick off a tiny BFTS run (1 draft, 2 iters) and stream
# status. See docs/superpowers/plans/2026-05-25-bfts-on-centaur.md (Phase 2).
[group('bfts')]
bfts-toy-run:
    #!/usr/bin/env bash
    set -euo pipefail
    api_deploy="deploy/${CENTAUR_RELEASE}-centaur-api"
    exec_curl() {
      kubectl exec -n $CENTAUR_NAMESPACE "$api_deploy" -- sh -c \
        'curl -sS "$@" -H "X-Api-Key: $SLACKBOT_API_KEY"' -- "$@"
    }
    run=$(exec_curl -X POST http://localhost:8000/workflows/runs \
        -H "Content-Type: application/json" \
        -d '{
              "workflow_name":"bfts_root",
              "input":{
                "idea":{
                  "Name":"toy-linreg",
                  "Title":"Linear regression baseline on 200 synthetic samples",
                  "Short Hypothesis":"A least-squares fit on a 1-feature dataset should achieve MSE below the variance of y.",
                  "Experiments":["sklearn.linear_model.LinearRegression on a single synthetic dataset of 200 samples."]
                },
                "num_drafts":1,
                "num_workers":1,
                "max_iters":2,
                "debug_prob":0.5
              }
            }')
    run_id=$(printf '%s' "$run" | jq -r '.run_id')
    echo "started bfts_root run ${run_id}"
    for _ in $(seq 1 240); do
      state=$(exec_curl "http://localhost:8000/workflows/runs/${run_id}")
      status=$(printf '%s' "$state" | jq -r '.status // empty')
      [ "$status" = "completed" ] && { printf '%s\n' "$state" | jq; exit 0; }
      [ "$status" = "failed" ] || [ "$status" = "failed_permanent" ] && { printf '%s\n' "$state" | jq >&2; exit 1; }
      sleep 5
    done
    echo "bfts_root run ${run_id} did not reach terminal in time" >&2
    exec_curl "http://localhost:8000/workflows/runs/${run_id}" | jq >&2
    exit 1
```

- [ ] **Step 2: Run the smoke**

```bash
just overlay::build && just deploy
kubectl rollout status -n centaur-system deploy/centaur-centaur-api --timeout=120s
just bfts-toy-run
```

Expected: exit 0 within ~20 minutes, with `status: completed` and `output_json.results[0].best_node_id` non-null. (Worst case, the toy idea produces a buggy draft → debug → improve cycle; with `max_iters=2` it may not produce a good node — that is acceptable for the smoke; what matters is the workflow reaches a terminal state and the tree-state tables hold consistent rows.)

If it fails, inspect the per-step checkpoints:

```bash
kubectl exec -n centaur-system deploy/centaur-centaur-api -- \
  psql "$DATABASE_URL" -c "SELECT step_name, step_kind, jsonb_pretty(state) FROM workflow_checkpoints WHERE run_id = '<run_id>' ORDER BY created_at LIMIT 50;"
```

- [ ] **Step 3: Capture verification log**

Create `docs/superpowers/plans/2026-05-25-bfts-on-centaur-phase2-smoke.md` with:

```markdown
# Phase 2 smoke verification log

- date:
- run_id:
- elapsed:
- iters_used:
- node_count:
- best_node_id (or "none — toy budget exhausted"):
- any unexpected failures:
- bfts_nodes row count for run_id:
- bfts_artifacts row count for run_id:
```

Fill in the fields after the smoke.

- [ ] **Step 4: Commit**

```bash
git add Justfile docs/superpowers/plans/2026-05-25-bfts-on-centaur-phase2-smoke.md
git commit -m "feat(bfts): add just bfts-toy-run Phase 2 end-to-end smoke"
```

---

# Phase 3 — VLM gating + best-checkpoint export

## Task 3.1: `bfts_vlm` tool scaffolding

**Files:**
- Create: `overlay/tools/bfts_vlm/__init__.py`
- Create: `overlay/tools/bfts_vlm/pyproject.toml`
- Create: `overlay/tools/bfts_vlm/tests/__init__.py`

- [ ] **Step 1: Verification command**

```bash
python - <<'PY'
import tomllib, pathlib
data = tomllib.loads(pathlib.Path("overlay/tools/bfts_vlm/pyproject.toml").read_text())
assert data["project"]["name"] == "bfts_vlm"
assert data["tool"]["centaur"]["module"] == "client.py"
secrets = data["tool"]["centaur"]["optional_secrets"]
assert any(s["name"] == "OPENAI_API_KEY" for s in secrets)
print("OK")
PY
```

- [ ] **Step 2: Run before to confirm**

Expected: `FileNotFoundError`.

- [ ] **Step 3: Create the files**

`overlay/tools/bfts_vlm/__init__.py`:

```python
"""BFTS VLM review tool. See plan Phase 3."""
```

`overlay/tools/bfts_vlm/tests/__init__.py`:

```python
```

`overlay/tools/bfts_vlm/pyproject.toml`:

```toml
[project]
name = "bfts_vlm"
description = "VLM review of BFTS-generated plots"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
package = false

[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[tool.pytest.ini_options]
asyncio_mode = "strict"

[tool.centaur]
module = "client.py"
optional_secrets = [
    {type = "http", name = "OPENAI_API_KEY", match_headers = ["authorization"], hosts = ["api.openai.com"]},
]
```

- [ ] **Step 4: Run verification**

Run the Python check from Step 1. Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add overlay/tools/bfts_vlm/__init__.py \
        overlay/tools/bfts_vlm/pyproject.toml \
        overlay/tools/bfts_vlm/tests/__init__.py
git commit -m "feat(bfts): scaffold bfts_vlm tool package"
```

---

## Task 3.2: `VLMReviewer.analyze_plots` contract

**Files:**
- Create: `overlay/tools/bfts_vlm/client.py`
- Create: `overlay/tools/bfts_vlm/tests/test_vlm_contract.py`

- [ ] **Step 1: Write the failing test**

`overlay/tools/bfts_vlm/tests/test_vlm_contract.py`:

```python
"""Test: VLMReviewer.analyze_plots returns the contract shape."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import VLMReviewer


@pytest.mark.asyncio
async def test_analyze_returns_contract_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plot1 = tmp_path / "a.png"; plot1.write_bytes(b"\x89PNG_a")
    plot2 = tmp_path / "b.png"; plot2.write_bytes(b"\x89PNG_b")

    async def fake_post(self, url, json=None, headers=None, **_):
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {
                        "tool_calls": [{
                            "id": "x",
                            "type": "function",
                            "function": {
                                "name": "submit_vlm_feedback",
                                "arguments": json.dumps({
                                    "plot_analyses": [
                                        {"analysis": "looks fine"},
                                        {"analysis": "also fine"},
                                    ],
                                    "valid_plots_received": True,
                                    "vlm_feedback_summary": "plots are clean and informative",
                                }),
                            },
                        }]
                    }
                }]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    reviewer = VLMReviewer(api_key="sk-test")
    out = await reviewer.analyze_plots(
        plot_paths=[str(plot1), str(plot2)],
        task_desc="toy linreg MSE",
    )
    assert out["is_valid"] is True
    assert out["summary"] == "plots are clean and informative"
    assert len(out["per_plot_analyses"]) == 2
    assert out["per_plot_analyses"][0]["plot_index"] == 0
    assert out["per_plot_analyses"][0]["analysis"] == "looks fine"
    assert out["per_plot_analyses"][1]["plot_index"] == 1
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd overlay/tools/bfts_vlm && uv run --python 3.11 --with pytest --with pytest-asyncio --with httpx pytest tests/test_vlm_contract.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `client.py`**

`overlay/tools/bfts_vlm/client.py`:

```python
"""VLM review of BFTS plots.

Reproduces Sakana's MinimalAgent._analyze_plots_with_vlm contract
(.scientist/ai_scientist/treesearch/parallel_agent.py:894-1033,
research 02 §VLM review). Encodes up to 10 plots as base64 image_url
content; calls a vision-capable model with vlm_feedback_spec; returns
{is_valid, per_plot_analyses, summary}.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

_MAX_PLOTS = 10
_VLM_MODEL = "gpt-4o-2024-11-20"
_VLM_TEMP = 0.5

_VLM_SPEC: dict = {
    "type": "function",
    "function": {
        "name": "submit_vlm_feedback",
        "description": "Review the plots and judge their validity.",
        "parameters": {
            "type": "object",
            "properties": {
                "plot_analyses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"analysis": {"type": "string"}},
                        "required": ["analysis"],
                    },
                },
                "valid_plots_received": {"type": "boolean"},
                "vlm_feedback_summary": {"type": "string"},
            },
            "required": ["plot_analyses", "valid_plots_received", "vlm_feedback_summary"],
        },
    },
}


class VLMReviewer:
    def __init__(self, api_key: str, model: str = _VLM_MODEL) -> None:
        self.api_key = api_key
        self.model = model

    async def analyze_plots(
        self, *, plot_paths: list[str], task_desc: str
    ) -> dict:
        """Return {is_valid, per_plot_analyses, summary}.

        Caps to the first 10 plot_paths (Sakana uses an LLM judge to pick
        the best 10 when len > 10; for MVP we just truncate — known
        gap, tracked in Phase 4 deferred refinement).
        """
        keep = plot_paths[:_MAX_PLOTS]
        content: list[dict] = [
            {"type": "text", "text": f"Task: {task_desc}\nReview the plots; judge whether they are valid and informative."}
        ]
        for path in keep:
            encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encoded}"},
            })

        body = {
            "model": self.model,
            "temperature": _VLM_TEMP,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": content}],
            "tools": [_VLM_SPEC],
            "tool_choice": {"type": "function", "function": {"name": "submit_vlm_feedback"}},
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"VLM call failed: {resp.status_code} {resp.text[:500]}")
        tool_call = resp.json()["choices"][0]["message"]["tool_calls"][0]
        args = json.loads(tool_call["function"]["arguments"])

        per_plot = [
            {"plot_index": idx, "analysis": entry.get("analysis", "")}
            for idx, entry in enumerate(args.get("plot_analyses") or [])
        ]
        return {
            "is_valid": bool(args.get("valid_plots_received")),
            "per_plot_analyses": per_plot,
            "summary": args.get("vlm_feedback_summary") or "",
        }


def _client() -> VLMReviewer:
    import os
    return VLMReviewer(api_key=os.getenv("OPENAI_API_KEY", ""))
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd overlay/tools/bfts_vlm && uv run --python 3.11 --with pytest --with pytest-asyncio --with httpx pytest tests/test_vlm_contract.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add overlay/tools/bfts_vlm/client.py overlay/tools/bfts_vlm/tests/test_vlm_contract.py
git commit -m "feat(bfts): add bfts_vlm.analyze_plots with Sakana shape"
```

---

## Task 3.3: Wire VLM into `_bfts_expand`

**Why:** After the plot exec, call the `bfts_vlm` tool with `node.plot_paths`, then write `is_buggy_plots = not is_valid` back to the node via `_bfts_state.mark_buggy_plots` (research 02 §VLM review).

**Files:**
- Modify: `overlay/workflows/_bfts_expand.py`
- Create: `overlay/workflows/tests/test_bfts_expand_vlm.py`

- [ ] **Step 1: Write the failing test**

`overlay/workflows/tests/test_bfts_expand_vlm.py`:

```python
"""Test: expand_node calls VLM after plotting on non-buggy nodes."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_expand import ExpandContext, expand_node


class _Ctx:
    def __init__(self, canned: dict[str, object]) -> None:
        self.canned = canned
        self.calls: list[str] = []

        class _Tools:
            class _Vlm:
                async def analyze_plots(_self, **kwargs):
                    return canned["vlm"]

            class _Exec:
                async def exec_python(_self, **kwargs):
                    return canned["exec"]

            bfts_vlm = _Vlm()
            bfts_executor = _Exec()

        self.tools = _Tools()

    async def step(self, name, fn):
        self.calls.append(name)
        if name in self.canned:
            return self.canned[name]
        return await fn() if callable(fn) else None

    def log(self, *a, **k): pass


@pytest.mark.asyncio
async def test_expand_node_runs_vlm_after_plot() -> None:
    canned = {
        "draft_propose": {"plan": "p", "code": "print(1)"},
        "draft_exec": {"term_out": ["ok\n"], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "bug_judge": {"is_bug": False, "summary": "ok"},
        "metric_parse_propose": "print('m')",
        "metric_parse_exec": {"term_out": ["m\n"], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "metric_extract": {"metric_names": []},
        "plot_propose": "import matplotlib",
        "plot_exec": {"term_out": [], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "collect_artifacts": ["loss.png"],
        "vlm_analyze": {"is_valid": True, "per_plot_analyses": [], "summary": "ok"},
    }
    ctx = _Ctx(canned)
    expand_ctx = ExpandContext(sandbox_id="s", parent_node=None, idea={}, openai_api_key="k", node_id="n1")
    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
    assert "vlm_analyze" in ctx.calls
    assert result["is_buggy_plots"] is False
    assert result["plot_analyses"] == []
    assert result["vlm_feedback_summary"] == "ok"
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio --with httpx pytest tests/test_bfts_expand_vlm.py -v
```

Expected: FAIL — `KeyError: 'is_buggy_plots'` in the returned dict (or similar — current `expand_node` doesn't call VLM).

- [ ] **Step 3: Extend `_bfts_expand.expand_node`**

Insert two new `ctx.step` calls between the existing `plot_exec` and the `return`:

```python
    artifacts = await ctx.step(
        "collect_artifacts",
        lambda: ctx.tools.bfts_executor.collect_artifacts(
            sandbox_id=expand_ctx.sandbox_id,
            dest_dir=Path(f"/tmp/bfts/{expand_ctx.node_id}"),
            node_id=expand_ctx.node_id,
        ),
    )
    plot_paths = [
        str(Path(f"/tmp/bfts/{expand_ctx.node_id}/experiment_{expand_ctx.node_id}") / name)
        for name in artifacts if name.endswith(".png")
    ]

    if plot_paths:
        vlm = await ctx.step(
            "vlm_analyze",
            lambda: ctx.tools.bfts_vlm.analyze_plots(
                plot_paths=plot_paths,
                task_desc=str(expand_ctx.idea.get("Title", "")),
            ),
        )
    else:
        vlm = {"is_valid": False, "per_plot_analyses": [], "summary": "no plots produced"}
```

…and append to the returned dict:

```python
        "is_buggy_plots": not vlm["is_valid"],
        "plot_analyses": vlm["per_plot_analyses"],
        "vlm_feedback_summary": vlm["summary"],
```

Add `from pathlib import Path` at the top of `_bfts_expand.py` if not already present.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio --with httpx pytest tests/test_bfts_expand_vlm.py tests/test_bfts_expand.py -v
```

Expected: 3 passed (the original 2 + new 1). The original `test_draft_expansion_calls_in_order` checks `ctx.calls == [...exact list...]` — update it to include `"collect_artifacts"` and `"vlm_analyze"` at the end if your test framework reports a length mismatch.

- [ ] **Step 5: Commit**

```bash
git add overlay/workflows/_bfts_expand.py overlay/workflows/tests/test_bfts_expand_vlm.py overlay/workflows/tests/test_bfts_expand.py
git commit -m "feat(bfts): wire VLM gate into expand_node pipeline"
```

---

## Task 3.4: Persist VLM result via `_bfts_state.mark_buggy_plots`

**Why:** The DAO already has `mark_buggy_plots`; wire `bfts_tree.handler` to call it after `update_node_metric` so `good_nodes` queries see the gate.

**Files:**
- Modify: `overlay/workflows/bfts_tree.py`

- [ ] **Step 1: Identify the change**

In `bfts_tree.handler`, after the existing `ctx.step("update_node", ...)` call, add another step that conditionally calls `mark_buggy_plots` when `result` has VLM fields.

- [ ] **Step 2: Apply the edit**

Inside the `for node_id, sel in prepared:` loop in `bfts_tree.py`, after the `update_node` step, insert:

```python
            if "is_buggy_plots" in result:
                await ctx.step(
                    "mark_buggy_plots",
                    lambda nid=node_id, r=result: mark_buggy_plots(
                        pool,
                        node_id=nid,
                        is_buggy_plots=bool(r["is_buggy_plots"]),
                        plot_analyses=r.get("plot_analyses"),
                        vlm_feedback_summary=r.get("vlm_feedback_summary"),
                    ),
                )
```

Add `mark_buggy_plots` to the existing `from _bfts_state import ...` block at the top of `bfts_tree.py`.

- [ ] **Step 3: Verify no test regression**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio --with httpx --with asyncpg pytest tests/ -v
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add overlay/workflows/bfts_tree.py
git commit -m "feat(bfts): persist VLM gate via mark_buggy_plots after expand"
```

---

## Task 3.5: Best-node export (`_bfts_export`)

**Why:** After tree terminates, write the best node's code + identifier as a `bfts_artifacts` row so downstream tooling (Phase 4 writeup, Phase 4 reflection) has a stable lookup. Best-node selection is **deterministic** `argmin(score)` — explicitly *not* the Sakana LLM-judge (Spec correction #6).

**Files:**
- Create: `overlay/workflows/_bfts_export.py`
- Create: `overlay/workflows/tests/test_bfts_export.py`

- [ ] **Step 1: Write the failing test**

`overlay/workflows/tests/test_bfts_export.py`:

```python
"""Test: _bfts_export.select_best picks deterministic argmin over good nodes."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_export import select_best


def _node(node_id: str, is_buggy: bool, is_buggy_plots: bool | None, final_value: float) -> dict:
    return {
        "node_id": node_id,
        "is_buggy": is_buggy,
        "is_buggy_plots": is_buggy_plots,
        "metric_json": {
            "metric_names": [{
                "metric_name": "loss",
                "lower_is_better": True,
                "description": "",
                "data": [{"dataset_name": "d", "final_value": final_value, "best_value": final_value}],
            }]
        },
        "code": f"# code for {node_id}",
    }


def test_select_best_argmin_lower_is_better() -> None:
    nodes = [
        _node("a", is_buggy=False, is_buggy_plots=False, final_value=0.5),
        _node("b", is_buggy=False, is_buggy_plots=False, final_value=0.3),
        _node("c", is_buggy=False, is_buggy_plots=False, final_value=0.4),
    ]
    assert select_best(nodes)["node_id"] == "b"


def test_select_best_excludes_buggy_plots() -> None:
    nodes = [
        _node("a", is_buggy=False, is_buggy_plots=True, final_value=0.1),  # buggy plots: excluded
        _node("b", is_buggy=False, is_buggy_plots=False, final_value=0.3),
    ]
    assert select_best(nodes)["node_id"] == "b"


def test_select_best_returns_none_when_no_good() -> None:
    nodes = [_node("a", is_buggy=True, is_buggy_plots=None, final_value=0.1)]
    assert select_best(nodes) is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio pytest tests/test_bfts_export.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `_bfts_export.py`**

`overlay/workflows/_bfts_export.py`:

```python
"""Best-node selection + artifact export.

Deterministic argmin(score) — NO LLM judge (Spec correction #6 in plan;
research 02 §Gotcha #6 — Sakana's LLM-as-arbiter is non-deterministic
and falls back to a different selection algorithm on error).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg

from _bfts_metric import score


def select_best(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick best of good nodes by lowest score(). Returns None if none good."""
    good = [n for n in nodes if n.get("is_buggy") is False and n.get("is_buggy_plots") is not True]
    if not good:
        return None

    def _score_for(n: dict[str, Any]) -> float:
        m = n.get("metric_json")
        if isinstance(m, str):
            try:
                m = json.loads(m)
            except json.JSONDecodeError:
                m = None
        if not isinstance(m, dict):
            m = {"_worst": True}
        return score(m)

    return min(good, key=_score_for)


async def write_best_artifact(
    pool: asyncpg.Pool, *, node_id: str, code: str
) -> str:
    """Persist the best node's code to bfts_artifacts. Returns artifact_id."""
    artifact_id = uuid.uuid4().hex
    await pool.execute(
        """
        INSERT INTO bfts_artifacts (artifact_id, node_id, kind, relative_path, bytes)
        VALUES ($1, $2, 'code', 'best_solution.py', $3)
        ON CONFLICT (node_id, relative_path) DO UPDATE SET bytes = EXCLUDED.bytes
        """,
        artifact_id, node_id, code.encode("utf-8"),
    )
    return artifact_id
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio pytest tests/test_bfts_export.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Wire into `bfts_tree.handler`**

In `bfts_tree.py`, replace the `best = min(...) if good else None` block + `set_best_node` step with:

```python
    from _bfts_export import select_best, write_best_artifact   # local import to keep top tidy

    best = select_best(final_nodes)
    if best is not None:
        await ctx.step(
            "write_best_artifact",
            lambda: write_best_artifact(pool, node_id=best["node_id"], code=best["code"]),
        )
        await ctx.step(
            "set_best",
            lambda: set_best_node(pool, run_id=inp.run_id, best_node_id=best["node_id"]),
        )
```

- [ ] **Step 6: Re-run all tests**

```bash
cd overlay/workflows && uv run --python 3.11 --with pytest --with pytest-asyncio --with httpx --with asyncpg pytest tests/ -v
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add overlay/workflows/_bfts_export.py overlay/workflows/tests/test_bfts_export.py overlay/workflows/bfts_tree.py
git commit -m "feat(bfts): add deterministic best-node selector + artifact export"
```

---

## Task 3.6: End-to-end Phase 3 smoke

**Why:** With VLM in the loop, the toy-run should also see `is_buggy_plots` populated and a `best_solution.py` artifact in `bfts_artifacts`.

**Files:**
- Modify: `Justfile`

- [ ] **Step 1: Add a verification recipe**

Append:

```just
# Phase 3 smoke: run a toy BFTS + assert best_solution.py exists.
[group('bfts')]
bfts-verify-best:
    #!/usr/bin/env bash
    set -euo pipefail
    just bfts-toy-run
    api_deploy="deploy/${CENTAUR_RELEASE}-centaur-api"
    count=$(kubectl exec -n $CENTAUR_NAMESPACE $api_deploy -- psql "$DATABASE_URL" -tAc \
      "SELECT count(*) FROM bfts_artifacts WHERE relative_path = 'best_solution.py';")
    if [ "$count" -ge "1" ]; then
      echo "BFTS-VERIFY-BEST OK ($count artifacts)"
      exit 0
    fi
    echo "no best_solution.py written" >&2
    exit 1
```

- [ ] **Step 2: Run it**

```bash
just overlay::build && just deploy && just bfts-verify-best
```

Expected: `BFTS-VERIFY-BEST OK (1 artifacts)` (or more if you've run multiple times). If the toy run never produces a good node within `max_iters=2`, manually re-run with `max_iters=4` by editing the `bfts-toy-run` recipe temporarily and confirm a `best_solution.py` lands.

- [ ] **Step 3: Commit**

```bash
git add Justfile
git commit -m "feat(bfts): add bfts-verify-best Phase 3 end-to-end check"
```

---

# Phase 4+ — deferred (named only)

These are real, code-able phases that were intentionally cut from MVP. Each is a follow-on sub-plan, not a future task.

## Phase 4a: GPU compute split

**Why deferred:** No GPU compute target is wired yet (research 03 OQ #2). Until there's a chosen target (k8s Job on a GPU nodepool, RunPod/Modal external API, or an in-cluster Sandbox with `nodeSelector: nvidia.com/gpu`), the relay workflow has no destination.

**Sketch when promoted:**
- Create `overlay/workflows/bfts_gpu_callback.py` with a `WEBHOOKS=[{slug: "bfts-gpu-done", auth: HmacAuth(...), trigger_key: {type: "header", header: "X-BFTS-Job-Id"}}]` block (mirroring `.centaur/workflows/github_issue_triage.py:28-37` per research 03 §Closest existing analogues #1).
- Handler validates HMAC + parses `{job_id, metric_url, status}`, then calls `send_workflow_event(pool, event_type="bfts_gpu_done", correlation_id=job_id, payload=...)` (per research 03 §Webhooks correction).
- Modify `_bfts_expand` to detect a `gpu: true` flag on the proposed code and route to the GPU path: enqueue against the chosen target, `await ctx.wait_for_event("bfts_gpu_done", correlation_id=job_id, timeout=2h)`.

## Phase 4b: Stages 2–4 of Sakana's curriculum

**Why deferred:** Stage 1 is BFTS; Stages 2–4 are AI-Scientist-curriculum-specific (hyperparam tuning, ablations, stability checks per research 02 §Outer loop). Each is a separate `bfts_tree` child workflow type with its own prompt fragments + completion criteria.

**Sketch when promoted:** add `bfts_tree_stage2.py`, `bfts_tree_stage3.py`, `bfts_tree_stage4.py`; `bfts_root` becomes a curriculum driver that chains them. Each stage seeds its journal with the previous stage's best node.

## Phase 4c: Outer loop (`bfts_reflection_nightly`)

**Why deferred:** No corpus of `bfts_root` runs to reflect on yet.

**Sketch:** new overlay migration `<next>_add_bfts_hyperparams.sql` adds:

```sql
CREATE TABLE bfts_hyperparams (
    effective_from TIMESTAMPTZ PRIMARY KEY,
    debug_prob FLOAT NOT NULL,
    max_debug_depth INT NOT NULL,
    num_drafts INT NOT NULL,
    notes TEXT
);
```

New workflow `overlay/workflows/bfts_reflection_nightly.py` with `SCHEDULE = {"cron": "0 3 * * *"}` reads recent `bfts_runs`, computes per-run best-node score + iter cost, and inserts a new `bfts_hyperparams` row (per research 03 §Outer loop). `bfts_root` reads the latest `bfts_hyperparams` row at start as defaults; the workflow input overrides.

(This is **not** a skill, per Spec correction #5.)

## Phase 4d: S2 ideation + citation

**Why deferred:** Per research 04 §Recommendation — Phase 1 ideation and Phase 2 citation are clean follow-ons; the overlay client (`overlay/tools/semantic_scholar/`) is already most of the work.

**Sketch:**
- Phase 1: new `overlay/workflows/ideation.py` that ports `perform_ideation_temp_free.py` (research 04 §Option B) — driver loop that calls `ctx.tools.semantic_scholar.search_papers` and emits an idea dict suitable as `bfts_root.Input.idea`.
- Phase 2: new `overlay/workflows/gather_citations.py` mirroring Sakana's `gather_citations`; requires adding `citationStyles` to the S2 client's `BIBTEX_PAPER_FIELDS` (research 04 §Citation tooling — `client.py:33` one-line addition).

## Phase 4e: SandboxTemplate / Claim / WarmPool

**Why deferred:** Requires `agentSandbox.controller.extensions: true` in `values.local.yaml` *and* extending or wrapping `KubernetesAgentSandboxBackend` to claim from a `SandboxClaim`. Centaur today writes raw `Sandbox` CRs (research 01 §Capability matrix; research 03 §Sandboxing). MVP works fine without this.

**Sketch:** add `overlay/services/agent-sandbox-templates/{proposer,debugger,reviewer,gpu-experiment}.yaml` + companion `SandboxWarmPool` CRs; helm-install them as separate resources via a small chart in `overlay/charts/` (a new top-level overlay chart, since `values.local.yaml` only overlays `.centaur/contrib/chart`).

## Phase 4f: Upstream PRs (separate ownership)

These cannot be done in this repo per the workspace AGENTS.md "never edit `.centaur/`" rule:

1. **`.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:16`**: `_AGENT_SANDBOX_VERSION = "v1alpha1"` is hardcoded. When upstream agent-sandbox graduates v1alpha1 → v1beta1 (research 01 §Open questions #6), this constant breaks. Open a PR that reads the version from an env var. BFTS would bump the matching `_AGENT_SANDBOX_VERSION` constant at `overlay/tools/bfts_executor/client.py` in lockstep.
2. **`.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:113`**: `service: False` is hardcoded. BFTS doesn't need stable DNS (we use pod-name = sandbox-name for exec), so this isn't blocking — but worth flagging if a future BFTS phase wants DNS-resolved sandbox identity.

**Explicitly out of scope:** iron-proxy's `domains: ["*"]` posture (`.centaur/services/iron-proxy/iron-proxy.yaml`) is **not** on this list. iron-proxy is not on the BFTS data path (Spec correction #10 + #13) — BFTS pods make no outbound LLM calls; the workflow controller calls model providers from inside the api pod via its own egress path. BFTS pod egress is scoped by the `bfts-sandbox-egress` NetworkPolicy created by Task 1.7, not by iron-proxy. No iron-proxy PR is required for BFTS.

## Phase 4g: Known fixes (deferred from MVP)

- **MetricValue mean-collapse + first-metric direction footgun** (research 02 §Gotcha #7). The current `_bfts_metric.mean()` matches Sakana 1:1 to preserve behavior. Phase 4 fix: expose a Centaur-config reducer (`min`, `weighted_mean`, `lexicographic`) on `bfts_root.Input`.
- **Plot selection when N > 10** (research 02 §VLM review). MVP truncates the first 10 plots; Sakana calls a feedback model to pick the 10 most informative.
- **Journal-wide LLM summary** regenerated each step (research 02 §Gotcha #8) — extra cost per step. MVP skips it entirely.
- **(Resolved during planning — was: `__BFTS_EXIT__` marker collision.)** Task 1.6's real `_KubernetesSandboxAPI.run_command` reads the exit code from the upstream `WsApiClient` `ERROR_CHANNEL` JSON frame (mirroring `.centaur/services/api/api/sandbox/kubernetes.py:1503-1551`), so there is no in-band exit marker for agent code to collide with. Leaving the entry here to record the resolution.

---

## Closed during planning

Three architectural questions were resolved during plan review and bound to concrete tasks above. Recorded here so future readers don't re-open them.

- **Open Q-1 — Executor architecture (Architecture B):** "Should the BFTS executor warm up sandboxes through `ctx.agent_turn(...)` and pull `sandbox_id` from its return dict?" → **No.** `do_agent_turn` (`.centaur/services/api/api/workflow_engine.py:1124`) is "spawn → message → execute → wait-for-agent-terminal-result" and drags in `spawn_assignment`, slackbot session opening, agent-execution event rows, and a running harness. BFTS sandboxes have no harness; they're code-exec workers. The executor creates `agents.x-k8s.io/v1alpha1 Sandbox` CRDs directly via `kubernetes_asyncio.client.CustomObjectsApi` (mirroring `KubernetesAgentSandboxBackend._create_workload` at `.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:109-154`) with workflow-generated `sandbox_id = f"bfts-{ctx.run_id}-tree-{i}"`. **Implementation:** Phase 1 Task 1.6 (CRD body + lifecycle), Phase 2 Task 2.9 (workflow side).
- **Open Q-2 — State volume mechanism (inline `volumeClaimTemplates`):** "Should Phase 0 set `KUBERNETES_SANDBOX_STATE_VOLUME_ENABLED=1` in `values.local.yaml` to get a per-sandbox PVC?" → **No.** That env var is read globally by both `.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:21` and `.centaur/services/api/api/sandbox/kubernetes.py:102`; flipping it on attaches a PVC to every Centaur sandbox spawned for any reason. Instead the BFTS executor sets `spec.volumeClaimTemplates` directly inside the BFTS Sandbox CRD body it creates (10Gi `ReadWriteOnce`, cluster default storage class), mounted at `/workspace` on the executor pod (whose `WORKDIR` is `/workspace`, so Sakana's `os.path.join(os.getcwd(), 'working')` resolves correctly). Retention across pause/resume is shipped by the controller already (`shutdownPolicy: "Retain"` + `replicas: 0|1` patch — `.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:114, 159-185`). **Implementation:** Phase 1 Task 1.6 (inline `volumeClaimTemplates`), Task 1.8 (write-sentinel → pause → resume → read-sentinel retention smoke).
- **Open Q-3 — Egress scoping (dedicated NetworkPolicy):** "Should we file an upstream PR against iron-proxy to make its `domains: ["*"]` allowlist Helm-configurable?" → **No** for BFTS. BFTS pods make no outbound LLM calls (the workflow controller calls model providers from inside the api pod via its own egress path); iron-proxy is not on the BFTS data path. Egress is scoped at the K8s NetworkPolicy layer: the BFTS executor creates a single namespace-scoped `bfts-sandbox-egress` policy that selects pods labeled `centaur.ai/bfts-sandbox: "true"` and adds `Egress` rules for TCP/8000 to the api pod + TCP/443 to the public internet (Kubernetes NetworkPolicy is union-based, so this layers cleanly on top of the chart's default-deny at `.centaur/contrib/chart/templates/networkpolicy.yaml:9-13` and `-allow-dns` at L15-34). We do NOT add the `centaur.ai/managed: "true"` label to BFTS pods — that label is the podSelector for the chart's `-sandbox` policy at L307-327 which locks egress to api:8000 only and would block PyPI/dataset fetches. RBAC already permits namespaced NetworkPolicy creation (`.centaur/contrib/chart/templates/rbac.yaml:39-41`). **Implementation:** Phase 1 Task 1.7.

---

## Self-review

**Spec coverage:**
- Tree controller as durable workflow → Phase 2 (Tasks 2.7–2.9). ✓
- Per-node Sandbox isolation → Phase 1 `bfts_executor` (Task 1.6 CRD body + inline `volumeClaimTemplates`; Phase 0 only proves the platform via pure-`kubectl`, not the BFTS tool). ✓
- Hibernate/resume → spec correction #2: pod stop/start with PVC reattach. The Phase 1 `_KubernetesSandboxAPI.pause_sandbox/resume_sandbox` methods (Task 1.6) mirror `KubernetesAgentSandboxBackend.pause_by_id/resume_by_id` at `.centaur/services/api/api/sandbox/kubernetes_agent_sandbox.py:159-185`; retention is proven end-to-end by Task 1.8's `bfts-retention-smoke` recipe. The controller in Phase 2 holds one sandbox per *tree* (not per node) so working state persists across expansions inside the tree. Per-node pause/resume is a Phase 4e refinement.
- `num_drafts` parallelism → Phase 2 Task 2.9 fans out N child `bfts_tree` workflows.
- `max_debug_depth`/`debug_prob` → Phase 2 Task 2.3 selector + Task 2.7 expand_node branch routing.
- CPU-bound work in-sandbox → Phase 1 `exec_python`.
- GPU work via external job + webhook → deferred to Phase 4a, with corrected webhook→event topology.
- Scoring + best-node carry-forward → Phase 3 Task 3.5 (`select_best` + `bfts_artifacts`).
- VLM tool call → Phase 3 Task 3.2 + 3.3.
- Roles as persona+skill per turn → not in MVP; the spec's "roles" framing is a Phase 4e concern when `SandboxTemplate` per-role lands.
- Outer loop nightly reflection → deferred to Phase 4c, reformulated as `bfts_hyperparams` table (per Spec correction #5).
- Security bounds (NetworkPolicy egress + GitHub token scoping) → BFTS pod egress is scoped by Phase 1 Task 1.7's namespace-scoped `bfts-sandbox-egress` NetworkPolicy (api:8000 + internet:443 only, on top of chart default-deny). iron-proxy is not on the BFTS data path and stays out of scope (Spec correction #10 + #13).

**Placeholder scan:** searched the plan for `TBD`, `TODO`, `implement later`, `fill in details`, `add appropriate`, `similar to`. None found in any task body. All code blocks contain real code with concrete identifiers tied to imports.

**Type consistency:**
- `ExecutionResult` defined in `overlay/tools/bfts_executor/models.py` (Task 1.2); consumed by `BFTSExecutor.exec_python` (Task 1.3) and by `_bfts_expand.expand_node` (Task 2.7, via dict round-trip — the `_FakePodExecResult` and `_RealPodExecResult` dataclasses in `client.py` are both wired to populate `ExecutionResult` fields with matching names).
- `NodeRef` defined in `_bfts_select.py` (Task 2.3); converted from DAO rows in `bfts_tree.handler._to_noderef` (Task 2.8). Both use the same field names: `node_id`, `parent_id`, `root_id`, `is_buggy`, `is_buggy_plots`, `debug_depth`, `metric_score`, `stage_name`, `is_leaf`.
- `SearchConfig` defined in `_bfts_select.py` (Task 2.3); constructed in `bfts_tree.handler` (Task 2.8) using `Input` field names that match exactly: `num_drafts`, `num_workers`, `max_debug_depth`, `debug_prob`.
- `MetricValue` shape: same nested dict `{"metric_names": [...]}` used in `_bfts_metric.py` (Task 2.2), `_bfts_prompts.METRIC_PARSE_SPEC` (Task 2.4), `_bfts_state.update_node_metric` (Task 2.5 — stored as `metric_json` JSONB), and `_bfts_export.select_best` (Task 3.5 — reads `metric_json` back).
- `bfts_nodes` schema columns referenced from `_bfts_state.py`, `bfts_tree.py`, `_bfts_export.py` all match the SQL in `services/api/db/migrations/20260525000001_add_bfts_tables.sql` (Task 2.1).

No issues to fix.

---

## Execution handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-25-bfts-on-centaur.md`. Two execution options:**

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. Use `superpowers:subagent-driven-development`.
2. **Inline Execution** — execute tasks in this session, batch with checkpoints. Use `superpowers:executing-plans`.

**Which approach?**
