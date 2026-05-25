---
name: academic-research
description: Use when answering questions about academic papers, citations, or research literature — search Semantic Scholar, surface paper metadata, walk the citation graph.
---

# academic-research

Use the `semantic_scholar` tool whenever the user asks about peer-reviewed
papers, preprints, citations, or what a specific researcher has published.
Prefer it over generic web search for anything that lives in the academic
literature: arXiv, NeurIPS, biology/chem journals, etc.

## When to use

- "Find recent papers about X" → `semantic_scholar.search_papers(query=X, year_from=<recent year>)`
- "Summarize this paper" given a DOI / arXiv ID / S2 ID → `semantic_scholar.get_paper(paper_id=...)`
- "What does this paper cite?" / building a related-work list → `semantic_scholar.get_references(paper_id=..., limit=20)`

## Output expectations

For a Slack reply, return at most 5 papers unless the user asks for more.
For each paper include: title, first author + et al., year, citation count,
and one sentence on the contribution drawn from the abstract. Link to
`url` (or `openAccessPdf.url` when present) so the human can dive in.

Do NOT fabricate titles, authors, or DOIs. If `search_papers` returns
nothing, say so and offer alternative queries — don't substitute web
results.

## Anti-patterns

- Don't use `web_search` for academic questions when this tool fits — its
  rankings are tuned for the open web, not the literature.
- Don't loop `get_paper` over hundreds of IDs in one turn; batch via
  `search_papers` first and only fetch full metadata for the few you'll
  cite.
