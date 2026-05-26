"""Smoke test that the overlay packages import.

Tests are migrated from ``tests-old/`` one at a time. This file is the
ACME-style entrypoint: keep it small, don't grow it past a few minutes
of runtime, and let new test files land alongside it as we re-author
each suite from ``tests-old/``.
"""

from __future__ import annotations

import centaur_sdk
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


def test_centaur_sdk_resolves_via_root_symlink() -> None:
    """``centaur_sdk`` is exposed via a repo-root symlink to ``.centaur/centaur_sdk``.

    Upstream's wheel build flattens ``centaur_sdk`` into the wheel root,
    so a normal ``pip install`` cannot expose ``from centaur_sdk import …``.
    The repo-root symlink sidesteps that by giving Python a real
    ``centaur_sdk/__init__.py`` at a location already on every entrypoint's
    ``sys.path`` (pytest, ``uv run python -m …``, IDEs). Bumping the
    ``.centaur`` submodule pin updates this SDK in lockstep with the API.
    """
    from centaur_sdk import Table, secret

    assert callable(secret)
    assert Table is not None
    assert centaur_sdk.__file__.endswith("centaur_sdk/__init__.py")
