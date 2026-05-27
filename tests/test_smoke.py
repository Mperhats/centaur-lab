"""Smoke test that the overlay packages import.

ACME-style entrypoint — keep it small, don't grow it past a few minutes
of runtime, and let new test files land alongside it as the suite grows.
"""

from __future__ import annotations

import centaur_sdk
import workflows
from tools import bfts_executor, bfts_vlm, semantic_scholar


def test_overlay_root_imports() -> None:
    assert bfts_executor is not None
    assert bfts_vlm is not None
    assert semantic_scholar is not None
    assert workflows is not None


def test_workflow_modules_import() -> None:
    from workflows import (
        bfts_expand_one,
        bfts_reflection_nightly,
        bfts_root,
        bfts_tree,
        gather_citations,
        ideation,
        research_brief,
        save_papers,
    )

    assert all(
        hasattr(mod, "WORKFLOW_NAME")
        for mod in (
            bfts_expand_one,
            bfts_reflection_nightly,
            bfts_root,
            bfts_tree,
            gather_citations,
            ideation,
            research_brief,
            save_papers,
        )
    )


def test_bfts_sdk_imports() -> None:
    """``packages/bfts_sdk`` is a workspace member; smoke that its public
    surface resolves through the `packages` pythonpath entry."""
    from packages.bfts_sdk import config, expand, llm, metric, prompts, select, state

    assert all(
        m is not None
        for m in (config, expand, llm, metric, prompts, select, state)
    )


def test_centaur_sdk_resolves_via_packages_symlink() -> None:
    """``centaur_sdk`` is exposed via ``packages/centaur_sdk`` symlink to
    ``.centaur/centaur_sdk``.

    Upstream's wheel build flattens ``centaur_sdk`` into the wheel root,
    so a normal ``pip install`` cannot expose ``from centaur_sdk import …``.
    The symlink sidesteps that by giving Python a real
    ``centaur_sdk/__init__.py`` at a location already on every entrypoint's
    ``sys.path`` (pytest, ``uv run python -m …``, IDEs) via the
    ``packages/`` entry in ``pyproject.toml`` ``pythonpath``. Bumping the
    ``.centaur`` submodule pin updates this SDK in lockstep with the API.
    """
    from centaur_sdk import Table, secret

    assert callable(secret)
    assert Table is not None
    assert centaur_sdk.__file__.endswith("centaur_sdk/__init__.py")
