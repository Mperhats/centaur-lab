---
title: Pagination - semanticscholar
url: https://semanticscholar.readthedocs.io/en/latest/pagination.html
site: semanticscholar.readthedocs.io
---

Back to top

[View this page](/en/latest/_sources/pagination.rst.txt "View this page")

# Pagination

* *class*semanticscholar.PaginatedResults.PaginatedResults

  This class abstracts paginated results from API search. You can just iterate over results regardless of the number of pages.

  * *async*async\_next\_page() → None

    Get next results

  - next\_page() → None

    Fetches the next page of results from the API and updates the current items list.

  * *property*item&#x73;*:list*

    Accumulated items across all fetched pages of results up to the current page.

    * Type:

      `list`

  - *property*nex&#x74;*:int*

    The position of the first item in the next page.

    * Type:

      `int`

  * *property*offse&#x74;*:int*

    The position of the first item in the current page.

    * Type:

      `int`

  - *property*raw\_dat&#x61;*:List\[dict]*

    The data from the current page of results in its original JSON structure, represented as a list of dict.

    * Type:

      `List` of `dict`

  * *property*tota&#x6C;*:int*

    Represents the total number of results in the query across all pages. From the official docs: “Because of the subtleties of finding partial phrase matches in different parts of the document, be cautious about interpreting the total field as a count of documents containing any particular word in the query.”

    * Type:

      `int`

---

Powered by [curl.md](https://curl.md)
