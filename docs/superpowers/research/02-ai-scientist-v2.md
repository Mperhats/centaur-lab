# Research: AI Scientist-v2 BFTS internals (porting reference)

> Source of truth: the vendored Sakana repo at `.scientist/`. Every claim of substance below is cited as `file:line` against that tree. Read-only investigation — nothing under `.scientist/` was modified or executed.

## TL;DR

- The "BFTS" engine in `.scientist/` is actually a **4-stage curriculum manager** wrapped around a per-stage best-first search. The advertised search loop is only one layer of the system.
- The per-stage search is a synchronous, batch-parallel loop: every `step()` selects `num_workers` nodes (root drafts, debug retries, or improvements of best), dispatches them to a `ProcessPoolExecutor`, blocks on all of them, then re-selects. There is **no async, no event loop, no fan-out across steps** (`parallel_agent.py:1183`, `parallel_agent.py:2053-2191`).
- A "node expansion" is not one LLM call — it is a fixed pipeline of **5–7 LLM calls + 2–3 subprocess code executions** per node (draft/debug/improve LLM call → exec code → LLM judge bug → LLM emit metric-parse code → exec it → LLM parse metrics → LLM emit plot code → exec it → VLM analyze plots → LLM summarize VLM). Most of the wall-clock cost is in `interpreter.run()`, not the LLM.
- Code execution is `multiprocessing.Process` + `multiprocessing.Queue` running in the same Python image, with `os.chdir(workspace)` then `exec(compile(code, ...))`. **No container boundary today.** The replacement Sandbox must reimplement the `Interpreter.run(code, reset_session=True) -> ExecutionResult` contract: stdout/stderr capture, timeout via SIGINT then SIGKILL, persisted `experiment_data.npy` and `*.png` artifacts in a `working/` subdir.
- The Centaur port can drop: process cleanup logic, GPUManager, the `coolname`/`shutup`/`rich` UI, the writeup/citation/review stages (separate concerns), the `manager.pkl` blob, and the implicit-best-via-LLM selector (replace with a deterministic policy).
- The Centaur port must preserve: the `Node` shape (the journal is the search state, gets serialized), `num_drafts`/`debug_prob`/`max_debug_depth` semantics, the metric format (multi-dataset/multi-metric), VLM-gated `is_buggy_plots`, and the experiment-results directory layout (downstream `aggregate_plots` and writeup read from it).

## Entrypoint walkthrough (`launch_scientist_bfts.py`)

The "BFTS run" is one of seven phases in this script. Only **phase 5** (`perform_experiments_bfts`) is the BFTS engine being ported. The other phases are post-processing that the spec already places out of scope.

| Phase | Lines | What it does |
|---|---|---|
| 1. CLI parsing | `launch_scientist_bfts.py:42-131` | Args control writeup/review stages, not search. Search params live entirely in `bfts_config.yaml`. |
| 2. Environment setup | `launch_scientist_bfts.py:182-189` | Sets `AI_SCIENTIST_ROOT` env var (consumed deep in plotting code at `parallel_agent.py:2268`), enumerates GPUs via `torch.cuda.device_count()`. |
| 3. Idea loading | `launch_scientist_bfts.py:191-247` | Reads ideas JSON (default `ideas/i_cant_believe_its_not_better.json`), selects `--idea_idx`, dumps as `idea.md` + `idea.json` into `experiments/<timestamp>_<name>_attempt_<n>/`. |
| 4. Config rewrite | `launch_scientist_bfts.py:249-254` → `bfts_utils.py:45-76` | `edit_bfts_config_file` copies the global `bfts_config.yaml` into the idea dir, then injects `desc_file=<idea.json>`, `workspace_dir=<idea_dir>`, `data_dir=<idea_dir>/data`, `log_dir=<idea_dir>/logs`. |
| 5. **BFTS run** | `launch_scientist_bfts.py:256` → `perform_experiments_bfts_with_agentmanager.py:58-256` | The entire 4-stage tree search. This is the port target. |
| 6. Writeup pipeline | `launch_scientist_bfts.py:271-302` | `aggregate_plots` → `gather_citations` → `perform_writeup` / `perform_icbinb_writeup`. Out of scope per spec. |
| 7. Paper review | `launch_scientist_bfts.py:304-319` | `perform_review` (text) + `perform_imgs_cap_ref_review` (post-paper VLM). Distinct from the in-loop VLM. Out of scope. |
| 8. Process reaper | `launch_scientist_bfts.py:321-369` | Greps `psutil` for anything matching `["python", "torch", "mp", "bfts", "experiment"]` and SIGTERM/SIGKILLs it. **This is a smell — proof that the in-process search leaks subprocesses.** A workflow-based port doesn't need any of it. |

**Mapping into the Centaur workflow:**
- Phases 1–4 become workflow inputs (the idea + the config) supplied at workflow start.
- Phase 5 becomes the workflow body — the tree controller.
- Phases 6–7 are explicitly out of scope per `docs/centaur-science.md:41`.
- Phase 8 disappears: in Centaur, sandbox lifecycle owns the process, not the controller.

## `bfts_config.yaml` — full field reference

Defaults from `.scientist/bfts_config.yaml`. Types from `.scientist/ai_scientist/treesearch/utils/config.py:26-110`.

### (a) Search / tree params — preserve verbatim

| Field | Type | Default | Effect | Port status |
|---|---|---|---|---|
| `agent.search.max_debug_depth` | int | 3 | Cap on consecutive `_debug` retries on one path (`parallel_agent.py:1986`). | **Centaur input.** |
| `agent.search.debug_prob` | float | 0.5 | Probability per scheduling pass of preferring a buggy leaf over a fresh improvement (`parallel_agent.py:1964`). | **Centaur input.** |
| `agent.search.num_drafts` | int | 3 | Number of independent root draft nodes per stage. Search refuses to leave drafting phase until `len(journal.draft_nodes) >= num_drafts` (`parallel_agent.py:1952`). | **Centaur input.** Maps to N child workflows per spec. |
| `agent.steps` | int | 5 | Fallback per-stage iteration cap when stage-specific overrides absent (`agent_manager.py:171-177`). | Preserve. |
| `agent.stages.stage{1..4}_max_iters` | int | 20/12/12/18 | Per-stage iteration cap (`agent_manager.py:174-176`). | Preserve. |
| `agent.num_workers` | int | 4 | Width of the per-`step()` dispatch fan-out (`parallel_agent.py:1183`). Capped to GPU count if any GPUs present (`parallel_agent.py:1178-1180`). | Preserve as fan-out bound. |
| `agent.type` | enum | `parallel` | Only `parallel` is wired; `sequential` is asserted but no separate implementation exists (`utils/config.py:173-174`). | **Drop** — port is always parallel. |
| `agent.multi_seed_eval.num_seeds` | int | 3 | After a stage's best node is chosen, re-run with N seeds and aggregate (`parallel_agent.py:1261-1330`, `agent_manager.py:738-758`). | Preserve. |

