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
│   ├── bfts_vlm/                    #   VLM plot review (pyproject declares optional A/OAI keys)
│   └── semantic_scholar/            #   S2 Graph API client + research-brief renderer
│       ├── client.py + cli.py       #     public tool surface
│       ├── utils.py                 #     canonical_json + content_hash (pure helpers)
│       └── projections/             #     pure doc-row builders for company_context_documents
│           ├── paper.py             #       single-paper → row
│           └── brief.py             #       paper-list → brief-row + markdown render
└── workflows/                       # auto-discovered durable handlers
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
- Dependency management uses a **uv workspace**: the root
  `pyproject.toml` declares `[tool.uv.workspace] members = ["tools/*", "packages/bfts_sdk"]`
  and lists each member dist-name in `[project].dependencies`
  (`bfts-sdk`, `bfts-executor`, `bfts-vlm`, `semantic-scholar`); each
  member name is pinned to `{ workspace = true }` under
  `[tool.uv.sources]`. A single `uv sync` from the repo root walks
  every member's `[project].dependencies` and installs them into the
  root `.venv` — no hand-curated transitive dep lists at the root,
  no duplication, and the dev/test venv matches what the API pod's
  `entrypoint.sh` installs at startup (which scans every
  `tools/*/pyproject.toml` independently). Each member sets
  `[tool.uv] package = false` so uv contributes the `[project].dependencies`
  block to the root venv **without** trying to wheel-build the
  member itself; consumers continue to import from the source tree
  (`tools.<name>.client`, `packages.bfts_sdk.X`) via pytest's
  `pythonpath` and the API pod's `TOOL_DIRS` lookup. Distribution
  names use dashes (`bfts-sdk`, `semantic-scholar`); module
  directory names keep their underscore form (`packages/bfts_sdk/`,
  `tools/semantic_scholar/`). Adding a new tool: drop a new
  `tools/<name>/pyproject.toml` with `name = "<name>"`, add `"<name>"`
  to the root `[project].dependencies`, and add the matching
  `[tool.uv.sources]` entry. `packages/centaur_sdk` is explicitly NOT
  a workspace member — upstream's pyproject there is not opted out of
  building and would confuse uv; the symlink exists only for dev-time
  import resolution (see below).
- Each `tools/<name>/` directory has its own `pyproject.toml` with a
  `[tool.centaur]` block. This is how the upstream `tool_manager`
  discovers tools and binds iron-proxy headers — without it the API
  pod registers zero tools at runtime. The same pyproject doubles as
  the workspace member manifest described above.
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
