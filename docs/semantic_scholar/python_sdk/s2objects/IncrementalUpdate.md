---
title: IncrementalUpdate - semanticscholar
url: https://semanticscholar.readthedocs.io/en/latest/s2objects/IncrementalUpdate.html
site: semanticscholar.readthedocs.io
---

Back to top

[View this page](/en/latest/_sources/s2objects/IncrementalUpdate.rst.txt "View this page")

# IncrementalUpdate

* *class*semanticscholar.DatasetDiff.IncrementalUpdate(*data*)

  This class represents a single diff between two sequential releases of a dataset.

  Initialize IncrementalUpdate object.

  * Parameters:

    **data** (*dict*) – Dataset diff data from the API.

  - keys() → list

    Returns a list of all keys in the API response data.

    * Return type:

      `list`

  * *property*delete\_file&#x73;*:list*

    List of files that contain deletes from the dataset.

    * Type:

      `list` of `str`

  - *property*from\_releas&#x65;*:str*

    Baseline release for this diff.

    * Type:

      `str`

  * *property*raw\_dat&#x61;*:dict*

    The API response data in its original JSON structure, represented as a dict.

    * Type:

      `dict`

  - *property*to\_releas&#x65;*:str*

    Target release for this diff.

    * Type:

      `str`

  * *property*update\_file&#x73;*:list*

    List of files that contain updates to the dataset.

    * Type:

      `list` of `str`

---

Powered by [curl.md](https://curl.md)
