"""Shim around ``api.vm_metrics`` for ``company_context_documents`` upserts.

Lives under ``overlay/shared/`` so both ``overlay/workflows/`` and
``overlay/tools/`` callers can emit ``vm_metrics`` events with a single
import at every upsert call site.

Inside the API pod ``api.vm_metrics`` resolves cleanly and calls land in
the real Prometheus counters. Outside the pod (every local pytest run,
where the ``api`` package isn't on sys.path) the import fails and the
fallback no-op stubs take over, so callers can invoke these helpers
unconditionally without scattering import guards. The fallback path is
exercised by every local test run.

Mirrors the call shape in
``.centaur/workflows/company_context_documents.py`` so dashboards can
aggregate overlay rows alongside Slack ETL rows.

Two-function shape
------------------

Upstream observes the document size histogram *before* the upsert and
records the change counter *after* the upsert (see
``company_context_documents.py:511-521``). The split means a document
is "seen" by the size histogram even when the upsert raises, and the
change counter only fires once the action (``inserted`` / ``updated``
/ ``noop``) is known. Call sites should follow the same order::

    observe_document_size(document)
    action = await upsert_document(...)
    record_document_change(document, action)
"""

from __future__ import annotations

from typing import Any

try:
    from api.vm_metrics import (
        observe_company_context_document_size as _observe_size,
    )
    from api.vm_metrics import (
        record_company_context_documents_changed as _record_changed,
    )
except ImportError:
    # The api package is on sys.path inside the production API pod but not
    # during local pytest runs; fall back to no-op stubs so workflow code
    # can call these helpers unconditionally.
    def _observe_size(source: str, source_type: str, chars: int) -> None:
        return None

    def _record_changed(
        source: str,
        source_type: str,
        action: str,
        count: int = 1,
    ) -> None:
        return None


def observe_document_size(document: dict[str, Any]) -> None:
    """Observe the size histogram for a projected document.

    Call this BEFORE invoking ``upsert_document`` so the histogram still
    sees the document even if the upsert raises — matches the upstream
    ordering in ``company_context_documents.py``.

    Args:
        document: The projected document dict (must have ``source``,
            ``source_type``, and ``body`` keys).
    """
    source = str(document.get("source", ""))
    source_type = str(document.get("source_type", ""))
    body = str(document.get("body") or "")
    _observe_size(source, source_type, len(body))


def record_document_change(document: dict[str, Any], action: str) -> None:
    """Record the change counter for a completed document upsert.

    Call this AFTER ``upsert_document`` returns, with the action it
    returned. Matches the upstream ordering in
    ``company_context_documents.py`` — the counter is keyed on the
    realized action, so it must run after the upsert resolves.

    Args:
        document: The projected document dict (must have ``source`` and
            ``source_type`` keys).
        action: ``"inserted"``, ``"updated"``, or ``"noop"`` — the value
            ``upsert_document`` returns.
    """
    source = str(document.get("source", ""))
    source_type = str(document.get("source_type", ""))
    _record_changed(source, source_type, action)