### (b) Agent / role params — mostly preserve, some prompt-coupled

| Field | Type | Default | Effect | Port status |
|---|---|---|---|---|
| `agent.k_fold_validation` | int | 1 | Prompt-injected hint to use k-fold CV (`parallel_agent.py:389-392`). `1` disables. | Preserve as workflow input. |
| `agent.expose_prediction` | bool | false | Declared in dataclass (`utils/config.py:60`) but not referenced in `parallel_agent.py` or `agent_manager.py`. **Dead config.** | **Drop.** |
| `agent.data_preview` | bool | false | When true, runs `data_preview` over `workspace_dir/input` and prefixes prompts (`parallel_agent.py:481-482`). | Drop unless the port supports user-supplied datasets at MVP. |
| `experiment.num_syn_datasets` | int | 1 | Number of synthetic datasets the prompt instructs the agent to use (`parallel_agent.py:315-326`). | Preserve as workflow input. |
| `debug.stage4` | bool | false | Declared in dataclass; **not referenced anywhere in `ai_scientist/`** (verified via grep). | **Drop.** |

### (c) Model / provider params — Centaur inputs

Each `StageConfig` is `{model, temp, max_tokens?, thinking?, betas?}` (`utils/config.py:35-41`).

| Field | Default | Used by |
|---|---|---|
| `agent.code.model` | `anthropic.claude-3-5-sonnet-20241022-v2:0` | All code-generation calls: draft / debug / improve / hyperparam / ablation / plotting (`parallel_agent.py:665-667, 1218-1219, 1836-1837, 1898-1899`). |
| `agent.code.temp` | 1.0 | same |
| `agent.code.max_tokens` | 12000 | same |
| `agent.feedback.model` | `gpt-4o-2024-11-20` | Bug judge (`parallel_agent.py:707-709`), metric extractor (`:1625-1627`), stage-completion judge (`agent_manager.py:368-370, 477-480`), substage-goal generator, plot selection. |
| `agent.feedback.temp` | 0.5 | same |
| `agent.feedback.max_tokens` | 8192 | same |
| `agent.vlm_feedback.model` | `gpt-4o-2024-11-20` | In-loop figure analyzer (`parallel_agent.py:1013-1014`). |
| `agent.vlm_feedback.temp` | 0.5 | same |
| `agent.summary.{model,temp}` | optional (default `gpt-4o`/`0.3`) | `Journal.generate_summary` (`journal.py:544-545`), `journal.save_experiment_notes` (`journal.py:607-608`). |
| `agent.select_node.{model,temp}` | optional (default `gpt-4o`/`0.3`) | `Journal.get_best_node` LLM judge (`journal.py:470-482`). |
| `report.model` / `report.temp` | `gpt-4o-2024-11-20` / 1.0 | Used by `journal2report` (`journal2report.py:28-29`) — only invoked if `generate_report=True`. |

All of these become Centaur workflow inputs — defaults preserved, but should be overridable per run.

### (d) Execution / IO params — must preserve contract

| Field | Default | Effect |
|---|---|---|
| `exec.timeout` | 3600 (sec) | Hard cap per code execution (`interpreter.py:279`). After timeout, SIGINT; after timeout+60, SIGKILL (`interpreter.py:283-289`). |
| `exec.agent_file_name` | `runfile.py` | Name of the file the code is written to before `exec()` (`interpreter.py:139-140`). Shows up in tracebacks. |
| `exec.format_tb_ipython` | false | If true, uses `IPython.core.ultratb.VerboseTB` for tracebacks (`interpreter.py:42-45`). Cosmetic. |
| `data_dir` | `data` | Source dir to copy/symlink into `workspace_dir/input` (`utils/config.py:209-216`). |
| `preprocess_data` | false | If true, unzips archives in `input/` (`utils/__init__.py:49-95`). |
| `copy_data` | true | If false, symlink instead of copy. **Symlinks break inside containers** — port must copy. |
| `workspace_dir` | `workspaces` | Top-level workspace root. Sub-workspaces created per-process at `workspace_dir/process_<name>/working` (`parallel_agent.py:1436-1441`). |
| `log_dir` | `logs` | Top-level log root. |
| `exp_name` | `run` | Used to compose `<idx>-<exp_name>` for both `log_dir` and `workspace_dir` (`utils/config.py:163-167`). |

### (e) Report / output params — port can drop

| Field | Default | Effect | Port status |
|---|---|---|---|
| `generate_report` | true | Triggers `overall_summarize()` → writes 4 JSON summary files (`perform_experiments_bfts_with_agentmanager.py:227-256`). | Optional; port can emit a simpler summary. |
| `goal`, `eval`, `desc_file` | null / null / injected | One of `desc_file` or `goal` must be set (`utils/config.py:143-146`). The entrypoint always injects `desc_file` from the idea JSON. | **Drop** `goal`/`eval` paths; use structured idea input. |

## Tree data model

### `Node` — `.scientist/ai_scientist/treesearch/journal.py:43-291`

```python
@dataclass(eq=False)
class Node(DataClassJsonMixin):
    # plan & code
    plan: str = ""
    overall_plan: str = ""               # synthesized post-hoc (log_summarization.py:262-296)
    code: str = ""                       # the experiment script
    plot_code: str = None                # the plotting script
    plot_plan: str = None

    # general
    step: int = None                     # assigned by Journal.append
    id: str = uuid4().hex
    ctime: float = time.time()
    parent: Optional["Node"] = None      # in-memory pointer; serialized as parent_id
    children: set["Node"] = set()        # set of child Nodes
    exp_results_dir: str = None          # path on disk where artifacts live

    # execution
    _term_out: list[str] = None          # captured stdout/stderr
    exec_time: float = None
    exc_type: str | None = None
    exc_info: dict | None = None
    exc_stack: list[tuple] | None = None

    # metric-parsing pass (a second exec)
    parse_metrics_plan: str = ""
    parse_metrics_code: str = ""
    parse_term_out: list[str] = None
    parse_exc_type: str | None = None
    parse_exc_info: dict | None = None
    parse_exc_stack: list[tuple] | None = None

    # plotting pass (a third exec)
    plot_term_out: list[str] = None
    plot_exec_time: float = None
    plot_exc_type: str | None = None
    plot_exc_info: dict | None = None
    plot_exc_stack: list[tuple] | None = None

    # evaluation
    analysis: str = None                 # LLM bug-summary
    metric: MetricValue = None           # see below; None or WorstMetricValue == "buggy"
    is_buggy: bool = None                # set True if exc_type != None OR LLM judges bug
    is_buggy_plots: bool = None          # set by VLM gate

    # plotting artifacts
    plot_data: dict = {}
    plots_generated: bool = False
    plots: List[str] = []                # web-relative paths
    plot_paths: List[str] = []           # absolute paths

    # VLM feedback
    plot_analyses: List[str] = []        # list of {"analysis": str, "plot_path": str}
    vlm_feedback_summary: List[str] = [] # actually a str in practice
    datasets_successfully_tested: List[str] = []

    exec_time_feedback: str = ""         # injected for stage 3 if exec too short
    ablation_name: str = None            # stage 4 only
    hyperparam_name: str = None          # stage 2 only
    is_seed_node: bool = False           # multi-seed eval child
    is_seed_agg_node: bool = False       # multi-seed aggregation node
```

