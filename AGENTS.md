# Centaur Scientist — Developer Guide

## Overview
Centaur Scientist combines [Centaur](https://github.com/paradigmxyz/centaur)
(the durable agent control plane) with
[AI Scientist-v2](https://github.com/SakanaAI/AI-Scientist-v2)
(autonomous scientific research via agentic tree search). The base platforms
are consumed as git submodules; this repo owns only the overlay that wires
them together.

The overlay shape mirrors
[`paradigmxyz/centaur-acme`](https://github.com/paradigmxyz/centaur-acme):
a flat root layout (`tools/`, `workflows/`, `services/`, `.agents/`) packaged
into a single `centaur-overlay` image via `COPY . /overlay`.

## Quick Start

```bash
git submodule update --init --recursive
uv sync
uv run pytest tests/
```

The local-stack `just up` recipe lives in upstream `.centaur/Justfile` —
see [`README.md`](README.md) for the full setup walkthrough.

## Directory Structure

```
centaur-scientist/
├── .agents/skills/                  # sandbox skills loaded with the overlay
│   └── academic-research/
├── .centaur/                        # git submodule -> paradigmxyz/centaur (base platform, pinned at SHA)
├── .scientist/                      # git submodule -> SakanaAI/AI-Scientist-v2 (BFTS reference impl, pinned at SHA)
├── Dockerfile                       # `COPY . /overlay` alpine image
├── Dockerfile.bfts-executor         # python:3.11-slim image used by BFTS sandbox pods
├── .dockerignore                    # what the overlay image does NOT ship
├── packages/                        # in-tree Python packages (overlay-owned)
│   ├── bfts_sdk/                    # BFTS controller internals shared by bfts_* workflows
│   │   ├── pyproject.toml           #   uv workspace member (name = "bfts-sdk"); declares runtime deps
│   │   ├── config.py                #   Input → hyperparams → BFTS_* env → defaults resolver
│   │   ├── state.py                 #   asyncpg DAO for bfts_runs / bfts_nodes / hyperparams
│   │   ├── expand.py + select.py    #   per-node LLM pipeline + UCB-1 selector
│   │   ├── llm.py + prompts.py      #   OpenAI/Anthropic clients + prompt builders
│   │   ├── metric.py + export.py    #   Sakana metric reducer + best/dot/run artifacts
│   │   └── hyperparams.py           #   reflection-tuned policy round-trip
│   └── centaur_sdk -> ../.centaur/centaur_sdk  # dev-only symlink (see "Conventions");
│                                    #   NOT a workspace member
├── pyproject.toml + uv.lock         # root coordinator: declares `[tool.uv.workspace] members = ["tools/*", "packages/bfts_sdk"]`
├── ruff.toml                        # lint + banned-api rules (no os.getenv, no requests)
├── services/
│   ├── api/db/migrations/           # overlay-owned dbmate migrations (bfts_runs, bfts_nodes, …)
│   └── sandbox/SYSTEM_PROMPT.md     # overlay sandbox prompt (when present)
├── tools/
│   ├── bfts_executor/               #   Drives agent-sandbox Sandbox CRs (pyproject + client.py)
│   ├── bfts_runner/                 #   Enqueue ``bfts_research`` from Slack sandboxes (client.py)
│   │   └── slack/
│   │       ├── post.py              #     thread delivery, plain posts, failure notices
│   │       ├── format.py            #     brief/idea/progress markdown formatters
│   │       └── stream.py            #     agent-session BFTS progress streaming
│   ├── bfts_vlm/                    #   VLM plot review (pyproject declares optional A/OAI keys)
│   └── semantic_scholar/            #   S2 Graph API client + research-brief renderer
│       ├── client.py + cli.py       #     public tool surface
│       ├── utils.py                 #     canonical_json + content_hash (pure helpers)
│       └── projections/             #     pure doc-row builders for company_context_documents
│           ├── paper.py             #       single-paper → row
│           └── brief.py             #       paper-list → brief-row + markdown render
└── workflows/                       # auto-discovered durable handlers
    ├── bfts_research.py             # Slack: plain brief+idea posts, BFTS-only stream
    ├── bfts_root.py                 # entry workflow, takes an `idea` dict
    ├── bfts_tree.py                 # tree-driver dispatcher
    ├── bfts_expand_one.py           # single-node expansion handler
    ├── bfts_reflection_nightly.py   # cron-tuned hyperparam reflection
    ├── ideation.py                  # S2-grounded research-idea generator
    ├── gather_citations.py          # post-run references.bib synth
    ├── research_brief.py            # search S2 → lit-review brief
    └── save_papers.py               # idempotent S2 metadata upsert
```

Each top-level file has one responsibility. The two submodule directories
are never edited from this repo — bumping their pinned SHAs is a deliberate
PR.

## Conventions

- Never edit files inside `.centaur/` or `.scientist/` from this repo.
- Bumping a submodule SHA is a deliberate PR, not a side effect.
- `tools/` and `workflows/` are implicit namespace packages (no
  `__init__.py` at the directory root) so the API pod can merge them with
  the upstream `/app/tools` and `/app/workflows` package roots at runtime.
  `packages/bfts_sdk/` is a regular package (`__init__.py` is present)
  because it is overlay-owned and never merged with an upstream
  namespace. `packages/` itself has no `__init__.py` — it's an implicit
  namespace package, which lets us add a sibling like
  `packages/centaur_sdk` (a dev-only symlink) without polluting the
  `packages.bfts_sdk.…` import path.
- Per-tool/per-workflow test files use absolute imports
  (`from workflows.tests._mocks import ...`) rather than relative
  (`from ._mocks import ...`) so pytest's `--import-mode=importlib` does
  not collide the leaf `tests` package across sibling test trees.
- BFTS controller internals live in `packages/bfts_sdk/` and are
  imported as `from packages.bfts_sdk.config import …`. The repo root
  is on `sys.path` for pytest (via pyproject `pythonpath = [".", "packages"]`),
  `uv run python -m …`, IDE tooling, and the API pod's runtime (the
  upstream `tool_manager` puts the overlay root — the parent of
  `TOOL_DIRS` — on `sys.path` at startup), so the `packages.bfts_sdk.X`
  prefix resolves identically across every entrypoint with no
  `sys.path.insert` shims and no per-workflow path bootstraps. The
  `bfts_sdk` name is intentionally namespaced under `packages.` rather
  than installed as a top-level module because the API pod's runtime
  only places the overlay root on `sys.path` — not arbitrary
  subdirectories — so a top-level `from bfts_sdk import …` would
  require either a deploy-time `PYTHONPATH` override or a per-workflow
  `sys.path.insert`. Both are brittle; the namespaced import is
  configuration-free.
- Document persistence follows the upstream `company_context_documents`
  pattern: pure projections live in `tools/semantic_scholar/projections/`
  (`paper.py`, `brief.py`), pure hash/JSON helpers in
  `tools/semantic_scholar/utils.py`, and the `_upsert_document` SQL +
  `vm_metrics` `try/except ImportError` shim are **inlined verbatim**
  per consumer (`tools/semantic_scholar/client.py` for the
  `research_brief` tool method, `workflows/save_papers.py` for the
  durable handler). The duplication is the upstream convention —
  upstream's own `company_context_documents.py` repeats the same SQL
  rather than sharing through a sibling module — and it keeps the
  per-consumer parent-linkage / retry semantics local to the consumer.
- Dependency management uses a **uv workspace in Shape B**: the
  root `pyproject.toml` only declares
  `[tool.uv.workspace] members = ["tools/*", "packages/bfts_sdk"]`.
  It does NOT re-list members in a root `[project].dependencies`
  block, and it does NOT carry a `[tool.uv.sources]` table — the
  workspace glob is the single source of truth for membership, and
  each member's own `pyproject.toml` is the single source of truth
  for what that member needs at runtime. CI runs
  `uv sync --all-packages --python 3.11`, which walks every member's
  `[project].dependencies` into the root `.venv`. Each member sets
  `[tool.uv] package = false` so uv contributes deps without trying
  to wheel-build the member itself; consumers continue to import
  from the source tree (`tools.<n>.client`,
  `packages.bfts_sdk.X`) via pytest's `pythonpath` and the API
  pod's `TOOL_DIRS` lookup. Distribution names use dashes
  (`bfts-sdk`, `semantic-scholar`); module directory names keep
  their underscore form. Adding a new tool: drop
  `tools/<n>/pyproject.toml` with `name = "<n>"` and the
  `[tool.centaur]` block — that's it; the `tools/*` workspace glob
  picks it up automatically. `packages/centaur_sdk` is explicitly NOT
  a workspace member because upstream's pyproject there is not opted
  out of building and would confuse uv; the symlink exists only for
  dev-time import resolution.
- Each `tools/<n>/` directory has its own `pyproject.toml` with a
  `[tool.centaur]` block. This is how the upstream `tool_manager`
  discovers tools (`.centaur/services/api/api/tool_manager.py:1375-1484`)
  and binds iron-proxy headers — without it the API pod registers
  zero tools at runtime. The same pyproject doubles as the workspace
  member manifest described above. Tool runtime name = **directory
  name** (not `[project].name`); the `[project].name` is only used
  for uv workspace dist resolution. `_client()` factory in the
  declared `module` (defaults to `client.py`) returns an instance
  whose public methods are auto-registered as agent-callable; methods
  starting with `_`, `@property` descriptors, and the lifecycle names
  `close` / `connect` / `disconnect` / `shutdown` are filtered out.
  Forbidden argument names (per `tool_manager._FORBIDDEN_TOOL_ARGUMENT_NAMES`):
  `output_path`, `output_dir`, `download_path`, `save_path`,
  `dest_path`, `destination_path`.
- **The API pod only installs deps declared in `tools/*/pyproject.toml`.**
  `entrypoint.sh` globs `${TOOL_DIRS}/**/pyproject.toml` and
  `uv pip install`s the union of every `[project].dependencies`.
  It does NOT scan `packages/` or `workflows/`. So the invariant
  that keeps prod working is: *every Python import in overlay code
  outside `tools/` must have its top-level distribution declared by
  some tool's `[project].dependencies`*. Today
  `packages/bfts_sdk/` only imports `asyncpg` + `httpx`, both
  declared by `tools/semantic_scholar/` (and `httpx` also by
  `tools/bfts_vlm/`), so the invariant holds. When you add a new
  third-party import to `packages/bfts_sdk/*.py` or to a
  `workflows/*.py` handler, declare it in the
  `tools/<n>/pyproject.toml` whose runtime use-case is closest
  (usually `bfts_executor` for BFTS internals or `semantic_scholar`
  for data-layer pieces). This is a code-review invariant — there
  is no automated CI check, because a string-equality check between
  two locations would only catch literal-mirror drift and would mask
  the real failure mode (a new transitive import landing with no
  tool home).
- `centaur_sdk` resolves through a tracked symlink at
  `packages/centaur_sdk` pointing into `.centaur/centaur_sdk`.
  Upstream's wheel-packaging declares
  `[tool.hatch.build.targets.wheel] packages = ["."]` which flattens
  module files into the wheel root and breaks `pip install`; the
  symlink works because `packages/` is on `sys.path` for every
  dev-side entrypoint (pytest, `uv run python -m ...`, IDEs) via
  pyproject's `pythonpath = [".", "packages"]`. Unlike `bfts_sdk`,
  `centaur_sdk` is imported as the **bare** top-level name
  (`from centaur_sdk import secret`) — the API pod has its own
  `/app/centaur_sdk/` installed as a real venv package in the API
  image's Dockerfile, so the bare name resolves identically in prod
  without needing the overlay symlink. The overlay image excludes
  `packages/centaur_sdk` (see `.dockerignore`).
- Secrets resolve via `from centaur_sdk import secret; secret("KEY")` —
  `os.getenv` is banned for API keys (lint-enforced in `ruff.toml`).
  Non-secret `BFTS_*` operator knobs (model names, debug-prob caps,
  etc.) go through `packages.bfts_sdk.config._env_knob`, the single
  annotated wrapper that documents the suppression in one place rather
  than scattering `# noqa: TID251` across the call sites.
- Only **integration** tests live in-tree right now
  (`tools/semantic_scholar/tests/integration/`,
  `workflows/tests/integration/`). The previous unit-test suite was
  parked under the gitignored `tmp/test-old/` mirror during the
  `centaur_lab` → S2 dispersal because the projections + workflow
  handlers were rewritten; unit coverage gets rebuilt incrementally
  against the new `projections/` + inlined-persistence shape rather
  than ported file-by-file from a structure that no longer matches.
- The full project conventions, architecture, and operational guides live
  upstream in [`.centaur/AGENTS.md`](.centaur/AGENTS.md).

## Local checks

```bash
uv sync
uv run pytest --ignore=workflows/tests/integration --ignore=tools/semantic_scholar/tests/integration
uv run ruff check .
docker build -t centaur-scientist-overlay:dev .
```
