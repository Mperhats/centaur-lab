---
name: academic-research
description: "Use when answering questions about academic papers, citations, literature reviews, or research briefs — search Semantic Scholar, build persisted lit-review briefs, surface paper metadata, walk the citation graph. Triggers on peer-reviewed papers, preprints, arXiv, NeurIPS, biology/chemistry journals, ML/AI research topics, \"research brief\", \"lit review\", \"literature review\", or \"what does the literature say about X\"."
---

# Academic Research

Use the `semantic_scholar` tool whenever the user asks about peer-reviewed
papers, preprints, citations, or what a specific researcher has published.
Prefer it over generic web search for anything that lives in the academic
literature: arXiv, NeurIPS, biology/chem journals, etc.

**Default flow: persist with a research brief.** After finding papers via
`search`, call `save_papers` (which always writes a linked
`research_brief` row) or call `semantic_scholar.research_brief` directly
for a single-query lit review. Skip persistence ONLY when the user
explicitly says "just search", "don't save", "exploratory only", or
otherwise signals they don't want the result remembered.

**Pick the right surface:**

- "Find papers about X" → `semantic_scholar.search` + `save_papers` follow-up (brief + papers)
- "Summarize this paper" / DOI / arXiv ID / S2 ID → `semantic_scholar.get_paper` + `save_papers` follow-up (brief + paper)
- "What does this paper cite?" → `semantic_scholar.get_references`
- "Build a brief / lit review / writeup on X" → `semantic_scholar.research_brief` (atomic search + render + persist)
- "Read the actual paper / quote from the body / I need more than the abstract" → `semantic_scholar.archive_paper` (single) or `archive_papers` workflow (batch) — fetches the open-access PDF, parses to Markdown, and indexes the full text for BM25 search

## Paper Search (`semantic_scholar.search`)

`search` queries the Semantic Scholar Graph API live. It does **not**
read from `company_context_documents` — there is no internal cache lane.

The response shape is `{status, query, limit, year_from, count, results}`.
Each entry in `results` is a standard S2 paper dict (`paperId`, `title`,
`authors`, `year`, `abstract`, `url`, `citationCount`, ...).

`search` does not auto-persist. After a successful search, call
`save_papers` on the `paperId`s you want remembered unless the user opted
out — `save_papers` always upserts a linked `research_brief` row plus
child paper rows. Use `research_brief` when one S2 query should drive the
whole writeup atomically.

`search_papers` is the lower-level sibling — same live API, raises on
failure instead of returning an error envelope. Prefer `search` for
agent turns; use `search_papers` only when you need exceptions or are
calling from code that already handles them.

## Output Expectations

For a Slack reply, return at most 5 papers unless the user asks for more.
For each paper include: title, first author + et al., year, citation count,
and one sentence on the contribution drawn from the abstract. Link to
`url` (or `openAccessPdf.url` when present) so the human can dive in.

Do NOT fabricate titles, authors, or DOIs. If `search` returns
nothing, say so and offer alternative queries — don't substitute web
results.

## What Not To Do

- Don't use `web_search` for academic questions when this tool fits — its
  rankings are tuned for the open web, not the literature.
- Don't loop `get_paper` over hundreds of IDs in one turn; batch via
  `search_papers` first and only fetch full metadata for the few you'll
  cite.
- Don't manually concatenate paper summaries into a Slack reply when the
  user asked for a brief / lit review / writeup — use
  `semantic_scholar.research_brief` so the brief is also persisted in
  `company_context_documents` for future retrieval via `company_context` or
  RAG. Use `semantic_scholar.research_brief` when the user asked for a
  brief / lit review / writeup.
- Don't fall back to `call workflow run` for `research_brief` — the
  workflow handler is now a thin back-compat wrapper around the tool method.
  External `/workflows/runs` callers (Justfile cluster smoke, etc.) still
  work, but in-Slack agent turns should always go through the tool.

## Persisting Research

Two on-demand surfaces turn ad-hoc Semantic Scholar lookups into durable
knowledge by upserting rows into `company_context_documents`: the
`save_papers` workflow and the `semantic_scholar.research_brief` tool
method. Both are content-hash idempotent — re-running with the same input
is safe and cheap (returns `noop` actions), so you don't have to track
whether you've already saved something.

### `save_papers` — Remember Specific Papers (+ brief)

The implicit default after every `search` / `search_papers` / `get_paper`
turn (see "Default flow" above). Also fires on explicit asks: "save these
papers", "remember these for later", "add these to context".