**There is no `status` enum.** Status is derived from three booleans:
- `is_buggy is None` → not yet executed
- `is_buggy is True` → execution or metric-parse failed
- `is_buggy is False and is_buggy_plots is False` → "good" (`journal.py:404-407`)
- `is_buggy_plots is True` → executed but plots are useless (excluded from `good_nodes`)

**Stage name (computed)** — `journal.py:158-168`:

```python
@property
def stage_name(self) -> Literal["draft", "debug", "improve"]:
    if self.parent is None:
        return "draft"
    return "debug" if self.parent.is_buggy else "improve"
```

**Debug depth (computed, recursive)** — `journal.py:202-212`:

```python
@property
def debug_depth(self) -> int:
    if self.stage_name != "debug":
        return 0
    return self.parent.debug_depth + 1
```

### `MetricValue` — `.scientist/ai_scientist/treesearch/utils/metric.py:112-325`

The metric is **not a scalar**. It's a nested dict:

```python
{
  "metric_names": [
    {
      "metric_name": str,           # e.g. "validation_loss"
      "lower_is_better": bool,
      "description": str,
      "data": [
        {"dataset_name": str, "final_value": float, "best_value": float},
        ...
      ]
    },
    ...
  ]
}
```

Comparison reduces to `np.mean` of all `final_value`s across all metrics and datasets, with direction set by the **first metric's** `lower_is_better` (`metric.py:191-203, 302-322`). Multi-metric trees inherit the first metric's direction — a real footgun.

`WorstMetricValue` (`metric.py:327-341`) is a `value=None` sentinel that always compares worse than any valid metric. Assigned on any failure path.

### `Journal` — `.scientist/ai_scientist/treesearch/journal.py:361-613`

Flat `list[Node]` plus convenience views:
- `draft_nodes`: `[n for n in nodes if n.parent is None]`
- `buggy_nodes`: `[n for n in nodes if n.is_buggy]`
- `good_nodes`: `[n for n in nodes if n.is_buggy is False and n.is_buggy_plots is False]`
- `get_best_node(only_good, use_val_metric_only, cfg)`: returns the highest-`metric` good node — but **only if there's exactly one**. Otherwise calls an LLM (`gpt-4o` by default) with all candidate `(id, metric, analysis, vlm_feedback)` tuples and asks it to pick (`journal.py:420-502`). Falls back to `max(nodes, key=lambda n: n.metric)` on LLM failure.

This LLM-as-arbiter is a Sakana-specific choice that the Centaur port can drop in favor of a deterministic argmax — the port spec already says scoring is "bring your own metric harness" (`docs/centaur-science.md:29`).

## Best-first expansion algorithm

The actual algorithm lives in **two places**: `AgentManager.run` (`agent_manager.py:692-829`) is the outer curriculum, and `ParallelAgent._select_parallel_nodes` (`parallel_agent.py:1931-2051`) is the per-step selector.

### Outer loop (curriculum) — `agent_manager.py:692-829`

```
for each main_stage in [1,2,3,4]:
    for each substage:
        agent = ParallelAgent(stage)
        if previous substage exists:
            seed journal with best_node from previous (as a non-parent root)
        loop:
            agent.step()                           # ← per-step BFTS inside the stage
            if check_stage_completion(stage):
                run multi_seed_eval(best_node)     # N seeded replicas
                run plot_aggregation(best_node)
                break                              # to next main stage
            if check_substage_completion(stage):
                create next substage (LLM picks name+goals)
                break
```

Stage 1 ends when ≥1 good node exists OR max_iters hit. Stage 2 ends via VLM judge on training-curve stability + dataset coverage (`agent_manager.py:444-498`). Stage 3 has an exec-time policing rule that re-prompts the agent if experiments finish too fast (`agent_manager.py:511-530`). Stage 4 just runs to max_iters.

**For the Centaur MVP, this is overkill** — stages 2–4 are AI-Scientist-specific curriculum, not BFTS. Port stage 1 first.

### Inner loop (BFTS per stage) — `parallel_agent.py:1931-2051`

```python
def _select_parallel_nodes() -> List[Optional[Node]]:
    nodes_to_process = []
    processed_trees = set()
    while len(nodes_to_process) < self.num_workers:
        # 1. Drafting: until num_drafts roots exist, always produce a draft
        if len(journal.draft_nodes) < search_cfg.num_drafts:
            nodes_to_process.append(None)              # None means "new draft"
            continue

        viable_trees = [
            root for root in journal.draft_nodes
            if not all(leaf.is_buggy for leaf in self._get_leaves(root))
        ]

        # 2. Debug: with prob debug_prob, prefer a random debuggable leaf
        if random.random() < search_cfg.debug_prob:
            debuggable = [
                n for n in journal.buggy_nodes
                if n.is_leaf and n.debug_depth <= search_cfg.max_debug_depth
            ]
            if debuggable:
                node = random.choice(debuggable)
                tree_id = id(root_of(node))
                if tree_id not in processed_trees or len(processed_trees) >= len(viable_trees):
                    nodes_to_process.append(node); processed_trees.add(tree_id); continue

        # 3. Stage-2 / Stage-4 short circuit
        if stage_name.startswith("4_"): nodes_to_process.append(best_stage3_node); continue
        if stage_name.startswith("2_"): nodes_to_process.append(best_stage1_node); continue

        # 4. Improve: pick best good node, one per tree
        if not journal.good_nodes:
            nodes_to_process.append(None); continue        # back to drafting
        best = journal.get_best_node(cfg=cfg)
        tree_id = id(root_of(best))
        if tree_id not in processed_trees or len(processed_trees) >= len(viable_trees):
            nodes_to_process.append(best); processed_trees.add(tree_id); continue
        # tree already taken this step → try next-best
        for node in sorted(good_nodes, key=lambda n: n.metric, reverse=True):
            ...
    return nodes_to_process
```

