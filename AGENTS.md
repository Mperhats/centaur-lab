# Centaur Scientist — Developer Guide

## Overview
Centaur Scientist combines [Centaur](https://github.com/paradigmxyz/centaur)
(the durable agent control plane) with
[AI Scientist-v2](https://github.com/SakanaAI/AI-Scientist-v2)
(autonomous scientific research via agentic tree search). The base platforms
are consumed as git submodules; this repo owns only the overlay that wires
them together.

## Quick Start

```bash
git submodule update --init --recursive
brew install just
cp .env.example .env   # fill in required keys
just up
```

See [`README.md`](README.md) for the full setup, smoke test, and
troubleshooting walkthrough.

## Directory Structure

```
centaur-scientist/
├── .centaur/             # git submodule -> paradigmxyz/centaur (base platform, pinned at SHA)
├── .scientist/           # git submodule -> SakanaAI/AI-Scientist-v2 (research pipeline, pinned at SHA)
├── overlay/              # org-specific extensions packaged into centaur-overlay:latest
│   ├── tools/            # auto-discovered tool plugins (mounted into API + sandbox)
│   │   └── semantic_scholar/
│   ├── workflows/        # auto-discovered durable workflows
│   │   ├── save_papers.py
│   │   └── research_brief.py
│   ├── Dockerfile        # overlay image definition
│   └── Justfile          # overlay build/test recipes
├── cloudflared/          # Cloudflare Tunnel routing + launchd agent template
├── db/                   # local DB tooling + notebooks
├── docs/
│   ├── centaur/          # offline mirror of centaur.run reference docs
│   └── superpowers/      # this repo's specs + implementation plans
├── values.local.yaml     # Helm overlay on .centaur/contrib/chart/values.dev.yaml
├── Justfile              # thin wrapper over .centaur/Justfile (owns `up` / `down` / overlay glue)
├── .env.example          # template for shell env vars consumed by `bootstrap-secrets`
└── .gitmodules           # pins .centaur/ and .scientist/ at specific upstream SHAs
```

Each top-level file has one responsibility. The two submodule directories
are never edited from this repo — bumping their pinned SHAs is a deliberate
PR.

## Conventions

- Never edit files inside `.centaur/` or `.scientist/` from this repo.
- Bumping a submodule SHA is a deliberate PR, not a side effect.
- The full project conventions, architecture, and operational guides live
  upstream in [`.centaur/AGENTS.md`](.centaur/AGENTS.md).
