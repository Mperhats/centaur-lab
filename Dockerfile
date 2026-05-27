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
# (dev caches, lockfiles, tests, README-style docs, the ``.centaur``
# submodule, ``cloudflared/``, ``docs/``, ``scripts/`` dev tooling,
# ``tmp/`` scratch, and any local Helm values).
#
# The chart's overlay-bootstrap initContainer copies ``sourcePath``
# (default ``/overlay``) into the API pod at ``/app/overlay/org`` and
# into sandbox pods at ``/home/agent/overlay/org``. Alpine is
# sufficient — the overlay only ships static files; tool and workflow
# handlers are .py modules the API pod discovers via TOOL_DIRS /
# WORKFLOW_DIRS at startup. Tool runtime deps are declared in each
# ``tools/<name>/pyproject.toml`` and installed by the API pod's
# ``entrypoint.sh`` at startup (which scans ``TOOL_DIRS`` for
# ``[project].dependencies`` blocks). The repo-root ``pyproject.toml``
# is a uv workspace whose members are those same per-tool files plus
# ``packages/bfts_sdk`` (the BFTS controller library), so the dev/test
# ``.venv`` resolves the same dep set with no duplicated manifest.
# CI publishes this image to GHCR on pushes to main; the BFTS sandbox
# runtime image is built separately from ``Dockerfile.bfts-executor``
# by the same workflow (see ``.github/workflows/overlay.yml``).
FROM alpine:3.20
WORKDIR /overlay
COPY . /overlay
