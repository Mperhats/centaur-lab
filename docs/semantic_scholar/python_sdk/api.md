---
title: API Endpoints - semanticscholar
url: https://semanticscholar.readthedocs.io/en/latest/api.html
site: semanticscholar.readthedocs.io
---

Back to top

[View this page](/en/latest/_sources/api.rst.txt "View this page")

# API Endpoints

## /datasets/v1

* * /datasets/v1/diffs/{start\_release\_id}/to/{end\_release\_id}/{dataset\_name}

    * GET: [`semanticscholar.SemanticScholar.SemanticScholar.get_dataset_diffs()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.get_dataset_diffs "semanticscholar.SemanticScholar.SemanticScholar.get_dataset_diffs")

* * /datasets/v1/release/

    * GET: [`semanticscholar.SemanticScholar.SemanticScholar.get_available_releases()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.get_available_releases "semanticscholar.SemanticScholar.SemanticScholar.get_available_releases")

* * /datasets/v1/release/{release\_id}

    * GET: [`semanticscholar.SemanticScholar.SemanticScholar.get_release()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.get_release "semanticscholar.SemanticScholar.SemanticScholar.get_release")

* * /datasets/v1/release/{release\_id}/dataset/{dataset\_name}

    * GET: [`semanticscholar.SemanticScholar.SemanticScholar.get_dataset_download_links()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.get_dataset_download_links "semanticscholar.SemanticScholar.SemanticScholar.get_dataset_download_links")

## /graph/v1/author

* * /graph/v1/author/batch

    * POST: [`semanticscholar.SemanticScholar.SemanticScholar.get_authors()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.get_authors "semanticscholar.SemanticScholar.SemanticScholar.get_authors")

* * /graph/v1/author/search

    * GET: [`semanticscholar.SemanticScholar.SemanticScholar.search_author()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.search_author "semanticscholar.SemanticScholar.SemanticScholar.search_author")

* * /graph/v1/author/{author\_id}

    * GET: [`semanticscholar.SemanticScholar.SemanticScholar.get_author()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.get_author "semanticscholar.SemanticScholar.SemanticScholar.get_author")

## /graph/v1/paper

* * /graph/v1/paper/autocomplete?query={query}

    * GET: [`semanticscholar.SemanticScholar.SemanticScholar.get_autocomplete()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.get_autocomplete "semanticscholar.SemanticScholar.SemanticScholar.get_autocomplete")

* * /graph/v1/paper/batch

    * POST: [`semanticscholar.SemanticScholar.SemanticScholar.get_papers()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.get_papers "semanticscholar.SemanticScholar.SemanticScholar.get_papers")

* * /graph/v1/paper/search

    * GET: [`semanticscholar.SemanticScholar.SemanticScholar.search_paper()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.search_paper "semanticscholar.SemanticScholar.SemanticScholar.search_paper")

* * /graph/v1/paper/search/bulk

    * GET: [`semanticscholar.SemanticScholar.SemanticScholar.search_paper()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.search_paper "semanticscholar.SemanticScholar.SemanticScholar.search_paper")

* * /graph/v1/paper/{author\_id}/papers

    * POST: [`semanticscholar.SemanticScholar.SemanticScholar.get_author_papers()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.get_author_papers "semanticscholar.SemanticScholar.SemanticScholar.get_author_papers")

* * /graph/v1/paper/{paper\_id}

    * GET: [`semanticscholar.SemanticScholar.SemanticScholar.get_paper()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.get_paper "semanticscholar.SemanticScholar.SemanticScholar.get_paper")

* * /graph/v1/paper/{paper\_id}/authors

    * POST: [`semanticscholar.SemanticScholar.SemanticScholar.get_paper_authors()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.get_paper_authors "semanticscholar.SemanticScholar.SemanticScholar.get_paper_authors")

* * /graph/v1/paper/{paper\_id}/citations

    * POST: [`semanticscholar.SemanticScholar.SemanticScholar.get_paper_citations()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.get_paper_citations "semanticscholar.SemanticScholar.SemanticScholar.get_paper_citations")

* * /graph/v1/paper/{paper\_id}/references

    * POST: [`semanticscholar.SemanticScholar.SemanticScholar.get_paper_references()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.get_paper_references "semanticscholar.SemanticScholar.SemanticScholar.get_paper_references")

## /graph/v1/snippet

* * /graph/v1/snippet/search

    * GET: [`semanticscholar.SemanticScholar.SemanticScholar.search_snippet()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.search_snippet "semanticscholar.SemanticScholar.SemanticScholar.search_snippet")

## /recommendations/v1

* * /recommendations/v1/papers/

    * POST: [`semanticscholar.SemanticScholar.SemanticScholar.get_recommended_papers_from_lists()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.get_recommended_papers_from_lists "semanticscholar.SemanticScholar.SemanticScholar.get_recommended_papers_from_lists")

* * /recommendations/v1/papers/forpaper/{paper\_id}

    * GET: [`semanticscholar.SemanticScholar.SemanticScholar.get_recommended_papers()`](/en/latest/mainclasses/semanticscholar.html#semanticscholar.SemanticScholar.SemanticScholar.get_recommended_papers "semanticscholar.SemanticScholar.SemanticScholar.get_recommended_papers")

---

Powered by [curl.md](https://curl.md)