Notes for the port:
- The selection policy is **best-first with debug retries**, not UCB or anything fancier. The "exploration" knob is `debug_prob`; the "diversification" knob is the one-node-per-tree-per-step constraint.
- `num_drafts` controls **fan-out at Stage 1 only**. After drafting, all expansions happen inside whichever tree the best node lives in. The spec's "N child workflows per draft" mapping (`docs/centaur-science.md:23`) over-promises here — Sakana does not grow N parallel trees post-Stage-1.
- The "one node per tree per step" rule is enforced via `id(tree_root)` (object identity, not hash) — translates to "max one expansion per root subtree per scheduling pass".
- `_select_parallel_nodes` can deadlock when `num_workers > len(viable_trees)` and all trees are processed — the inner loop falls back to allowing duplicates via `len(processed_trees) >= len(viable_trees)`.

### `step()` — `parallel_agent.py:2053-2191`

After selection, `step()`:
1. Serializes each selected node via `to_dict()` (pickle-tests it for safety).
2. Generates a journal-wide `memory_summary` via an LLM call (`generate_summary`).
3. For stage 2: generates one new `HyperparamTuningIdea` per node.
4. For stage 4: generates one new `AblationIdea` per node.
5. Submits all N to `self.executor` (ProcessPool).
6. Blocks on `future.result(timeout=self.cfg.exec.timeout)` for each.
7. `Node.from_dict(result_data, self.journal)` rehydrates the child node and re-links parent/child via journal lookup.
8. `journal.append(result_node)` assigns the new `step` number.

## Agent turn shape

One expansion = one call to `_process_node_wrapper` (`parallel_agent.py:1409-1796`), which runs in a worker process. The pipeline:

1. **Branch selection** (lines 1486-1522):
   - `parent_node is None` → `_draft()` (lines 453-492)
   - `parent_node.is_buggy` → `_debug(parent_node)` (lines 494-521)
   - stage 2 with non-buggy parent + hyperparam_idea → `_generate_hyperparam_tuning_node` (lines 557-603)
   - stage 4 with non-buggy parent + ablation_idea → `_generate_ablation_node` (lines 605-656)
   - `seed_eval=True` → `_generate_seed_node` (copies parent code as-is, lines 549-555)
   - else → `_improve(parent_node)` (lines 523-547)

2. **LLM call #1: code generation** — `plan_and_code_query` (lines 658-681). Builds a markdown prompt (compiled from nested dicts via `compile_prompt_to_md`), calls `agent.code.model`, extracts the python code block + the preceding natural-language plan. Retries 3x with a "parsing feedback" message if extraction fails.

3. **Code exec #1: experiment** — `process_interpreter.run(child_node.code, True)` (line 1526). Returns `ExecutionResult(term_out, exec_time, exc_type, exc_info, exc_stack)`.

4. **LLM call #2: bug judge** — `parse_exec_result` (lines 683-718). Uses `agent.feedback.model` with `review_func_spec` (`{is_bug: bool, summary: str}`). Sets `node.analysis = summary` and `node.is_buggy = is_bug OR (exc_type is not None)`.

5. **LLM call #3: metric-parse code generation** (lines 1554-1594). Asks the same model that wrote the experiment to write a separate script that reads `experiment_data.npy` and prints out final-vs-best for each (dataset, metric) pair. Skipped if `seed_eval=True` (reuses parent's parse code).

6. **Code exec #2: metric parse** — `process_interpreter.run(parse_metrics_code, True)` (line 1597).

7. **LLM call #4: metric extraction** (lines 1619-1628). Feeds the parse-exec stdout to `agent.feedback.model` with `metric_parse_spec`. Returns the nested `{metric_names: [...]}` dict that becomes `MetricValue`.

8. **If not buggy: LLM call #5: plotting code generation** — `_generate_plotting_code` (lines 720-833). Stage 3/4 are seeded with stage 2's/3's `best_node.plot_code`. Output stored on `node.plot_code`.

9. **Code exec #3: plotting** (line 1695). Retried up to 3x if plot exec fails.

10. **If plots exist: LLM call #6: VLM analysis** — `_analyze_plots_with_vlm` (lines 894-1033). Base64-encodes up to 10 PNGs and sends as `image_url` content to `agent.vlm_feedback.model` with `vlm_feedback_spec`. Sets `node.plot_analyses`, `node.vlm_feedback_summary`, `node.is_buggy_plots`.

11. **LLM call #7: dataset-tested judge** — `_determine_datasets_successfully_tested` (lines 835-892). Asks the feedback model to name which datasets the VLM-confirmed plots actually came from. Populates `node.datasets_successfully_tested`.

Net: **5 LLM calls + 3 code execs in the happy path**, 6–7 LLM calls if VLM + dataset-judge run, repeated up to 3x on plot exec failure.

### Prompt structure

All prompts are built as nested `dict`s (`PromptType = str | dict | list`, `backend/utils.py:6`), then compiled to markdown via `compile_prompt_to_md` (`backend/utils.py:44-102`). Each dict key becomes an `#`-header at the current depth. Lists become bullet items. This means the port should preserve prompts as data structures, not strings — the compilation is the contract.

Key reusable prompt fragments live as `MinimalAgent` properties at `parallel_agent.py:273-451`:
- `_prompt_environment` — package list (shuffled each call for entropy)
- `_prompt_impl_guideline` — the giant "CRITICAL GPU REQUIREMENTS" block; encodes the `experiment_data.npy` data-saving contract
- `_prompt_resp_fmt` — "natural language followed by single python codeblock"

## Code execution contract

This is the **most important section for the sandbox port.** Everything that needs to happen on the agent-sandbox side is defined here.

### Today: `Interpreter` — `.scientist/ai_scientist/treesearch/interpreter.py:81-313`

```python
class Interpreter:
    def __init__(self, working_dir, timeout=3600, format_tb_ipython=False,
                 agent_file_name="runfile.py", env_vars={}):
        self.working_dir = Path(working_dir).resolve()      # asserted to exist
        ...

    def run(self, code: str, reset_session=True) -> ExecutionResult:
        # 1. If reset_session, kill the prior child Process and start a fresh one.
        # 2. Send `code` to the child via a multiprocessing.Queue.
        # 3. Wait for "state:ready" (10s timeout) then "state:finished".
        # 4. After self.timeout, send SIGINT. After self.timeout + 60, SIGKILL.
        # 5. Drain stdout/stderr queue until <|EOF|> marker.
        # 6. Return ExecutionResult(term_out, exec_time, exc_type, exc_info, exc_stack).
```

