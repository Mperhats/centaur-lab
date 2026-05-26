"""Shim around ``api.vm_metrics`` for ``company_context_documents`` upserts.

Lives under ``overlay/shared/`` so both ``overlay/workflows/`` and
``overlay/tools/`` callers can emit ``vm_metrics`` events with a single
import (``emit_document_metrics``) at every upsert call site.

Inside the API pod ``api.vm_metrics`` resolves cleanly and calls land in
the real Prometheus counters. Outside the pod (every local pytest run,
where the ``api`` package isn't on sys.path) the import fails and the
fallback no-op stubs take over, so callers can invoke
``emit_document_metrics`` unconditionally without scattering import
guards. The fallback path is exercised by every local test run.

Mirrors the call shape in
``.centaur/workflows/company_context_documents.py`` so dashboards can
aggregate overlay rows alongside Slack ETL rows.
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
    # can call emit_document_metrics unconditionally.
    def _observe_size(source: str, source_type: str, chars: int) -> None:
        return None

    def _record_changed(
        source: str,
        source_type: str,
        action: str,
        count: int = 1,
    ) -> None:
        return None


def emit_document_metrics(document: dict[str, Any], action: str) -> None:
    """Emit size + change metrics for a company_context_documents upsert.

    Matches the upstream call shape from ``company_context_documents.py``:
    observe size unconditionally (so noops still feed the histogram), then
    record the change keyed by the action returned from ``upsert_document``.

    Args:
        document: The projected document dict (must have ``source``,
            ``source_type``, and ``body`` keys).
        action: ``"inserted"``, ``"updated"``, or ``"noop"`` — the value
            ``upsert_document`` returns.
    """
    source = str(document.get("source", ""))
    source_type = str(document.get("source_type", ""))
    body = str(document.get("body") or "")
    _observe_size(source, source_type, len(body))
    _record_changed(source, source_type, action)
