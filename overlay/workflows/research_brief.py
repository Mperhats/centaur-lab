"""Workflow: thin wrapper delegating to ``SemanticScholarClient.research_brief``.

The actual S2-search → render → upsert pipeline now lives on
``SemanticScholarClient.research_brief`` in
``overlay/tools/semantic_scholar/client.py``. This workflow handler
exists only to satisfy ``call workflow run`` callers (Justfile smoke
recipes, external posters to ``/workflows/runs``); it delegates to the
tool method and translates the tool's ``{"status": "error"}`` envelope
back to the workflow's pre-existing ``{"status": "skipped"}`` contract
for ``empty_query`` and ``invalid_limit`` — the two soft-skip cases
that workflow callers already depend on. All other tool returns
(success and other errors) pass through unchanged.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from tools.semantic_scholar.client import SemanticScholarClient

WORKFLOW_NAME = "research_brief"


@dataclass
class Input:
    """Runtime options for the ``research_brief`` workflow."""

    query: str
    limit: int = 5
    year_from: int | None = None


# The tool method returns ``{"status": "error", "error": <message>}`` for these
# two input-validation cases. The pre-T3 workflow returned a ``"skipped"``
# envelope with a distinct ``reason`` instead; preserve that shape so external
# callers (Justfile smoke recipes, direct posters to ``/workflows/runs``)
# observe no contract change.
_SKIPPED_TRANSLATIONS: dict[str, dict[str, str]] = {
    "query cannot be empty": {"status": "skipped", "reason": "empty_query"},
    "limit must be positive": {"status": "skipped", "reason": "invalid_limit"},
}


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    """Delegate to ``SemanticScholarClient.research_brief`` and pass through.

    The tool method is synchronous (drives its own event loop via
    ``asyncio.run``); run it in a worker thread so it doesn't collide
    with the workflow engine's running event loop.
    """
    ctx.log(
        "research_brief_delegating",
        query=inp.query,
        limit=inp.limit,
        year_from=inp.year_from,
    )

    with SemanticScholarClient() as client:
        result = await asyncio.to_thread(
            client.research_brief,
            query=inp.query,
            limit=inp.limit,
            year_from=inp.year_from,
        )

    if result.get("status") == "error":
        translated = _SKIPPED_TRANSLATIONS.get(str(result.get("error", "")))
        if translated is not None:
            ctx.log("research_brief_skipped", reason=translated["reason"])
            return translated

    ctx.log("research_brief_delegated", status=result.get("status"))
    return result
