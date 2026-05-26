---
title: Dataset - semanticscholar
url: https://semanticscholar.readthedocs.io/en/latest/s2objects/Dataset.html
site: semanticscholar.readthedocs.io
---

Back to top

[View this page](/en/latest/_sources/s2objects/Dataset.rst.txt "View this page")

# Dataset

* *class*semanticscholar.Dataset.Dataset(*data*)

  This class represents a particular dataset in a release version of the Semantic Scholar Datasets.

  Initialize Dataset object.

  * Parameters:

    **data** (*dict*) – Dataset data from the API.

  - keys() → list

    Returns a list of all keys in the API response data.

    * Return type:

      `list`

  * *property*descriptio&#x6E;*:str*

    Dataset description.

    * Type:

      `str`

  - *property*file&#x73;*:list*

    List of file urls in the dataset.

    * Type:

      `list` of `str`

  * *property*nam&#x65;*:str*

    Dataset name.

    * Type:

      `str`

  - *property*raw\_dat&#x61;*:dict*

    The API response data in its original JSON structure, represented as a dict.

    * Type:

      `dict`

  * *property*readm&#x65;*:str*

    Dataset README.

    * Type:

      `str`

---

Powered by [curl.md](https://curl.md)
