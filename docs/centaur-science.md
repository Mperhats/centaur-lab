# BFTS-on-Centaur: Specification

## Objective

Port AI Scientist-v2's best-first tree search into Centaur as a durable, self-hosted experiment-search loop. Centaur provides orchestration, isolation, and durability; the search logic and scoring are reimplemented as a workflow rather than run as Sakana's process.

**Reference implementation:** [SakanaAI/AI-Scientist-v2](https://github.com/SakanaAI/AI-Scientist-v2) — the original BFTS pipeline. Key files for porting: `launch_scientist_bfts.py` (entrypoint), `ai_scientist/treesearch/` (the tree-search module being reimplemented as a Centaur workflow), and `bfts_config.yaml` (the search parameters mapped below).

**Sandbox runtime:** [kubernetes-sigs/agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox) — a Kubernetes SIG Apps CRD/controller for isolated, stateful, single-pod agent runtimes (`Sandbox`, plus `SandboxTemplate`, `SandboxClaim`, `SandboxWarmPool` extensions). Per a Centaur maintainer's tweet ([gakonst, status 2059037129020150145](https://x.com/gakonst/status/2059037129020150145)), Centaur has adopted agent-sandbox as its sandbox layer. This is reported via that tweet and not otherwise confirmed in Centaur's published docs; the integration details below are inferred from agent-sandbox's documented capabilities, not from the tweet's specifics. Project is early (v0.1.x), so several roadmap features named below are aspirational, not shipped.

## Architecture

The system is a single Centaur workflow acting as the tree-search controller, fanning out to isolated sandboxes (and optionally external compute) for individual experiment evaluations. The controller owns the search tree in durable Postgres-backed state; node executions are disposable.

## Components

**Tree controller (workflow).** A durable workflow handler holds the node tree as checkpointed state. Each node represents one experiment attempt — generated code plus its resulting metrics. The handler implements best-first expansion: select the highest-scoring unexpanded node, expand it, score the children, repeat until a `steps` budget (max nodes) is exhausted. Because state is checkpointed per step, the search survives worker restarts and resumes from the last expansion rather than losing the tree.

**Node evaluation.** Each expansion runs an agent turn in its own `Sandbox` (agent-sandbox CRD): the agent writes experiment code, runs it, and returns metrics and artifacts. The sandbox boundary is the isolation primitive — unvetted agent-generated code never touches the controller or other nodes. Run sandboxes under gVisor by default and Kata Containers for nodes executing the least-trusted code, giving a kernel/VM-level boundary rather than shared-kernel namespacing. CPU-bound experiments run in-sandbox directly. Each node's working state lives on the Sandbox's persistent storage, which survives pod restarts — so a long experiment's intermediate state is not lost to a reschedule, and the best-node checkpoint has a durable home tied to the node's stable identity.

**Node lifecycle (hibernate/resume).** Use the Sandbox lifecycle controls to pause a node between expansions instead of tearing it down: a promising-but-not-yet-expanded node hibernates its state to persistent storage and auto-resumes when the controller next visits it. This decouples "node exists in the tree" from "node is consuming a running pod," letting the tree hold far more live nodes than there are active pods. Scheduled deletion reaps pruned branches.

**Parallelism.** The `num_drafts` parameter (independent root nodes) maps to N child workflows launched at Stage 1, each growing its own tree. Within a tree, concurrent node evaluations are dispatched as child workflows up to a configured fan-out, bounded by Centaur's execution-worker concurrency. Back the node pods with a `SandboxWarmPool` so expansions claim a pre-warmed pod in milliseconds rather than paying cold-start scheduling latency on every node — the relevant bottleneck for a search that spawns many short-lived evaluations. Define per-role pods as `SandboxTemplate`s and let the controller issue a `SandboxClaim` per node.

**Failure handling.** Port `max_debug_depth` and `debug_prob` as controller logic: when a node's experiment fails, attempt a debug turn with probability `debug_prob`, up to `max_debug_depth` retries on that path; otherwise prune the node and expand the next-best. This keeps the search from sinking unbounded effort into a dead branch.

**Compute split.** CPU-bound work (simulation, symbolic, small numerical experiments) runs in-sandbox. GPU work is dispatched to an external process — the controller enqueues a job via `ctx.step`, then blocks on `ctx.wait_for_event`; the external process posts results back through a signed `/api/webhooks/{slug}` callback that resumes the run. Note that agent-sandbox is an orchestration/lifecycle layer, not a scheduler extension: it standardizes isolation, persistence, and warm-pooling but does not itself provision GPUs. A GPU node would be a Sandbox scheduled onto a GPU-equipped node pool (standard Kubernetes node selectors/resource requests), or an entirely external process — either way the compute-provisioning problem is unchanged by the integration.

**Scoring.** Bring your own metric harness — the objective signal is whatever the experiment emits. Optional VLM (vision-language model) figure/result review is wired as a tool call: a vision-capable model inspects generated plots for legibility and caption/claim alignment before a node is carried forward. Best-performing node checkpoints are carried forward as the basis for subsequent expansions.

**Roles.** Specialist behavior (proposer, debugger, reviewer) is expressed as persona + skill applied per agent turn, not as distinct agents. The controller selects the role for each turn; there is no agent-to-agent delegation. Where roles need different resources or isolation (e.g. a debugger with more memory, a GPU-experiment node under Kata), back each role with its own `SandboxTemplate` so the resource/isolation profile is declarative rather than per-call configuration.

**Outer loop.** Centaur's nightly reflection treats search hyperparameters — `debug_prob`, scoring priors, expansion policy — as a tunable skill, adjusting them between runs based on prior search performance. This is the second-order recursive loop on top of the first-order experiment search.

## Security bounds

The agent writes and executes the code it runs, so containment is enforced by infrastructure, not by confirmation prompts. Scope the GitHub token to only the target repos; lock the egress allowlist to the model provider, the experiment data sources, and the external compute callback host. Any external GPU process inherits the same posture — disposable compute, scoped credentials, restricted egress — since the sandbox NetworkPolicy doesn't extend to it.

## Explicit non-goals / gaps

This port does not run Sakana's `treesearch` module as-is; the controller is reimplemented as a workflow. The agent-sandbox integration improves isolation (gVisor/Kata), node persistence/hibernation, and warm-pool allocation latency, but it does **not** provide GPU execution, per-interaction credential scoping (still deployment-scoped — that lives in Centaur's iron-proxy layer, not the sandbox runtime), or autonomous multi-agent coordination. agent-sandbox is v0.1.x; treat hibernation depth, cross-sandbox memory sharing, and similar roadmap items as unverified until tested. The Centaur integration itself is reported via a maintainer tweet and not confirmed in published docs — verify against Centaur's repo before building on integration-specific behavior. The novelty-check (Semantic Scholar) and citation tooling from AI Scientist-v2 are out of scope unless added as separate tools.