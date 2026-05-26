# Centaur overlay image. The chart's overlay-bootstrap initContainer copies
# `sourcePath` (default /overlay) into the API pod at /app/overlay/org and
# into sandbox pods at /home/agent/overlay/org. Alpine is sufficient — the
# overlay only needs to ship static files; tool dependencies are installed
# from each tool's pyproject.toml at API discovery time. Workflow handlers
# are similarly static .py files; the API discovers them via WORKFLOW_DIRS
# at startup.
#
# `services/` ships overlay-owned dbmate migrations. The API pod's
# `run_migrations` resolves the dir as
# ``$CENTAUR_OVERLAY_DIR/services/api/db/migrations`` (see
# .centaur/services/api/api/db.py:14-17) and tracks applied versions in
# ``schema_migrations_overlay`` — separate from the core
# ``schema_migrations`` table — so overlay migrations are versioned
# independently from upstream. Without this COPY the migrations dir is
# missing on the pod, ``dbmate up`` silently no-ops for the overlay
# set, and any workflow touching an overlay-only table (e.g.
# ``paper_archives``) fails at runtime with
# ``relation "..." does not exist``.
#
# CI publishes this image to GHCR on pushes to main (see
# `.github/workflows/overlay.yml`).
FROM alpine:3.20
WORKDIR /overlay
COPY tools /overlay/tools
COPY workflows /overlay/workflows
COPY .agents /overlay/.agents
COPY services /overlay/services
