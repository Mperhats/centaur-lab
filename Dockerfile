# Centaur overlay image.
#
# Mirrors the canonical overlay Dockerfile from upstream
# (.centaur/docs/public/md/extend/overlay.md:80-84) — a single
# ``COPY . /overlay`` so every overlay-extensible upstream surface
# (``tools/``, ``workflows/``, ``.agents/``, ``services/api/db/migrations``,
# ``services/sandbox/SYSTEM_PROMPT.md``, future ``services/<name>/...``
# bolt-ons) ships automatically. Per-directory ``COPY`` lines are an
# anti-pattern: every time upstream adds a new overlay-extensible
# surface our image silently drops it, which is exactly how
# ``services/api/db/migrations`` went missing and broke
# ``paper_archives`` in the cluster (see docs/overlay-db-migrations.md).
#
# Build context exclusions live in ``.dockerignore`` — that file is the
# single point of truth for "what does the overlay image NOT ship"
# (dev caches, lockfiles, tests, Justfile, README-style docs, the
# ``.centaur`` / ``.scientist`` submodules, ``db/``, ``cloudflared/``,
# ``docs/``, ``clusters/`` GitOps configs, and Helm values).
#
# The chart's overlay-bootstrap initContainer copies ``sourcePath``
# (default ``/overlay``) into the API pod at ``/app/overlay/org`` and
# into sandbox pods at ``/home/agent/overlay/org``. Alpine is
# sufficient — the overlay only ships static files; tool dependencies
# are installed from each tool's pyproject.toml at API discovery time,
# and workflow handlers are similarly static .py files (API discovers
# them via WORKFLOW_DIRS at startup). CI publishes this image to GHCR
# on pushes to main (see ``.github/workflows/overlay.yml``).
FROM alpine:3.20
WORKDIR /overlay
COPY . /overlay
