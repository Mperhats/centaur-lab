# Centaur overlay image. The chart's overlay-bootstrap initContainer copies
# `sourcePath` (default /overlay) into the API pod at /app/overlay/org and
# into sandbox pods at /home/agent/overlay/org. Alpine is sufficient — the
# overlay only needs to ship static files; tool dependencies are installed
# from each tool's pyproject.toml at API discovery time. Workflow handlers
# are similarly static .py files; the API discovers them via WORKFLOW_DIRS
# at startup. CI publishes this image to GHCR on pushes to main (see
# `.github/workflows/overlay.yml`).
#
# `centaur_lab/` ships persistence helpers (`centaur_lab.paper_document`,
# `centaur_lab.metrics`) imported by both the semantic_scholar tool and the
# save_papers / research_brief workflows. The API pod adds /app/overlay/org
# to sys.path so `centaur_lab`, `tools`, and `workflows` are sibling
# top-level packages — drop this COPY and the imports fail at module load.
# The package is named `centaur_lab` (not `shared`) because upstream's
# tool loader reserves the `shared.*` namespace for its tools runtime
# (see .centaur/services/api/api/tool_manager.py:1686-1693).
FROM alpine:3.20
WORKDIR /overlay
COPY tools /overlay/tools
COPY workflows /overlay/workflows
COPY centaur_lab /overlay/centaur_lab
COPY .agents /overlay/.agents
COPY services /overlay/services
