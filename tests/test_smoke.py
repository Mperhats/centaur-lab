"""Smoke test that the overlay packages import.

ACME-style entrypoint for the centaur-scientist overlay. Keep this
file small and let new test files land alongside it as we grow the
surface. The exhaustive per-handler and per-tool suites live under
``workflows/tests/`` and ``tools/<name>/tests/`` and are discovered
by the same ``uv run pytest`` invocation via the root
``pyproject.toml``'s ``testpaths``.
"""

from __future__ import annotations

import centaur_lab
import workflows
from tools import bfts_executor, bfts_vlm, semantic_scholar


def test_overlay_root_imports() -> None:
    assert bfts_executor is not None
    assert bfts_vlm is not None
    assert semantic_scholar is not None
    assert centaur_lab is not None
    assert workflows is not None


def test_tool_clients_import() -> None:
    from tools.bfts_executor.client import BFTSExecutor
    from tools.bfts_vlm.client import VLMReviewer
    from tools.semantic_scholar.client import (
        BIBTEX_PAPER_FIELDS,
        SemanticScholarClient,
    )

    assert BFTSExecutor is not None
    assert VLMReviewer is not None
    assert SemanticScholarClient is not None
    assert isinstance(BIBTEX_PAPER_FIELDS, str)


def test_workflow_modules_import() -> None:
    # Each overlay workflow module declares ``WORKFLOW_NAME`` so the API
    # pod's workflow loader (see ``.centaur/services/api/api/app.py``)
    # can register it. Asserting on this here catches accidental
    # renames of the contract during refactors.
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

    handlers = (
        bfts_expand_one,
        bfts_reflection_nightly,
        bfts_root,
        bfts_tree,
        gather_citations,
        ideation,
        research_brief,
        save_papers,
    )
    assert all(hasattr(mod, "WORKFLOW_NAME") for mod in handlers)


def test_centaur_lab_helpers_import() -> None:
    from centaur_lab.brief import persist_research_brief_from_papers
    from centaur_lab.metrics import observe_document_size, record_document_change
    from centaur_lab.paper_document import build_paper_document, upsert_document

    assert callable(persist_research_brief_from_papers)
    assert callable(observe_document_size)
    assert callable(record_document_change)
    assert callable(build_paper_document)
    assert callable(upsert_document)


def test_centaur_sdk_resolves_via_symlink() -> None:
    """``centaur_sdk`` resolves through the repo-root ``centaur_sdk``
    symlink (→ ``.centaur/centaur_sdk``).

    Upstream's ``centaur_sdk/pyproject.toml`` declares ``packages = ["."]``
    which flattens module files into the wheel root, so a normal
    ``pip install`` / ``uv add`` does not expose ``from centaur_sdk
    import …``. The repo root is already on ``sys.path`` for pytest,
    ``uv run python -m …`` and IDE tooling, so a tracked symlink at
    the repo root makes the package importable without any pythonpath
    manipulation. Bumping the ``.centaur`` submodule pin updates this
    SDK in lockstep with the API runtime.
    """
    from pathlib import Path

    import centaur_sdk
    from centaur_sdk.tool_sdk import secret

    assert callable(secret)
    sdk_path = Path(centaur_sdk.__file__).resolve()
    assert sdk_path.parent.name == "centaur_sdk"
    # ``resolve()`` collapses the symlink, so the SDK should physically
    # live under the pinned upstream submodule.
    assert ".centaur" in sdk_path.parts
