---
title: SemanticScholar - semanticscholar
url: https://semanticscholar.readthedocs.io/en/latest/mainclasses/semanticscholar.html
site: semanticscholar.readthedocs.io
---

Back to top

[View this page](/en/latest/_sources/mainclasses/semanticscholar.rst.txt "View this page")

# SemanticScholar

* *class*semanticscholar.SemanticScholar.SemanticScholar(*timeout:int=30*, *api\_key:str|None=None*, *api\_url:str|None=None*, *debug:bool=False*, *retry:bool=True*)

  Main class to retrieve data from Semantic Scholar Graph API synchronously.

  * Parameters:

    * **timeout** (*float*) – (optional) an exception is raised if the server has not issued a response for timeout seconds.

    * **api\_key** (*str*) – (optional) private API key.

    * **api\_url** (*str*) – (optional) custom API url.

    * **debug** (*bool*) – (optional) enable debug mode.

    * **retry** (*bool*) – enable retry mode.

  - get\_author(*author\_id:str*, *fields:list|None=None*) → [Author](/en/latest/s2objects/Author.html#semanticscholar.Author.Author "semanticscholar.Author.Author")

    Author lookup

    * Calls:

      [GET /graph/v1/author/{author\_id}](https://api.semanticscholar.org/api-docs/graph#tag/Author-Data/operation/get_graph_get_author)

    * Parameters:

      **author\_id** (*str*) – S2AuthorId.

    * Returns:

      author data

    * Return type:

      [`semanticscholar.Author.Author`](/en/latest/s2objects/Author.html#semanticscholar.Author.Author "semanticscholar.Author.Author")

    * Raises:

      ObjectNotFoundException: if Author ID not found.

  * get\_author\_papers(*author\_id:str*, *fields:list|None=None*, *limit:int=100*) → [PaginatedResults](/en/latest/pagination.html#semanticscholar.PaginatedResults.PaginatedResults "semanticscholar.PaginatedResults.PaginatedResults")

    Get details about a author’s papers

    * Calls:

      [POST /graph/v1/paper/{author\_id}/papers](https://api.semanticscholar.org/api-docs/graph#tag/Paper-Data/operation/get_graph_get_author_papers)

    * Parameters:

      * **paper\_id** (*str*) –

        S2PaperId, CorpusId, DOI, ArXivId, MAG, ACL, PMID, PMCID, or URL from:

        * semanticscholar.org

        * arxiv.org

        * aclweb.org

        * acm.org

        * biorxiv.org

      * **fields** (*list*) – (optional) list of the fields to be returned.

      * **limit** (*int*) – (optional) maximum number of results to return (must be <= 1000).

  - get\_authors(*author\_ids:List\[str]*, *fields:list|None=None*, *return\_not\_found:bool=False*) → List\[[Author](/en/latest/s2objects/Author.html#semanticscholar.Author.Author "semanticscholar.Author.Author")]|Tuple\[List\[[Author](/en/latest/s2objects/Author.html#semanticscholar.Author.Author "semanticscholar.Author.Author")],List\[str]]

    Get details for multiple authors at once

    * Calls:

      [POST /graph/v1/author/batch](https://api.semanticscholar.org/api-docs/graph#tag/Author-Data/operation/get_graph_get_author)

    * Parameters:

      **author\_ids** (*str*) – list of S2AuthorId (must be <= 1000).

    * Returns:

      author data, and optionally list of IDs not found.

    * Return type:

      `List` of [`semanticscholar.Author.Author`](/en/latest/s2objects/Author.html#semanticscholar.Author.Author "semanticscholar.Author.Author") or `Tuple` \[`List` of [`semanticscholar.Author.Author`](/en/latest/s2objects/Author.html#semanticscholar.Author.Author "semanticscholar.Author.Author"), `List` of `str`]

    * Raises:

      BadQueryParametersException: if no author was found.

  * get\_autocomplete(*query:str*) → List\[[Autocomplete](/en/latest/s2objects/Autocomplete.html#semanticscholar.Autocomplete.Autocomplete "semanticscholar.Autocomplete.Autocomplete")]

    Get autocomplete suggestions for a paper query.

    * Calls:

      [GET /graph/v1/paper/autocomplete?query={query}](https://api.semanticscholar.org/api-docs/graph#tag/Paper-Data/operation/get_graph_get_paper_autocomplete)

    * Parameters:

      **query** (*str*) – query to get autocomplete suggestions for.

    * Returns:

      list of autocomplete suggestions.

    * Return type:

      `List` of [`semanticscholar.Autocomplete.Autocomplete`](/en/latest/s2objects/Autocomplete.html#semanticscholar.Autocomplete.Autocomplete "semanticscholar.Autocomplete.Autocomplete")

  - get\_available\_releases() → List\[str]

    Gets all available dataset releases.

    * Calls:

      [GET /datasets/v1/release/](https://api.semanticscholar.org/api-docs/datasets#tag/Release-Data/operation/get_releases)

    * Returns:

      list of available release ids.

    * Return type:

      `List` of `str`

  * get\_dataset\_diffs(*dataset\_name:str*, *start\_release\_id:str*, *end\_release\_id:str*) → [DatasetDiff](/en/latest/s2objects/DatasetDiff.html#semanticscholar.DatasetDiff.DatasetDiff "semanticscholar.DatasetDiff.DatasetDiff")

    Get incremental diffs for a dataset between two releases.

    * Calls:

      [GET /datasets/v1/diffs/{start\_release\_id}/to/ {end\_release\_id}/{dataset\_name}](https://api.semanticscholar.org/api-docs/datasets#tag/Incremental-Updates/operation/get_diff)

    * Parameters:

      * **dataset\_name** (*str*) – Name of the dataset.

      * **start\_release\_id** (*str*) – ID of the release currently held by the client.

      * **end\_release\_id** (*str*) – ID of the release the client wishes to update to, or ‘latest’ for the most recent release.

    * Returns:

      information containing dataset, start\_release, end\_release, and list of diffs.

    * Return type:

      [`semanticscholar.DatasetDiff.DatasetDiff`](/en/latest/s2objects/DatasetDiff.html#semanticscholar.DatasetDiff.DatasetDiff "semanticscholar.DatasetDiff.DatasetDiff")

  - get\_dataset\_download\_links(*release\_id:str*, *dataset\_name:str*) → [Dataset](/en/latest/s2objects/Dataset.html#semanticscholar.Dataset.Dataset "semanticscholar.Dataset.Dataset")

    Get download links for a specific dataset in a release.

    * Calls:

      [GET /datasets/v1/release/{release\_id}/dataset/{dataset\_name}](https://api.semanticscholar.org/api-docs/datasets#tag/Release-Data/operation/get_dataset)

    * Parameters:

      * **release\_id** (*str*) – Release identifier (e.g., ‘2023-12-01’).

      * **dataset\_name** (*str*) – Name of the dataset.

    * Returns:

      dataset information including download links.

    * Return type:

      [`semanticscholar.Dataset.Dataset`](/en/latest/s2objects/Dataset.html#semanticscholar.Dataset.Dataset "semanticscholar.Dataset.Dataset")

  * get\_paper(*paper\_id:str*, *fields:list|None=None*) → [Paper](/en/latest/s2objects/Paper.html#semanticscholar.Paper.Paper "semanticscholar.Paper.Paper")

    Paper lookup

    * Calls:

      [GET /graph/v1/paper/{paper\_id}](https://api.semanticscholar.org/api-docs/graph#tag/Paper-Data/operation/get_graph_get_paper)

    * Parameters:

      * **paper\_id** (*str*) –

        S2PaperId, CorpusId, DOI, ArXivId, MAG, ACL, PMID, PMCID, or URL from:

        * semanticscholar.org

        * arxiv.org

        * aclweb.org

        * acm.org

        * biorxiv.org

      * **fields** (*list*) – (optional) list of the fields to be returned.

    * Returns:

      paper data

    * Return type:

      [`semanticscholar.Paper.Paper`](/en/latest/s2objects/Paper.html#semanticscholar.Paper.Paper "semanticscholar.Paper.Paper")

    * Raises:

      ObjectNotFoundException: if Paper ID not found.

  - get\_paper\_authors(*paper\_id:str*, *fields:list|None=None*, *limit:int=100*) → [PaginatedResults](/en/latest/pagination.html#semanticscholar.PaginatedResults.PaginatedResults "semanticscholar.PaginatedResults.PaginatedResults")

    Get details about a paper’s authors

    * Calls:

      [POST /graph/v1/paper/{paper\_id}/authors](https://api.semanticscholar.org/api-docs/graph#tag/Paper-Data/operation/get_graph_get_paper_authors)

    * Parameters:

      * **paper\_id** (*str*) –

        S2PaperId, CorpusId, DOI, ArXivId, MAG, ACL, PMID, PMCID, or URL from:

        * semanticscholar.org

        * arxiv.org

        * aclweb.org

        * acm.org

        * biorxiv.org

      * **fields** (*list*) – (optional) list of the fields to be returned.

      * **limit** (*int*) – (optional) maximum number of results to return (must be <= 1000).

  * get\_paper\_citations(*paper\_id:str*, *fields:list|None=None*, *limit:int=100*) → [PaginatedResults](/en/latest/pagination.html#semanticscholar.PaginatedResults.PaginatedResults "semanticscholar.PaginatedResults.PaginatedResults")

    Get details about a paper’s citations

    * Calls:

      [POST /graph/v1/paper/{paper\_id}/citations](https://api.semanticscholar.org/api-docs/graph#tag/Paper-Data/operation/get_graph_get_paper_citations)

    * Parameters:

      * **paper\_id** (*str*) –

        S2PaperId, CorpusId, DOI, ArXivId, MAG, ACL, PMID, PMCID, or URL from:

        * semanticscholar.org

        * arxiv.org

        * aclweb.org

        * acm.org

        * biorxiv.org

      * **fields** (*list*) – (optional) list of the fields to be returned.

      * **limit** (*int*) – (optional) maximum number of results to return (must be <= 1000).

  - get\_paper\_references(*paper\_id:str*, *fields:list|None=None*, *limit:int=100*) → [PaginatedResults](/en/latest/pagination.html#semanticscholar.PaginatedResults.PaginatedResults "semanticscholar.PaginatedResults.PaginatedResults")

    Get details about a paper’s references

    * Calls:

      [POST /graph/v1/paper/{paper\_id}/references](https://api.semanticscholar.org/api-docs/graph#tag/Paper-Data/operation/get_graph_get_paper_references)

    * Parameters:

      * **paper\_id** (*str*) –

        S2PaperId, CorpusId, DOI, ArXivId, MAG, ACL, PMID, PMCID, or URL from:

        * semanticscholar.org

        * arxiv.org

        * aclweb.org

        * acm.org

        * biorxiv.org

      * **fields** (*list*) – (optional) list of the fields to be returned.

      * **limit** (*int*) – (optional) maximum number of results to return (must be <= 1000).

  * get\_papers(*paper\_ids:List\[str]*, *fields:list|None=None*, *return\_not\_found:bool=False*) → List\[[Paper](/en/latest/s2objects/Paper.html#semanticscholar.Paper.Paper "semanticscholar.Paper.Paper")]|Tuple\[List\[[Paper](/en/latest/s2objects/Paper.html#semanticscholar.Paper.Paper "semanticscholar.Paper.Paper")],List\[str]]

    Get details for multiple papers at once

    * Calls:

      [POST /graph/v1/paper/batch](https://api.semanticscholar.org/api-docs/graph#tag/Paper-Data/operation/post_graph_get_papers)

    * Parameters:

      * **paper\_ids** (*str*) –

        list of IDs (must be <= 500) - S2PaperId, CorpusId, DOI, ArXivId, MAG, ACL, PMID, PMCID, or URL from:

        * semanticscholar.org

        * arxiv.org

        * aclweb.org

        * acm.org

        * biorxiv.org

      * **fields** (*list*) – (optional) list of the fields to be returned.

      * **return\_not\_found** (*bool*) – (optional) flag to include not found IDs in the return, except for IDs in URL:\<url> format.

    * Returns:

      papers data, and optionally list of IDs not found.

    * Return type:

      `List` of [`semanticscholar.Paper.Paper`](/en/latest/s2objects/Paper.html#semanticscholar.Paper.Paper "semanticscholar.Paper.Paper") or `Tuple` \[`List` of [`semanticscholar.Paper.Paper`](/en/latest/s2objects/Paper.html#semanticscholar.Paper.Paper "semanticscholar.Paper.Paper"), `List` of `str`]

    * Raises:

      BadQueryParametersException: if no paper was found.

  - get\_recommended\_papers(*paper\_id:str*, *fields:list|None=None*, *limit:int=100*, *pool\_from:Literal\['recent','all-cs']='recent'*) → List\[[Paper](/en/latest/s2objects/Paper.html#semanticscholar.Paper.Paper "semanticscholar.Paper.Paper")]

    Get recommended papers for a single positive example.

    * Calls:

      [GET /recommendations/v1/papers/forpaper/{paper\_id}](https://api.semanticscholar.org/api-docs/recommendations#tag/Paper-Recommendations/operation/get_papers_for_paper)

    * Parameters:

      * **paper\_id** (*str*) –

        S2PaperId, CorpusId, DOI, ArXivId, MAG, ACL, PMID, PMCID, or URL from:

        * semanticscholar.org

        * arxiv.org

        * aclweb.org

        * acm.org

        * biorxiv.org

      * **fields** (*list*) – (optional) list of the fields to be returned.

      * **limit** (*int*) – (optional) maximum number of recommendations to return (must be <= 500).

      * **pool\_from** (*str*) – (optional) which pool of papers to recommend from. Must be either “recent” or “all-cs”.

    * Returns:

      list of recommendations.

    * Return type:

      `List` of [`semanticscholar.Paper.Paper`](/en/latest/s2objects/Paper.html#semanticscholar.Paper.Paper "semanticscholar.Paper.Paper")

  * get\_recommended\_papers\_from\_lists(*positive\_paper\_ids:List\[str]*, *negative\_paper\_ids:List\[str]|None=None*, *fields:list|None=None*, *limit:int=100*) → List\[[Paper](/en/latest/s2objects/Paper.html#semanticscholar.Paper.Paper "semanticscholar.Paper.Paper")]

    Get recommended papers for lists of positive and negative examples.

    * Calls:

      [POST /recommendations/v1/papers/](https://api.semanticscholar.org/api-docs/recommendations#tag/Paper-Recommendations/operation/post_papers)

    * Parameters:

      * **positive\_paper\_ids** (*list*) – list of paper IDs that the returned papers should be related to.

      * **negative\_paper\_ids** (*list*) – (optional) list of paper IDs that the returned papers should not be related to.

      * **fields** (*list*) – (optional) list of the fields to be returned.

      * **limit** (*int*) – (optional) maximum number of recommendations to return (must be <= 500).

    * Returns:

      list of recommendations.

    * Return type:

      `List` of [`semanticscholar.Paper.Paper`](/en/latest/s2objects/Paper.html#semanticscholar.Paper.Paper "semanticscholar.Paper.Paper")

  - get\_release(*release\_id:str*) → [Release](/en/latest/s2objects/Release.html#semanticscholar.Release.Release "semanticscholar.Release.Release")

    Get a specific release.

    * Calls:

      [GET /datasets/v1/release/{release\_id}](https://api.semanticscholar.org/api-docs/datasets#tag/Release-Data/operation/get_release)

    * Parameters:

      **release\_id** (*str*) – Release identifier (e.g., ‘2023-12-01’).

    * Returns:

      release information including datasets.

    * Return type:

      [`semanticscholar.Release.Release`](/en/latest/s2objects/Release.html#semanticscholar.Release.Release "semanticscholar.Release.Release")

  * search\_author(*query:str*, *fields:list|None=None*, *limit:int=100*) → [PaginatedResults](/en/latest/pagination.html#semanticscholar.PaginatedResults.PaginatedResults "semanticscholar.PaginatedResults.PaginatedResults")

    Search for authors by name

    * Calls:

      [GET /graph/v1/author/search](https://api.semanticscholar.org/api-docs/graph#tag/Author-Data/operation/get_graph_get_author_search)

    * Parameters:

      * **query** (*str*) – plain-text search query string.

      * **fields** (*list*) – (optional) list of the fields to be returned.

      * **limit** (*int*) – (optional) maximum number of results to return (must be <= 1000).

    * Returns:

      query results.

    * Return type:

      [`semanticscholar.PaginatedResults.PaginatedResults`](/en/latest/pagination.html#semanticscholar.PaginatedResults.PaginatedResults "semanticscholar.PaginatedResults.PaginatedResults")

  - search\_paper(*query:str*, *year:str|None=None*, *publication\_types:list|None=None*, *open\_access\_pdf:bool|None=None*, *venue:list|None=None*, *fields\_of\_study:list|None=None*, *fields:list|None=None*, *publication\_date\_or\_year:str|None=None*, *min\_citation\_count:int|None=None*, *limit:int=100*, *bulk:bool=False*, *sort:str|None=None*, *match\_title:bool=False*) → [PaginatedResults](/en/latest/pagination.html#semanticscholar.PaginatedResults.PaginatedResults "semanticscholar.PaginatedResults.PaginatedResults")|[Paper](/en/latest/s2objects/Paper.html#semanticscholar.Paper.Paper "semanticscholar.Paper.Paper")

    Search for papers by keyword. Performs a search query based on the S2 search relevance algorithm, or a bulk retrieval of basic paper data without search relevance (if bulk=True). Paper relevance search is the default behavior and returns up to 1,000 results. Bulk retrieval instead returns up to 10,000,000 results (1,000 in each page).

    * Calls:

      [GET /graph/v1/paper/search](https://api.semanticscholar.org/api-docs/graph#tag/Paper-Data/operation/get_graph_paper_relevance_search)

    * Calls:

      [GET /graph/v1/paper/search/bulk](https://api.semanticscholar.org/api-docs/graph#tag/Paper-Data/operation/get_graph_paper_bulk_search)

    * Parameters:

      * **query** (*str*) – plain-text search query string.

      * **year** (*str*) – (optional) restrict results to the given range of publication year.

      * **publication\_type** (*list*) – (optional) restrict results to the given publication type list.

      * **open\_access\_pdf** (*bool*) – (optional) restrict results to papers with public PDFs.

      * **venue** (*list*) – (optional) restrict results to the given venue list.

      * **fields\_of\_study** (*list*) – (optional) restrict results to given field-of-study list, using the s2FieldsOfStudy paper field.

      * **fields** (*list*) – (optional) list of the fields to be returned.

      * **publication\_date\_or\_year** (*str*) – (optional) restrict results to the given range of publication date in the format \<start\_date>:\<end\_date>, where dates are in the format YYYY-MM-DD, YYYY-MM, or YYYY.

      * **min\_citation\_count** (*int*) – (optional) restrict results to papers with at least the given number of citations.

      * **limit** (*int*) – (optional) maximum number of results to return (must be <= 100).

      * **bulk** (*bool*) – (optional) bulk retrieval of basic paper data without search relevance (ignores the limit parameter if True and returns up to 1,000 results in each page).

      * **sort** (*str*) – (optional) sorts results (only if bulk=True) using \<field>:\<order> format, where “field” is either paperId, publicationDate, or citationCount, and “order” is asc (ascending) or desc (descending).

      * **match\_title** (*bool*) – (optional) retrieve a single paper whose title best matches the given query.

    * Returns:

      query results.

    * Return type:

      [`semanticscholar.PaginatedResults.PaginatedResults`](/en/latest/pagination.html#semanticscholar.PaginatedResults.PaginatedResults "semanticscholar.PaginatedResults.PaginatedResults") or [`semanticscholar.Paper.Paper`](/en/latest/s2objects/Paper.html#semanticscholar.Paper.Paper "semanticscholar.Paper.Paper")

  * search\_snippet(*query:str*, *paper\_ids:List\[str]|None=None*, *authors:List\[str]|None=None*, *min\_citation\_count:int|None=None*, *year:str|None=None*, *venue:list|None=None*, *fields\_of\_study:list|None=None*, *fields:list|None=None*, *publication\_date\_or\_year:str|None=None*, *limit:int=10*) → List\[[Snippet](/en/latest/s2objects/Snippet.html#semanticscholar.Snippet.Snippet "semanticscholar.Snippet.Snippet")]

    Search for text snippets matching a query. Text snippets are excerpts of approximately 500 words, drawn from a paper’s title, abstract, and body text.

    * Calls:

      [GET /graph/v1/snippet/search](https://api.semanticscholar.org/api-docs/graph#tag/Snippet-Text/operation/get_snippet_search)

    * Parameters:

      * **query** (*str*) – plain-text search query string.

      * **paper\_ids** (*list*) – (optional) restrict results to snippets from specific papers (up to \~100 IDs).

      * **authors** (*list*) – (optional) restrict results to papers with authors matching the given names.

      * **min\_citation\_count** (*int*) – (optional) restrict results to papers with at least the given number of citations.

      * **year** (*str*) – (optional) restrict results to the given range of publication year.

      * **venue** (*list*) – (optional) restrict results to the given venue list.

      * **fields\_of\_study** (*list*) – (optional) restrict results to given field-of-study list.

      * **fields** (*list*) – (optional) list of the snippet fields to be returned.

      * **publication\_date\_or\_year** (*str*) – (optional) restrict results to the given range of publication date in the format \<start\_date>:\<end\_date>.

      * **limit** (*int*) – (optional) maximum number of results to return (must be <= 1000, default 10).

    * Returns:

      list of snippet search results.

    * Return type:

      `List` of [`semanticscholar.Snippet.Snippet`](/en/latest/s2objects/Snippet.html#semanticscholar.Snippet.Snippet "semanticscholar.Snippet.Snippet")

  - *property*debu&#x67;*:bool*

    Enable/disable debug mode.

    * Type:

      `bool`

    Deprecated since version 0.8.4: Use Python’s standard logging in DEBUG level instead.

  * *property*retr&#x79;*:bool*

    Enable/disable retry mode.

    * Type:

      `bool`

  - *property*timeou&#x74;*:int*

    Timeout for server response in seconds.

    * Type:

      `int`

---

Powered by [curl.md](https://curl.md)

## cta.description

Narrow results with objective:

## cta.commands

| command                                                                                                           | description               |
|-------------------------------------------------------------------------------------------------------------------|---------------------------|
| curl.md https://semanticscholar.readthedocs.io/en/latest/mainclasses/semanticscholar.html --objective <objective> | focus on a specific topic |