The child process (`_run_session`, lines 130-161):

```python
def _run_session(self, code_inq, result_outq, event_outq):
    import shutup; shutup.mute_warnings()
    for k, v in self.env_vars.items(): os.environ[k] = v
    os.chdir(str(self.working_dir))
    sys.path.append(str(self.working_dir))
    sys.stdout = sys.stderr = RedirectQueue(result_outq)

    global_scope = {}
    while True:
        code = code_inq.get()
        os.chdir(str(self.working_dir))                       # again, defensively
        with open(self.agent_file_name, "w") as f: f.write(code)
        event_outq.put(("state:ready",))
        try:
            exec(compile(code, self.agent_file_name, "exec"), global_scope)
        except BaseException as e:
            ...                                                # capture traceback
            event_outq.put(("state:finished", e_cls_name, exc_info, exc_stack))
        else:
            event_outq.put(("state:finished", None, None, None))
        result_outq.put("<|EOF|>")
```

**What the port's Sandbox `exec` wrapper must reproduce:**

| Behavior | Source |
|---|---|
| `chdir(workspace)` before exec; agent code uses `os.path.join(os.getcwd(), 'working')` to know where to write artifacts | `interpreter.py:120, 138`; agent prompt at `parallel_agent.py:339-341` |
| Capture combined stdout+stderr as `list[str]` chunks | `interpreter.py:128, 70-78` |
| Hard timeout: SIGINT at T, SIGKILL at T+60 | `interpreter.py:283-289` |
| Return: `{term_out, exec_time, exc_type, exc_info, exc_stack}` | `interpreter.py:26-37` |
| Truncate `term_out` rendering at 5100 chars (2500 head/tail) before passing to LLM | `utils/response.py:41-52`, called from `Node.term_out` |
| Persist `*.npy` and `*.png` files in `working/` for downstream LLM consumption | agent prompts at `parallel_agent.py:339-385, 730-734` |
| Move artifacts to `logs/<run>/experiment_results/experiment_<node.id>_proc_<pid>/` after exec succeeds | `parallel_agent.py:1715-1766` |
| Honor `AI_SCIENTIST_ROOT` env var (consumed by seed-aggregation plotting code) | `parallel_agent.py:1354, 2268` |
| `agent_file_name` (default `runfile.py`) is the filename shown in tracebacks | `interpreter.py:144, 55` |

**What can be dropped:**
- The interactive REPL persistence (`reset_session=False`) is never invoked from the BFTS path — every call uses `reset_session=True`. The Sandbox `exec` can be one-shot.
- The `RedirectQueue` workaround is only there because `exec()` runs in the same process tree as the controller. With a real container boundary, ordinary stdout pipes suffice.
- The `<|EOF|>` marker is for the in-process queue protocol; not needed across HTTP/gRPC.

### Workspace layout — what the port has to produce on disk

The agent prompt **explicitly tells the agent** to write to specific paths (`parallel_agent.py:338-385`):

```
working_dir = os.path.join(os.getcwd(), 'working')
os.makedirs(working_dir, exist_ok=True)
# ... experiment runs ...
np.save(os.path.join(working_dir, 'experiment_data.npy'), experiment_data)
# plots saved as working_dir/*.png
```

The Sandbox must therefore present a writable `working/` subdirectory under its `cwd`. After exec, the controller moves the contents to a per-node directory (`parallel_agent.py:1719-1733, 1739-1766`):

```
logs/<exp>/experiment_results/experiment_<node.id>_proc_<pid>/
    ├── experiment_code.py           # the agent's code
    ├── plotting_code.py             # the agent's plot code
    ├── experiment_data.npy          # numpy metric dump
    ├── *.png                        # plots
    └── (later) aggregation_plotting_code.py for seed-agg nodes
```

The `parallel_agent.py:1755` line constructs a web-relative path of the form `../../logs/<run>/experiment_results/experiment_<id>_proc_<pid>/<plot>.png` and stores it on `Node.plots`. This is downstream-consumed by `aggregate_plots` and the writeup. The port can store cleaner identifiers but must give downstream consumers a stable lookup.

## Debug / retry policy

`max_debug_depth` and `debug_prob` are implemented in `_select_parallel_nodes` (`parallel_agent.py:1964-2004`):

```python
if random.random() < search_cfg.debug_prob:                # prob-gated, not deterministic
    debuggable_nodes = [
        n for n in self.journal.buggy_nodes
        if isinstance(n, Node)
        and n.is_leaf                                       # only leaves
        and n.debug_depth <= search_cfg.max_debug_depth     # bounded path length
    ]
    if debuggable_nodes:
        node = random.choice(debuggable_nodes)              # uniform random
        ...
```

When the selector returns a buggy node, `_process_node_wrapper` (`parallel_agent.py:1490-1493`) checks `parent_node.is_buggy` and routes to `MinimalAgent._debug` (`parallel_agent.py:494-521`). The debug prompt includes:
- the parent's buggy code (`wrap_code(parent_node.code)`)
- the parent's `term_out`
- the parent's `vlm_feedback_summary` (often empty for buggy nodes)
- the parent's `exec_time_feedback`

The new child inherits no execution state beyond what's in the prompt. `Node.debug_depth` is recursive (`journal.py:202-212`):

```python
return self.parent.debug_depth + 1   # if parent is also a debug node
```

So if a draft fails three times and each debug also fails, the third debug child has `debug_depth=3` and (with `max_debug_depth=3`) becomes ineligible for further debugging. The branch is effectively pruned **passively** — there's no explicit prune call. The buggy leaf just stops being selected, and the best-first selector keeps choosing other trees' good nodes for improvement.

## Concurrency model

**Today (Sakana):**
- `ProcessPoolExecutor(max_workers=cfg.agent.num_workers)` instantiated once per `ParallelAgent` (`parallel_agent.py:1183`).
- Each `step()` is a synchronous batch: select N, dispatch N, **wait for all N**, then re-select (`parallel_agent.py:2148-2191`). The next batch cannot start until the slowest worker in the current batch finishes (or times out at `cfg.exec.timeout`).
- `_process_node_wrapper` runs the entire 5–7-call pipeline (LLM calls + code execs) sequentially **inside** the worker process. The pool parallelizes across nodes, not across the steps of one node.
- `GPUManager` (`parallel_agent.py:1091-1117`) pins one GPU per active worker by setting `CUDA_VISIBLE_DEVICES`. If GPUs detected, `num_workers = min(num_workers, num_gpus)` (`parallel_agent.py:1178-1180`).
- Inside `_process_node_wrapper`, the `Interpreter` itself spawns yet another `multiprocessing.Process` per code execution (`interpreter.py:170-174`). So a single agent turn looks like: **controller → worker pool process → interpreter child process**.

