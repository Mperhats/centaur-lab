# Centaur overlay image.
#
# Mirrors the canonical overlay Dockerfile from upstream
# (.centaur/docs/public/md/extend/overlay.md:80-84) — a single
# ``COPY . /overlay`` so every overlay-extensible upstream surface
# (``tools/``, ``workflows/``, ``.agents/``, ``services/api/db/migrations``,
# ``services/sandbox/SYSTEM_PROMPT.md``, future ``services/<name>/...``
# bolt-ons) ships automatically. Per-directory ``COPY`` lines are an
# anti-pattern: every time upstream adds a new overlay-extensible
# surface our image silently drops it.
#
# Build context exclusions live in ``.dockerignore`` — that file is the
# single point of truth for "what does the overlay image NOT ship"
# (dev caches, lockfiles, tests, README-style docs, the ``.centaur`` and
# ``.scientist`` submodules, ``cloudflared/``, ``docs/``, ``tmp/``
# scratch, and any local Helm values).
#
# The chart's overlay-bootstrap initContainer copies ``sourcePath``
# (default ``/overlay``) into the API pod at ``/app/overlay/org`` and
# into sandbox pods at ``/home/agent/overlay/org``. Alpine is
# sufficient — the overlay only ships static files; tool and workflow
# handlers are .py modules the API pod discovers via TOOL_DIRS /
# WORKFLOW_DIRS at startup. Each ``tools/<name>/pyproject.toml`` carries
# the ``[tool.centaur]`` block the upstream API pod's ``tool_manager``
# reads to register the tool and bind iron-proxy secret headers; the
# root ``pyproject.toml`` only aggregates dev/test deps for the shared
# ``uv`` venv (see ``AGENTS.md`` "Conventions").
FROM alpine:3.20
WORKDIR /overlay
COPY . /overlay
