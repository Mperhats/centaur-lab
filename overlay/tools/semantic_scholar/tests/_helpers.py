"""Unit-test mocks for the semantic_scholar tool suite.

Currently only :class:`MockContext` is exported here because the tool's
unit tests stub the asyncpg pool inline (no workflow ``_upsert_document``
runs from inside the tool itself). When that changes — e.g. an
integration-only fixture needs the same pool surface as the workflow
tests — copy :class:`MockPool` and :class:`MockAsyncpgConn` over from
``overlay/workflows/tests/_helpers.py`` rather than reaching across
trees; mirroring is the upstream convention (see
``.centaur/services/api/tests/conftest.py`` vs. the per-tool conftests).
"""

from __future__ import annotations

from typing import Any


class MockContext:
    """Minimal workflow context exposing ``_pool`` and a recording ``log``.

    Used by the tool-side integration tests that exercise
    workflow-like flows against a real ``db_pool``.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self.logs: list[tuple[str, dict[str, Any]]] = []

    def log(self, event: str, **kwargs: Any) -> None:
        self.logs.append((event, kwargs))
