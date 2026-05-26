# Centaur overlay image. The chart's overlay-bootstrap initContainer copies
# `sourcePath` (default /overlay) into the API pod at /app/overlay/org and
# into sandbox pods at /home/agent/overlay/org. Alpine is sufficient — the
# overlay only needs to ship static files; tool dependencies are installed
# from each tool's pyproject.toml at API discovery time. Workflow handlers
# are similarly static .py files; the API discovers them via WORKFLOW_DIRS
# at startup. CI publishes this image to GHCR on pushes to main (see
# `.github/workflows/overlay.yml`).
FROM alpine:3.20
WORKDIR /overlay
COPY tools /overlay/tools
COPY workflows /overlay/workflows
COPY .agents /overlay/.agents
