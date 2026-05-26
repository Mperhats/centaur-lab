---
title: Snippet - semanticscholar
url: https://semanticscholar.readthedocs.io/en/latest/s2objects/Snippet.html
site: semanticscholar.readthedocs.io
---

Back to top

[View this page](/en/latest/_sources/s2objects/Snippet.rst.txt "View this page")

# Snippet

* *class*semanticscholar.Snippet.Snippet(*data:dict*)

  This class abstracts a snippet search result.

  * keys() → list

    Returns a list of all keys in the API response data.

    * Return type:

      `list`

  - *property*pape&#x72;*:[SnippetPaper](/en/latest/s2objects/SnippetPaper.html#semanticscholar.Snippet.SnippetPaper "semanticscholar.Snippet.SnippetPaper")*

    Basic paper data.

    * Type:

      [`semanticscholar.Snippet.SnippetPaper`](/en/latest/s2objects/SnippetPaper.html#semanticscholar.Snippet.SnippetPaper "semanticscholar.Snippet.SnippetPaper")

  * *property*raw\_dat&#x61;*:dict*

    The API response data in its original JSON structure, represented as a dict.

    * Type:

      `dict`

  - *property*scor&#x65;*:float*

    Relevance score of the snippet match.

    * Type:

      `float`

  * *property*snippe&#x74;*:[SnippetText](/en/latest/s2objects/SnippetText.html#semanticscholar.Snippet.SnippetText "semanticscholar.Snippet.SnippetText")*

    Snippet data.

    * Type:

      [`semanticscholar.Snippet.SnippetText`](/en/latest/s2objects/SnippetText.html#semanticscholar.Snippet.SnippetText "semanticscholar.Snippet.SnippetText")

  - *property*tex&#x74;*:str*

    Shortcut for snippet.text.

    * Type:

      `str`

---

Powered by [curl.md](https://curl.md)
