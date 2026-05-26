---
title: Release - semanticscholar
url: https://semanticscholar.readthedocs.io/en/latest/s2objects/Release.html
site: semanticscholar.readthedocs.io
---

Back to top

[View this page](/en/latest/_sources/s2objects/Release.rst.txt "View this page")

# Release

* *class*semanticscholar.Release.Release(*data*)

  This class represents a release version of the Semantic Scholar Datasets.

  Initialize Release object.

  * Parameters:

    **data** (*dict*) – Release data from the API.

  - keys() → list

    Returns a list of all keys in the API response data.

    * Return type:

      `list`

  * *property*dataset&#x73;*:list*

    List of datasets in this release.

    * Type:

      `list` of [`semanticscholar.Dataset.Dataset`](/en/latest/s2objects/Dataset.html#semanticscholar.Dataset.Dataset "semanticscholar.Dataset.Dataset")

  - *property*raw\_dat&#x61;*:dict*

    The API response data in its original JSON structure, represented as a dict.

    * Type:

      `dict`

  * *property*readm&#x65;*:str*

    Release README.

    * Type:

      `str`

  - *property*release\_i&#x64;*:str*

    Release identifier.

    * Type:

      `str`

---

Powered by [curl.md](https://curl.md)