**Bottleneck analysis:** the dominant cost per node is `interpreter.run(experiment_code)` with `cfg.exec.timeout=3600`. LLM calls are at most a few minutes total. So scaling across nodes is GPU-bound (or CPU-bound if no GPU). LLM API rate limits are secondary.

**Mapping to Centaur:**
- `num_drafts` × independent Stage-1 trees → independent child workflows.
- `num_workers` within a tree → bounded `ctx.step` fan-out per controller step. The synchronous-batch semantics map naturally to "fire N steps, `wait` for all N, then loop" inside the workflow handler.
- The per-node sequential pipeline (LLM → exec → LLM → exec → LLM) should become individual `ctx.step` calls or sandbox-tool invocations so the durable workflow can survive a restart mid-pipeline.
- GPUManager disappears — GPU allocation is the cluster scheduler's job per spec (`docs/centaur-science.md:27`).

## VLM review (in-loop)

**Where:** `parallel_agent.py:894-1033` (`MinimalAgent._analyze_plots_with_vlm`), called from `_process_node_wrapper` line 1774 after plotting code succeeds.

**Distinct from** the post-writeup VLM at `.scientist/ai_scientist/perform_vlm_review.py` (which reviews the final PDF's figures, not in-loop).

**Contract:**
- Input: `node.plot_paths` (list of absolute paths to `.png`/`.jpg`/`.jpeg` files).
- If >10 plots, first calls `agent.feedback.model` with `plot_selection_spec` to pick the 10 most relevant (`parallel_agent.py:916-980`).
- Encodes each as `data:image/jpeg;base64,...` in an OpenAI chat-completion `image_url` content block (`parallel_agent.py:983-1005`).
- Calls `agent.vlm_feedback.model` (default `gpt-4o-2024-11-20`) with `vlm_feedback_spec` (`parallel_agent.py:103-133`):

```json
{
  "plot_analyses": [{"analysis": "..."}],
  "valid_plots_received": bool,
  "vlm_feedback_summary": "..."
}
```

- Writes back to the node:
  - `node.is_buggy_plots = not valid_plots_received` (this is the gating signal — buggy plots → excluded from `good_nodes`)
  - `node.plot_analyses` = the returned list, with `plot_path` injected per index
  - `node.vlm_feedback_summary` = the returned string
  - Then triggers `_determine_datasets_successfully_tested` (another LLM call to `agent.feedback.model`) to populate `datasets_successfully_tested`.

**For the port as a Centaur tool:** input is N PNG bytes + idea description; output is `{is_valid: bool, per_plot_analyses: [{plot_index, analysis}], summary: str}`. The tool should produce the same gating signal so the controller knows whether to mark the node "good".

## Output artifacts

`save_run` (`.scientist/ai_scientist/treesearch/utils/config.py:219-260`) is called per step via `step_callback`. Produces:

```
logs/<exp>/stage_<stage_name>/
    ├── journal.json                 # serialize.dump_json(journal) — full node graph
    ├── config.yaml                  # OmegaConf dump of effective config
    ├── tree_plot.html               # interactive tree viz (igraph + JS)
    ├── best_solution_<best_id>.py   # best node's code, ONLY (no other artifacts)
    ├── best_node_id.txt             # the bare best id string
    └── notes/
        ├── node_<id>_summary.json   # per-step LLM summary of latest node
        └── stage_progress.json      # rolling stage stats
```

Plus at end of run (`perform_experiments_bfts_with_agentmanager.py:213-256`):

```
logs/<exp>/
    ├── manager.pkl                  # pickled AgentManager (fallback: pickled journals)
    ├── draft_summary.json           # stage 1 summary
    ├── baseline_summary.json        # stage 2 summary
    ├── research_summary.json        # stage 3 summary (list of ablation node logs)
    └── ablation_summary.json        # stage 4 summary
    └── experiment_results/
        └── experiment_<id>_proc_<pid>/    # per-node artifacts (see Code Execution Contract)
            ├── experiment_code.py
            ├── plotting_code.py
            ├── experiment_data.npy
            └── *.png
        └── seed_aggregation_<agg_id>/     # multi-seed aggregation
            ├── aggregation_plotting_code.py
            └── *.png
```

**Journal JSON shape** (`utils/serialize.py:11-31`): `{nodes: [Node.to_dict()...], node2parent: {child_id: parent_id}, __version: "2"}`. Each node serializes via `Node.to_dict` (`journal.py:214-291`) — a flat dict of the fields listed in §"Tree data model".

**The Centaur port must emit at minimum:** `journal.json` (or equivalent durable state), `best_solution_<id>.py`, and the per-node `experiment_results/` directory tree, because all downstream Sakana code (writeup, plot aggregation, citation gathering) is wired to these exact paths. Even though those stages are out of scope, preserving the layout keeps the door open for them.

## Mapping to the Centaur workflow

| Concept (Sakana) | Centaur port |
|---|---|
| `AgentManager` (4-stage curriculum) | **MVP: drop.** Port stage 1 only. The 4-stage curriculum is AI-Scientist-specific, not BFTS. Re-add stages 2–4 later as child workflows if needed. |
| `ParallelAgent` per stage | One Centaur **tree controller workflow** per stage. |
| `Journal` | Workflow's durable state. Serialize per step via `ctx.set_state` or equivalent. |
| `Node` dataclass | Workflow state schema. Preserve the field shape verbatim — downstream code reads it. |
| `MetricValue` (multi-metric, multi-dataset dict) | Preserve the format. Reduction-to-mean policy can be overridden by the Centaur `Scoring` config. |
| `_select_parallel_nodes` | Pure function in the workflow body: `(journal, search_cfg, random_seed) -> List[NodeId | None]`. Deterministic given seed — useful for replays. |
| `step()` synchronous batch | `for node in selected: ctx.step(expand_node, node)` then `ctx.wait_all(...)`. Survives restarts. |
| `_process_node_wrapper` (the LLM-LLM-exec-LLM-exec-LLM pipeline) | One Centaur workflow function with each sub-call as a separate `ctx.step` so it's resumable mid-pipeline. The 3 code-exec sub-steps target the Sandbox tool. |
| `MinimalAgent._draft/_debug/_improve/_generate_*_node` | Branch on `Node.stage_name` and `parent.is_buggy` inside `expand_node`. Each branch is a prompt template + an LLM call — implement as a single `propose_code` step with a strategy parameter. |
| `Interpreter.run(code) -> ExecutionResult` | **Sandbox tool: `exec_python(code: str, timeout_s: int) -> ExecutionResult`.** Returns `{term_out, exec_time, exc_type, exc_info, exc_stack}`. Sandbox must chdir to a persistent workspace before exec. |
| `working/` directory inside `cwd` | Sandbox's persistent storage. Per spec, the Sandbox's PVC survives pod restarts so partial artifacts aren't lost. |
| `experiment_results/experiment_<id>_proc_<pid>/` artifact dir | Centaur-owned object store path keyed by node id. After Sandbox exec, controller copies `working/*.{npy,png}` out. |
| `_analyze_plots_with_vlm` | **Centaur tool: `vlm_analyze_plots(plot_paths, task_desc) -> {is_valid, per_plot_analyses, summary}`**. Per spec (`docs/centaur-science.md:29`), this is wired as a tool call. |
| `Journal.get_best_node` (LLM judge) | Replace with deterministic `argmax` on `MetricValue.get_mean_value()`. Drop the LLM judge. |
| `GPUManager` | Drop. GPU allocation is Kubernetes node-selector + resource-request, not controller logic. |
| `ProcessPoolExecutor` | Drop. Centaur workflow fan-out replaces it. |
| `coolname`, `rich`, `shutup`, in-process logging | Drop. Centaur has its own observability. |
| `manager.pkl` checkpoint | Drop. Workflow state durability is Centaur's job. |
| `aggregate_plots` / `perform_writeup` / `perform_review` | **Out of scope per spec.** Reserve hooks but don't port. |
| `multi_seed_eval` + `plot_aggregation` (post-stage) | Phase 2 work. Maps to N parallel `expand_node` calls with identical code + different seeds, plus an extra aggregation step. Skip for MVP. |
| `bfts_config.yaml` | Becomes the workflow input schema. Flatten the OmegaConf structure to a Centaur input object. |
| Stage-specific prompts (`main_stage_goals`, `_curate_task_desc`) | Move into prompt templates. Hardcode stage 1 first. |
| `Node.exec_time_feedback` (stage-3 too-fast warning) | Stage 3 only — defer. |

## Gotchas

These will quietly break if not handled in the port:

1. **`os.chdir` everywhere.** The agent's generated code assumes `os.path.join(os.getcwd(), 'working')` is its writable dir (prompt at `parallel_agent.py:339-341`). The metric-parse code, plotting code, and seed-eval code all repeat this. Sandbox `exec` must chdir before invoking, and `working/` must already exist. (`interpreter.py:138`)
2. **`AI_SCIENTIST_ROOT` env var** is consumed by the seed-aggregation plotting code at `parallel_agent.py:2268` — `os.getenv("AI_SCIENTIST_ROOT")` is concatenated with experiment-data paths. Set in the Sandbox env at workflow start.
3. **`torch.cuda.is_available()` is hardcoded in the prompt** (`parallel_agent.py:303`). Agent-generated code will always try CUDA first. If the Sandbox image lacks GPU, the agent code's `device = torch.device('cuda' if ...)` will fall back to CPU silently — fine, but exec timeouts will hit hard.
4. **The `psutil` reaper** (`launch_scientist_bfts.py:321-369`) SIGKILLs anything matching `["python", "torch", "mp", "bfts", "experiment"]`. If you run the Sakana code inside an existing Python orchestrator, you will kill your orchestrator. The port avoids this by not having an in-process exec tree.
5. **Symlinks in `data_dir`.** `copy_data: False` symlinks the data dir into `workspace_dir/input` (`utils/config.py:209-216`, `utils/__init__.py:9-37`). Symlinks across container mount points are usually broken — the port must always copy.
6. **The `LLM-as-best-judge` is non-deterministic** and falls back to a different selection algorithm on error (`journal.py:497-502`). Two identical runs can pick different bests. Replace with deterministic argmax in the port.
7. **`MetricValue.__gt__` uses the *first* metric's `lower_is_better`** to decide direction across **all** metrics and datasets (`metric.py:191-203`). A node that returns `[validation_loss↓, val_accuracy↑]` will compare accuracy as if lower were better. Document or fix in the port.
8. **`generate_summary` is called every `step()`** (`parallel_agent.py:2072-2081`). That's a full-tree LLM summarization per scheduling pass. For a 20-step stage, that's 20 extra LLM calls just for prompt context. The port can sample or skip.
9. **`<|EOF|>` marker** in stdout (`interpreter.py:161, 299-301`) — if an agent's print output happens to contain that string, output parsing breaks. Minor footgun.
10. **Pickling parent references.** `Node.__deepcopy__` (`journal.py:128-143`) intentionally drops parent/children to break cycles, and `_safe_pickle_test` (`parallel_agent.py:31-38`) is called before every dispatch. If any new field on `Node` references a non-picklable object (e.g. a Centaur context handle), dispatch silently fails. The port should swap `to_dict`/`from_dict` for a Pydantic/dataclasses-json schema validated at the workflow boundary.
11. **Exec retry is 3 for plotting only** (`parallel_agent.py:1698`); experiment exec is single-shot. If the experiment fails, the *only* recovery is `_debug` on the next scheduling pass.
12. **`_select_parallel_nodes` modifies `processed_trees: set`** while iterating — relies on `id(tree_root)` (object identity). After workflow restart and journal re-hydration, the new `Node` objects have different `id()`s, but that's fine because the set is per-`step()` call (recreated each time). The port should match this lifecycle.
13. **`agent.summary` and `agent.select_node` are optional**. Code paths default to `gpt-4o` and `0.3` hardcoded (`journal.py:471-475, 607-608, 544-545`) if absent. The port should require them or remove those code paths entirely.
14. **Stage 1 can hang.** If no draft ever produces a good node within `stage1_max_iters`, the manager sets `current_stage = None` and exits silently with no best result (`agent_manager.py:419-429`). The Centaur port should surface this as an explicit terminal workflow state.

## Open questions for the master plan

1. **Stage scope for MVP.** Stage 1 only, or all four? Recommendation: Stage 1 only. Stages 2–4 are AI-Scientist-curriculum-specific and add curriculum-management complexity orthogonal to BFTS.
2. **VLM gate semantics.** Today, `is_buggy_plots=True` excludes a node from `good_nodes` and hence from best-first selection — a hard gate. Should the port make this a soft penalty instead?
3. **Best-node selector.** Drop the LLM-as-judge entirely, or keep it behind a flag for parity? Recommendation: drop. Spec already says deterministic.
4. **Metric reduction.** The mean-across-metrics policy is brittle. Should the port expose a Centaur-config-level reducer (e.g. `min`, `weighted_mean`, `lexicographic`)?
5. **Sandbox state lifetime.** A node hibernates per spec — what `working/` content survives? The metric-parse code reads `working/experiment_data.npy`, which must persist between the exec subturn and the parse subturn even though they're separate `ctx.step` calls. Concretely: hold the Sandbox alive across the three exec sub-calls of one expansion, OR persist the npy/png to Centaur storage between sub-calls.
6. **In-tree fan-out vs. cross-tree parallelism.** Sakana's `num_workers=4` runs 4 expansions within a single stage's tree(s) per step. The spec maps `num_drafts` to N child workflows (cross-tree) but doesn't mandate any intra-tree parallelism. Decide whether `num_workers` is preserved as intra-tree fan-out or collapsed to 1.
7. **Memory summary.** The journal-wide LLM summary is regenerated every step. Replace with a rolling buffer, a fixed-size recent-history window, or drop entirely?
8. **Token budget.** Sakana has `ai_scientist.utils.token_tracker` (used at `launch_scientist_bfts.py:35-39`) — should the port surface token costs to Centaur observability?
9. **Determinism / replay.** Sakana's selector uses `random.random()` with no seed plumbed through config. For Centaur replay-after-restart, the port should seed the RNG from durable state.
10. **Per-stage Sandbox templates.** Spec at `docs/centaur-science.md:31` proposes per-role `SandboxTemplate`s (proposer / debugger / reviewer). Sakana has no such distinction — all turns share `agent.code.model`. Defer until there's evidence a role split matters.

## Sources

All citations are `path:line` against `/Users/perhats/Documents/GitHub/centaur-scientist/.scientist/`.

- Entrypoint: `launch_scientist_bfts.py:1-369` (full file).
- Config schema: `ai_scientist/treesearch/utils/config.py:26-110`.
- Config defaults: `bfts_config.yaml:1-87`.
- Config rewriter: `ai_scientist/treesearch/bfts_utils.py:45-76`.
- Outer curriculum loop: `ai_scientist/treesearch/agent_manager.py:692-829`.
- Stage definitions: `ai_scientist/treesearch/agent_manager.py:103-167`.
- Stage 1 completion: `ai_scientist/treesearch/agent_manager.py:434-442`.
- Stage 2 completion: `ai_scientist/treesearch/agent_manager.py:444-498`.
- Stage 3 exec-time policing: `ai_scientist/treesearch/agent_manager.py:511-530`.
- Inner BFTS selector: `ai_scientist/treesearch/parallel_agent.py:1931-2051`.
- Step dispatch: `ai_scientist/treesearch/parallel_agent.py:2053-2191`.
- Process pool init: `ai_scientist/treesearch/parallel_agent.py:1183`.
- GPU manager: `ai_scientist/treesearch/parallel_agent.py:1091-1117`.
- `_process_node_wrapper`: `ai_scientist/treesearch/parallel_agent.py:1409-1796`.
- Branch selection (draft/debug/improve/etc): `ai_scientist/treesearch/parallel_agent.py:1486-1522`.
- `_draft` prompt: `ai_scientist/treesearch/parallel_agent.py:453-492`.
- `_debug` prompt: `ai_scientist/treesearch/parallel_agent.py:494-521`.
- `_improve` prompt: `ai_scientist/treesearch/parallel_agent.py:523-547`.
- Bug judge prompt + spec: `ai_scientist/treesearch/parallel_agent.py:81-101, 683-718`.
- Metric-parse code generation: `ai_scientist/treesearch/parallel_agent.py:1554-1594`.
- Metric extraction spec: `ai_scientist/treesearch/parallel_agent.py:135-202`.
- Metric extraction call: `ai_scientist/treesearch/parallel_agent.py:1619-1646`.
- Plotting code generation: `ai_scientist/treesearch/parallel_agent.py:720-833`.
- Plotting exec: `ai_scientist/treesearch/parallel_agent.py:1668-1714`.
- VLM analysis: `ai_scientist/treesearch/parallel_agent.py:894-1033`.
- VLM feedback spec: `ai_scientist/treesearch/parallel_agent.py:103-133`.
- Datasets-tested judge: `ai_scientist/treesearch/parallel_agent.py:835-892`.
- Multi-seed eval: `ai_scientist/treesearch/parallel_agent.py:1261-1330`.
- Seed-eval aggregation: `ai_scientist/treesearch/parallel_agent.py:1332-1408, 2228-2331`.
- `Node` dataclass: `ai_scientist/treesearch/journal.py:43-291`.
- `Node.stage_name`: `ai_scientist/treesearch/journal.py:158-168`.
- `Node.debug_depth`: `ai_scientist/treesearch/journal.py:202-212`.
- `Journal`: `ai_scientist/treesearch/journal.py:361-613`.
- `Journal.get_best_node`: `ai_scientist/treesearch/journal.py:420-502`.
- `Journal.good_nodes`: `ai_scientist/treesearch/journal.py:389-407`.
- `MetricValue`: `ai_scientist/treesearch/utils/metric.py:112-325`.
- `WorstMetricValue`: `ai_scientist/treesearch/utils/metric.py:327-341`.
- `Interpreter`: `ai_scientist/treesearch/interpreter.py:81-313`.
- `ExecutionResult`: `ai_scientist/treesearch/interpreter.py:26-37`.
- `_run_session` (subprocess child): `ai_scientist/treesearch/interpreter.py:130-161`.
- Timeout / SIGINT handling: `ai_scientist/treesearch/interpreter.py:276-293`.
- `run_save` (output layout): `ai_scientist/treesearch/utils/config.py:219-260`.
- Per-step output: `ai_scientist/treesearch/perform_experiments_bfts_with_agentmanager.py:103-159`.
- Final summary outputs: `ai_scientist/treesearch/perform_experiments_bfts_with_agentmanager.py:213-256`.
- Journal serialization: `ai_scientist/treesearch/utils/serialize.py:11-58`.
- Backend dispatch: `ai_scientist/treesearch/backend/__init__.py:1-77`.
- `compile_prompt_to_md`: `ai_scientist/treesearch/backend/utils.py:44-102`.
- Workspace prep: `ai_scientist/treesearch/utils/config.py:209-216`.
- Per-process workspace creation: `ai_scientist/treesearch/parallel_agent.py:1434-1441`.
- Artifact promotion to logs: `ai_scientist/treesearch/parallel_agent.py:1715-1766`.
- Per-node prompt guideline (data-saving contract): `ai_scientist/treesearch/parallel_agent.py:296-394`.
- `trim_long_string`: `ai_scientist/treesearch/utils/response.py:41-52`.
- `extract_code` / `extract_text_up_to_code`: `ai_scientist/treesearch/utils/response.py:55-83`.
- Post-paper VLM (out of scope for in-loop): `ai_scientist/perform_vlm_review.py:1-483`.
