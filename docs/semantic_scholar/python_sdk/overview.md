---
title: semanticscholar - semanticscholar
url: https://semanticscholar.readthedocs.io/en/latest/overview.html
site: semanticscholar.readthedocs.io
---

Back to top

[View this page](/en/latest/_sources/overview.rst.txt "View this page")

# semanticscholar

Unofficial Python client library for [Semantic Scholar APIs](https://api.semanticscholar.org/).

## Main features

* Simplified access to the Semantic Scholar APIs

* Support for the Academic Graph, Recommendations, and Datasets APIs

* Typed responses

* Streamlined navigation of paginated responses

* Support for asynchronous requests

## Quickstart

### Installation

```
pip install semanticscholar
```

See the [Installation](/en/latest/install.html) page for more detailed installation instructions.

### Usage

```
# First, import the client from semanticscholar module
from semanticscholar import SemanticScholar

# You'll need an instance of the client to request data from the API
sch = SemanticScholar()

# Get a paper by its ID
paper = sch.get_paper('10.1093/mind/lix.236.433')

# Print the paper title
print(paper.title)
```

Output:

```
Computing Machinery and Intelligence
```

### What next?

* [Usage](/en/latest/usage.html) - See additional examples to learn how to use the library to fetch data from Semantic Scholar APIs.

* [Reference](/en/latest/reference.html) - Get the details of the classes and methods available in the library.

* [API Endpoints](/en/latest/api.html) - Check the supported SemanticScholar API endpoints and which methods implement them.

## Semantic Scholar API official docs and additional resources

If you have concerns or feedback specific to this library, feel free to [open an issue](https://github.com/danielnsilva/semanticscholar/issues). However, the official documentation provides additional resources for broader API-related issues.

* For details on Semantic Scholar APIs capabilities and limits, [go to the official documentation](https://api.semanticscholar.org/api-docs/graph).

* The [Frequently Asked Questions](https://www.semanticscholar.org/faq) page also provides helpful content if you need a better understanding of data fetched from Semantic Scholar services.

## Contributing

As a volunteer-maintained open-source project, contributions of all forms are welcome! For more information, see the [Contributing Guidelines](/en/latest/contributing.html).

Please make sure to understand our [Contributor Covenant Code of Conduct](/en/latest/code_of_conduct.html) before you contribute. TL;DR: Be nice and respectful!

## License

This project is licensed under the MIT License - see the [MIT License](/en/latest/license.html) file for details.

---

Powered by [curl.md](https://curl.md)
