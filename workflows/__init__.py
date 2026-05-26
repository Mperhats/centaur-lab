"""Durable workflow handlers for the centaur-lab overlay.

Dep contract (enforced by ``tests/test_overlay_invariants.py``): any
third-party import in a workflow module must resolve from either the
API base image (``.centaur/services/api/pyproject.toml``) or some
``tools/<name>/pyproject.toml`` ``[project].dependencies`` block.

The API pod's ``entrypoint.sh`` only ``uv pip install``s deps from
``TOOL_DIRS`` pyprojects; ``workflows/`` is not on that path. Upstream
unions every tool's deps before launching uvicorn, so a workflow can
ride along on any tool's ``[project].dependencies`` regardless of
which tool actually uses the import. If you reach for a library that
none of the tools currently need, add it to whichever tool is the
closest semantic neighbor (or to ``tools/pdf`` as a neutral catch-all).
"""
