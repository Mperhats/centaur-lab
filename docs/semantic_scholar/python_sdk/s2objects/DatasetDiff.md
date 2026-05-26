---
title: DatasetDiff - semanticscholar
url: https://semanticscholar.readthedocs.io/en/latest/s2objects/DatasetDiff.html
site: semanticscholar.readthedocs.io
---

Back to top

[View this page](/en/latest/_sources/s2objects/DatasetDiff.rst.txt "View this page")

# DatasetDiff

* *class*semanticscholar.DatasetDiff.DatasetDiff(*data*)

  This class represents the complete diff information for a dataset between two releases, including the dataset name, release information, and list of individual diffs.

  Initialize DatasetDiff object.

  * Parameters:

    **data** (*dict*) – Dataset diffs data from the API.

  - keys() → list

    Returns a list of all keys in the API response data.

    * Return type:

      `list`

  * *property*datase&#x74;*:str*

    Dataset name.

    * Type:

      `str`

  - *property*diff&#x73;*:list*

    List of diffs that need to be applied to bring the dataset at ‘start\_release’ up to date with ‘end\_release’.

    * Type:

      `list` of [`semanticscholar.DatasetDiff.IncrementalUpdate`](/en/latest/s2objects/IncrementalUpdate.html#semanticscholar.DatasetDiff.IncrementalUpdate "semanticscholar.DatasetDiff.IncrementalUpdate")

  * *property*end\_releas&#x65;*:str*

    Ending release, i.e. the release the client wants to update to.

    * Type:

      `str`

  - *property*raw\_dat&#x61;*:dict*

    The API response data in its original JSON structure, represented as a dict.

    * Type:

      `dict`

  * *property*start\_releas&#x65;*:str*

    Beginning release, i.e. the release currently held by the client.

    * Type:

      `str`

---

Powered by [curl.md](https://curl.md)
