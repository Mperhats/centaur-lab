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
├── centaur_lab/                     # shared persistence/metrics helpers
├── centaur_sdk -> .centaur/centaur_sdk  # dev-only symlink (see "Conventions" below)
├── pyproject.toml + uv.lock         # single-root uv project (aggregated tool + workflow deps)
├── ruff.toml                        # lint + banned-api rules (no os.getenv, no requests)
├── services/
│   ├── api/db/migrations/           # overlay-owned dbmate migrations (bfts_runs, bfts_nodes, …)
│   └── sandbox/SYSTEM_PROMPT.md     # overlay sandbox prompt (when present)
├── tests/                           # ACME-style root pytest smoke suite
├── tools/
│   ├── bfts_executor/               # Drives agent-sandbox Sandbox CRs for BFTS experiment exec
│   ├── bfts_vlm/                    # VLM plot review for BFTS analysis nodes
│   └── semantic_scholar/            # S2 Graph API client + research-brief renderer
└── workflows/                       # auto-discovered durable handlers
    ├── _bfts_*.py                   # BFTS internals (leading `_` skipped by loader)
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
- Per-tool/per-workflow test files use absolute imports
  (`from workflows.tests._mocks import ...`) rather than relative
  (`from ._mocks import ...`) so pytest's `--import-mode=importlib` does
  not collide the leaf `tests` package across sibling test trees.
- `centaur_lab/` is the shared overlay helper package (renderer +
  persistence + metrics). Named `centaur_lab` (not `shared`) because
  upstream reserves the `shared.*` namespace for its tools runtime.
- `centaur_sdk` resolves through a tracked symlink at the repo root
  pointing into `.centaur/centaur_sdk`. Upstream's wheel-packaging
  declares `[tool.hatch.build.targets.wheel] packages = ["."]` which
  flattens module files into the wheel root and breaks `pip install`;
  the symlink works because the repo root is already on `sys.path` for
  every entrypoint (pytest, `uv run python -m ...`, IDEs). The overlay
  image excludes the symlink — the API pod ships its own
  `/app/centaur_sdk/`.
- Per-tool `pyproject.toml` files were dropped to match the centaur-acme
  layout: the root `pyproject.toml` is the single source of truth for
  dev/test deps. Note that the upstream `tool_manager` discovers tools
  by scanning each `tools/<name>/pyproject.toml` for a `[tool.centaur]`
  block, so the overlay registers zero tools at runtime until per-tool
  pyprojects are restored (minimal `[tool.centaur]` blocks only) or
  upstream lands root-aggregated discovery. `bfts_vlm` will specifically
  need its `optional_secrets` block back so the iron-proxy Anthropic /
  OpenAI binding still works.
- Secrets resolve via `from centaur_sdk import secret; secret("KEY")` —
  `os.getenv` is banned for API keys (lint-enforced in `ruff.toml`). The
  one exception is non-secret `BFTS_*` config flags consumed by
  `workflows/_bfts_config.py`, which are still ruff-flagged today and
  ride this branch as known pre-existing lint debt.
- The full project conventions, architecture, and operational guides live
  upstream in [`.centaur/AGENTS.md`](.centaur/AGENTS.md).

## Local checks

```bash
uv sync
uv run pytest --ignore=workflows/tests/integration --ignore=tools/semantic_scholar/tests/integration
uv run ruff check .
docker build -t centaur-scientist-overlay:dev .
```
