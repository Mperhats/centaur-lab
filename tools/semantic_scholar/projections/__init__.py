"""Projection helpers for the ``semantic_scholar`` tool.

Each module in this subpackage projects a Semantic Scholar API shape
(``paper``, ``research_brief``) into a row for the
``company_context_documents`` table. Projections are pure — they only
compose the input shape, ``utils.canonical_json`` / ``utils.content_hash``,
and standard-library helpers — so they unit-test without a database, an
API pod, or any other workflow context.

Callers that need persistence (``client.research_brief`` and
``workflows/save_papers``) inline the ``vm_metrics`` shim and the
``_upsert_document`` SQL alongside their own call to these builders,
matching the upstream ``company_context_documents`` duplicate-and-mirror
convention. The projections live in this shared subpackage because the
projection logic itself is fixed by the table schema, but the
persistence path is per-call-site so it can vary on parent linkage,
retry semantics, and metric labels.
"""
