---
title: SnippetText - semanticscholar
url: https://semanticscholar.readthedocs.io/en/latest/s2objects/SnippetText.html
site: semanticscholar.readthedocs.io
---

Back to top

[View this page](/en/latest/_sources/s2objects/SnippetText.rst.txt "View this page")

# SnippetText

* *class*semanticscholar.Snippet.SnippetText(*data:dict*)

  Text snippet data returned by the snippet search endpoint.

  * keys() → list

    Returns a list of all keys in the API response data.

    * Return type:

      `list`

  - *property*annotation&#x73;*:dict*

    Annotations (sentences, refMentions).

    * Type:

      `dict`

  * *property*raw\_dat&#x61;*:dict*

    The API response data in its original JSON structure, represented as a dict.

    * Type:

      `dict`

  - *property*sectio&#x6E;*:str*

    Section of the paper where the snippet is located (only for body snippets).

    * Type:

      `str`

  * *property*snippet\_kin&#x64;*:str*

    Where the snippet is located: title, abstract, or body.

    * Type:

      `str`

  - *property*snippet\_offse&#x74;*:dict*

    Location of the snippet within the paper (start, end).

    * Type:

      `dict`

  * *property*tex&#x74;*:str*

    The direct quote or snippet text from the paper.

    * Type:

      `str`

---

Powered by [curl.md](https://curl.md)
