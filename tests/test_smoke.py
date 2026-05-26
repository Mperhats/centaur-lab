"""Smoke test that the overlay packages import.

Tests are migrated from ``tests-old/`` one at a time. This file is the
ACME-style entrypoint: keep it small, don't grow it past a few minutes
of runtime, and let new test files land alongside it as we re-author
each suite from ``tests-old/``.
"""

from __future__ import annotations

import workflows
from tools import pdf, semantic_scholar


def test_overlay_root_imports() -> None:
    assert pdf is not None
    assert semantic_scholar is not None
    assert workflows is not None


def test_workflow_modules_import() -> None:
    from workflows import (
        archive_papers,
        research_brief,
        save_papers,
        search_and_archive_papers,
    )

    assert all(
        hasattr(mod, "WORKFLOW_NAME")
        for mod in (archive_papers, research_brief, save_papers, search_and_archive_papers)
    )


def test_centaur_sdk_resolves_via_submodule_path() -> None:
    """``centaur_sdk`` resolves through ``.centaur`` on the pytest pythonpath.

    Upstream's wheel-build config flattens ``centaur_sdk`` into the wheel
    root, so a normal ``pip install`` does not expose the package —
    pytest's ``pythonpath = [".", ".centaur"]`` mirrors the API pod's
    cwd-based discovery instead. Bumping the ``.centaur`` submodule pin
    updates this SDK in lockstep with the API runtime.
    """
    from centaur_sdk.tool_sdk import secret

    assert callable(secret)
