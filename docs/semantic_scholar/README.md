# Semantic Scholar API — Local Reference

Snapshot of the public Semantic Scholar API documentation, fetched via
[`curl.md`](https://curl.md) for the prose tutorial and directly from the
Swagger 2.0 specs for the API reference.

| File | Source | Notes |
| --- | --- | --- |
| [`tutorial.md`](./tutorial.md) | https://www.semanticscholar.org/product/api/tutorial | End-to-end usage walkthrough (search, recommendations, authors, datasets, pagination tips). |
| [`graph-api.md`](./graph-api.md) | https://api.semanticscholar.org/graph/v1/swagger.json | Academic Graph API — papers, authors, citations, references. |
| [`recommendations-api.md`](./recommendations-api.md) | https://api.semanticscholar.org/recommendations/v1/swagger.json | Paper recommendations from one or more seed papers. |
| [`datasets-api.md`](./datasets-api.md) | https://api.semanticscholar.org/datasets/v1/swagger.json | Bulk dataset downloads and incremental diffs. |
| [`openapi/*.json`](./openapi/) | Same hosts | Raw pretty-printed Swagger 2.0 specs (source of truth for the `*-api.md` files). |
| [`python_sdk/`](./python_sdk/) | https://semanticscholar.readthedocs.io/en/latest/ | Unofficial `semanticscholar` Python SDK reference (overview, install, usage, main classes, all 14 S2 object types, pagination, exceptions, changelog). Useful for cross-checking the SDK's surface against our overlay client. |

## Base URLs

- Academic Graph API: `https://api.semanticscholar.org/graph/v1`
- Recommendations API: `https://api.semanticscholar.org/recommendations/v1`
- Datasets API: `https://api.semanticscholar.org/datasets/v1`

## Refreshing this snapshot

```bash
mkdir -p docs/semantic_scholar/openapi

curl -fsSL "https://curl.md/https://www.semanticscholar.org/product/api/tutorial" \
  -o docs/semantic_scholar/tutorial.md

for api in graph recommendations datasets; do
  curl -fsSL "https://api.semanticscholar.org/${api}/v1/swagger.json" \
    | python3 -m json.tool > "docs/semantic_scholar/openapi/${api}.json"
done
```

The `*-api.md` files were generated from the OpenAPI specs with a small
Swagger-2.0-to-markdown script; rerun it (or any equivalent tool such as
`widdershins`) against the JSON in `openapi/` to regenerate them.

### Refreshing the Python SDK snapshot

```bash
mkdir -p docs/semantic_scholar/python_sdk/{mainclasses,s2objects}

BASE="https://semanticscholar.readthedocs.io/en/latest"
DEST="docs/semantic_scholar/python_sdk"

curl.md "$BASE/overview.html" > "$DEST/overview.md"
for p in install usage reference pagination exceptions api changes; do
  curl.md "$BASE/$p.html" > "$DEST/$p.md"
done
for p in semanticscholar asyncsemanticscholar; do
  curl.md "$BASE/mainclasses/$p.html" > "$DEST/mainclasses/$p.md"
done
for p in Author Autocomplete Citation Dataset DatasetDiff IncrementalUpdate \
         Journal Paper PublicationVenue Reference Release Snippet \
         SnippetPaper SnippetText Tldr; do
  curl.md "$BASE/s2objects/$p.html" > "$DEST/s2objects/$p.md"
done
```

A few of the readthedocs pages (`usage`, `changes`, both main classes) emit a
stray `## data` line before the frontmatter; strip it after fetching if it
reappears.