```bash
call workflow run '{"workflow_name":"save_papers","input":{"paper_ids":["173ba8ae...","abcd1234..."],"query":"diffusion models"}}'
```

Returns `{status, papers_inserted, papers_updated, papers_noop, papers_failed,
brief_document_id, brief_action, brief_query, results}`. Always writes a
`research_brief` row linking the saved papers. Pass the original user query
in the optional `query` field for traceability; when omitted, a stable
`save_papers:<hash>` query is synthesized from the paper ID set.

Idempotency means the save call is cheap to make even when the agent
isn't sure whether the papers are already cached: re-saving an unchanged
paper returns `noop` and writes nothing.

### `research_brief` — Synthesized Lit Review, Persisted

Use when the user wants a writeup, not just a list. Trigger phrases:
"build a research brief", "give me a literature summary", "do a lit review
on X", "summary of what's known about X", "what does the literature say
about Y". Prefer this over chaining `search_papers` + `save_papers` — it
does both atomically: searches S2, renders a structured Markdown brief,
upserts the brief as one row, and upserts each underlying paper as a child
row pointing at the brief.

The tool method is what `call discover semantic_scholar` surfaces; invoke
it directly via the tool surface (no `call workflow run` needed):

`semantic_scholar.research_brief(query="active inference world models", limit=5)`

Returns `{status, brief_document_id, brief_action, results_count,
papers_inserted, papers_updated, papers_noop, markdown}`. The `markdown`
field is the full rendered brief — post that back to Slack as your reply.
The `brief_document_id` is stable for the same query + `year_from`
(case-insensitive), so re-running updates the same row instead of accruing
duplicates; surface it for traceability so a future turn (or a RAG retrieval
over `company_context_documents`) can pivot back to the exact brief.

## Archiving Full-Text PDFs

`save_papers` and `research_brief` only ever index the abstract — useful
for ranking and discovery, not for substantive quoting or methodology
review. When the user wants more than the abstract ("read the paper",
"quote from the methods section", "what does this paper actually
report?"), reach for the archive surface.

Two surfaces:

- `semantic_scholar.archive_paper(paper_id)` — single paper, agent-facing
  tool method. Returns inline; safe to call from a Slack turn.
- `archive_papers` workflow — batch over a list of paper IDs. Best when
  the user just produced a brief and wants the bodies of every cited
  paper indexed for later retrieval.

Pipeline (both surfaces):

1. Resolves the PDF URL from `openAccessPdf.url`, falling back to
   `https://arxiv.org/pdf/{externalIds.ArXiv}.pdf` when present.
2. Streams the PDF with a 50 MiB hard cap. Paywalled / oversized papers
   return `{"status": "skipped", "reason": "no_pdf_url" | "too_large"}` —
   not an error, just unfetchable. Surface this to the user verbatim
   instead of retrying.
3. Parses through a `pymupdf4llm` → `pymupdf` → `pypdf` fallback chain
   with a 100-char min-size guard between tiers. The first tier produces
   real Markdown (preserves headings, tables, reading order); the
   later tiers are plain-text fallbacks for image-only or restricted-env
   PDFs.
4. Persists three rows:
   - raw bytes + parsed text in `paper_archives` (overlay-owned, keyed
     by paperId — source of truth, lets us re-parse without re-fetching)
   - the metadata row in `company_context_documents` with
     `source_type="paper"` (same shape as `save_papers` writes)
   - the parsed Markdown body in `company_context_documents` with
     `source_type="paper_fulltext"`, `parent_document_id` pointing at
     the metadata row

Idempotent on `(paper_id, pdf_sha256)` — re-running on an unchanged PDF
returns `{"status": "noop", "archive_action": "noop", ...}` without
re-parsing or rewriting. Safe to call without checking whether the
paper has been archived before.

When ranking or filtering search results downstream, the
`paper_fulltext` rows make the body searchable via BM25. The `paper`
rows remain unchanged so abstract-level queries keep their existing
recall and idempotency contracts.

Examples:

```bash
# Single paper
call discover semantic_scholar
call run semantic_scholar archive_paper '{"paper_id":"173ba8ae4582b6f9f6919aa3f813579a5349f1f9"}'

# Batch over a brief's paper IDs
call workflow run '{"workflow_name":"archive_papers","input":{"paper_ids":["173ba8ae...","abcd1234..."]}}'
```

Don't archive a paper just to read its abstract — the abstract is
already in the metadata row. Archive only when the user actually needs
the body. Don't loop `archive_paper` over hundreds of IDs in one turn;
post to the `archive_papers` workflow instead so the API pod handles
the batch with a single connection lease.
