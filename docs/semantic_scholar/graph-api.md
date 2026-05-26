# Academic Graph API

_Version 1.0_

Base URL: `https://api.semanticscholar.org/graph/v1`

Fetch paper and author data from the Semantic Scholar Academic Graph (S2AG).
        <br><br>
        Some things to note:
        <ul>
        <li>If you are using an API key, it must be set in the header <code>x-api-key</code> (case-sensitive).</li>
        <li>We have two different IDs for a single paper:
          <ul>
            <li><code>paperId</code> - string - The primary way to identify papers when using our website or this API</li>
            <li><code>corpusId</code> - int64 - A second way to identify papers. Our datasets use corpusId when pointing to papers.</li>
          </ul>
        </li>
        <li>Other useful resources<ul>
        <li><a href="https://www.semanticscholar.org/product/api">Overview</a></li>
        <li><a href="https://github.com/allenai/s2-folks/">allenai/s2-folks</a></li>
        <li><a href="https://github.com/allenai/s2-folks/blob/main/FAQ.md">FAQ</a> in allenai/s2folks</li>
        </ul></li>

Default response media types: `application/json`

Default request media types: `application/json`

## Endpoints

### `POST /author/batch` — Get details for multiple authors at once

* Fields is a single-value string parameter, not a multi-value one.
* It is a query parameter, not to be submitted in the POST request's body.

In python:

    r = requests.post(
        'https://api.semanticscholar.org/graph/v1/author/batch',
        params={'fields': 'name,hIndex,citationCount'},
        json={"ids":["1741101", "1780531"]}
    )
    print(json.dumps(r.json(), indent=2))

    [
      {
        "authorId": "1741101",
        "name": "Oren Etzioni",
        "citationCount": 34803,
        "hIndex": 86
      },
      {
        "authorId": "1780531",
        "name": "Daniel S. Weld",
        "citationCount": 35526,
        "hIndex": 89
      }
    ]

Other Examples:
<ul>
    <li><code>https://api.semanticscholar.org/graph/v1/author/batch</code></li>
    <ul>
        <li><code>{"ids":["1741101", "1780531", "48323507"]}</code></li>
        <li>Returns details for 3 authors.</li>
        <li>Each author returns the field authorId and name if no other fields are specified.</li>
    </ul>
    <li><code>https://api.semanticscholar.org/graph/v1/author/batch?fields=url,name,paperCount,papers,papers.title,papers.openAccessPdf</code></li>
    <ul>
        <li><code>{"ids":["1741101", "1780531", "48323507"]}</code></li>
        <li>Returns authorID, url, name, paperCount, and list of papers for 3 authors.</li>
        <li>Each paper has its paperID, title, and link if available.</li>
    </ul>
</ul>
<br>
Limitations:
<ul>
    <li>Can only process 1,000 author ids at a time.</li>
    <li>Can only return up to 10 MB of data at a time.</li>
</ul>

_Tags: Author Data_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `fields` | query | no | string | A comma-separated list of the fields to be returned. See the contents of Response Schema below for a list of all available fields that can be returned. The <code>authorId</code> field is always returned. If the fields parameter is omitted, only the <code>authorId</code> and <code>name</code> will be returned. <p>Use a period (“.”) for subfields of <code>papers</code>.<br><br> Examples: <ul>     <li><code>fields=name,affiliations,papers</code></li>     <li><code>fields=url,papers.year,papers.authors</code></li> </ul> |

**Request body**

- Type: [`AuthorIdList`](#schema-authoridlist)

**Responses**

- **400** — Bad query parameters
  - Body: [`Error400`](#schema-error400)
- **200** — List of authors with default or requested fields
  - Body: [`AuthorWithPapers`](#schema-authorwithpapers)

---

### `GET /author/search` — Search for authors by name

Specifying <code>papers</code> fields in the request will return all papers linked to each author in the results. Set a <code>limit</code> on the search results to reduce output size and latency.<br><br>
Examples:
<ul>
    <li><code>https://api.semanticscholar.org/graph/v1/author/search?query=adam+smith</code></li>
    <ul>
        <li>Returns with total=490, offset=0, next=100, and data is a list of 100 authors.</li>
        <li>Each author has their authorId and name. </li>
    </ul>
    <li><code>https://api.semanticscholar.org/graph/v1/author/search?query=adam+smith&fields=name,url,papers.title,papers.year&limit=5</code></li>
    <ul>
        <li>Returns with total=490, offset=0, next=5, and data is a list of 5 authors.</li>
        <li>Each author has authorId, name, url, and a list of their papers title and year.</li>
    </ul>
    <li><code>https://api.semanticscholar.org/graph/v1/author/search?query=totalGarbageNonsense</code></li>
    <ul>
        <li>Returns with total = 0, offset=0, and data is a list of 0 author.</li>
    </ul>
    <br>
    Limitations:
<ul>
    <li>Can only return up to 10 MB of data at a time.</li>
</ul>

_Tags: Author Data_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `offset` | query | no | integer | Used for pagination. When returning a list of results, start with the element at this position in the list. |
| `limit` | query | no | integer | The maximum number of results to return.<br> Must be <= 1000 |
| `fields` | query | no | string | A comma-separated list of the fields to be returned. See the contents of the <code>data</code> array in Response Schema below for a list of all available fields that can be returned. The <code>authorId</code> field is always returned. If the fields parameter is omitted, only the <code>authorId</code> and <code>name</code> will be returned. <p>Use a period (“.”) for subfields of <code>papers</code>.<br><br> Examples: <ul>     <li><code>fields=name,affiliations,papers</code></li>     <li><code>fields=url,papers.year,papers.authors</code></li> </ul> |
| `query` | query | yes | string | A plain-text search query string. * No special query syntax is supported. * Hyphenated query terms yield no matches (replace it with space to find matches) |

**Responses**

- **400** — Bad query parameters
  - Body: [`Error400`](#schema-error400)
- **200** — Batch of authors with default or requested fields
  - Body: [`AuthorSearchBatch`](#schema-authorsearchbatch)

---

### `GET /author/{author_id}` — Details about an author

Examples:
<ul>
    <li><code>https://api.semanticscholar.org/graph/v1/author/1741101</code></li>
    <ul>
        <li>Returns the author's authorId and name.</li>
    </ul>
    <li><code>https://api.semanticscholar.org/graph/v1/author/1741101?fields=url,papers</code></li>
    <ul>
        <li>Returns the author's authorId, url, and list of papers.  </li>
        <li>Each paper has its paperId plus its title.</li>
    </ul>
    <li><code>https://api.semanticscholar.org/graph/v1/author/1741101?fields=url,papers.abstract,papers.authors</code></li>
    <ul>
        <li>Returns the author's authorId, url, and list of papers.  </li>
        <li>Each paper has its paperId, abstract, and list of authors.</li>
        <li>In that list of authors, each author has their authorId and name.</li>
    </ul>
    <br>
    Limitations:
    <ul>
        <li>Can only return up to 10 MB of data at a time.</li>
    </ul>
</ul>

_Tags: Author Data_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `fields` | query | no | string | A comma-separated list of the fields to be returned. See the contents of Response Schema below for a list of all available fields that can be returned. The <code>authorId</code> field is always returned. If the fields parameter is omitted, only the <code>authorId</code> and <code>name</code> will be returned. <p>Use a period (“.”) for subfields of <code>papers</code>.<br><br> Examples: <ul>     <li><code>fields=name,affiliations,papers</code></li>     <li><code>fields=url,papers.year,papers.authors</code></li> </ul> |
| `author_id` | path | yes | string |  |

**Responses**

- **404** — Bad paper id
  - Body: [`Error404`](#schema-error404)
- **400** — Bad query parameters
  - Body: [`Error400`](#schema-error400)
- **200** — Author with default or requested fields
  - Body: [`AuthorWithPapers`](#schema-authorwithpapers)

---

### `GET /author/{author_id}/papers` — Details about an author's papers

Fetch the papers of an author in batches.<br>
Only retrieves the most recent 10,000 citations/references for papers belonging to the batch.<br>
To retrieve the full set of citations for a paper,
use the /paper/{paper_id}/citations endpoint
<br><br>
Examples:
<ul>
    <li><code>https://api.semanticscholar.org/graph/v1/author/1741101/papers</code></li>
    <ul>
        <li>Return with offset=0, and data is a list of the first 100 papers.</li>
        <li>Each paper has its paperId and title.</li>
    </ul>
    <li><code>https://api.semanticscholar.org/graph/v1/author/1741101/papers?fields=url,year,authors&limit=2</code></li>
    <ul>
        <li>Returns with offset=0, next=2, and data is a list of 2 papers.</li>
        <li>Each paper has its paperId, url, year, and list of authors.</li>
        <li>Each author has their authorId and name.</li>
    </ul>
    <li><code>https://api.semanticscholar.org/graph/v1/author/1741101/papers?fields=citations.authors&offset=260</code></li>
    <ul>
        <li>Returns with offset=260, and data is a list of the last 4 papers.</li>
        <li>Each paper has its paperId and a list of citations.</li>
        <li>Each citation has its paperId and a list of authors.</li>
        <li>Each author has their authorId and name.</li>
    </ul>
</ul>

_Tags: Author Data_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `publicationDateOrYear` | query | no | string | Restricts results to the given range of publication dates or years (inclusive). Accepts the format <code>&lt;startDate&gt;:&lt;endDate&gt;</code> with each date in <code>YYYY-MM-DD</code> format.  <br> <br> Each term is optional, allowing for specific dates, fixed ranges, or open-ended ranges. In addition, prefixes are supported as a shorthand, e.g. <code>2020-06</code> matches all dates in June 2020. <br> <br> Specific dates are not known for all papers, so some records returned with this filter will have a <code>null</code> value for </code>publicationDate</code>. <code>year</code>, however, will always be present. For records where a specific publication date is not known, they will be treated as if published on January 1st of their publication year. <br> <br> Examples: <ul>     <li><code>2019-03-05</code> on March 5th, 2019</li>     <li><code>2019-03</code> during March 2019</li>     <li><code>2019</code> during 2019</li>     <li><code>2016-03-05:2020-06-06</code> as early as March 5th, 2016 or as late as June 6th, 2020</li>     <li><code>1981-08-25:</code> on or after August 25th, 1981</li>     <li><code>:2015-01</code> before or on January 31st, 2015</li>     <li><code>2015:2020</code> between January 1st, 2015 and December 31st, 2020</li> </ul> |
| `offset` | query | no | integer | Used for pagination. When returning a list of results, start with the element at this position in the list. |
| `limit` | query | no | integer | The maximum number of results to return.<br> Must be <= 1000 |
| `fields` | query | no | string | A comma-separated list of the fields to be returned. See the contents of the <code>data</code> array in Response Schema below for a list of all available fields that can be returned. The <code>paperId</code> field is always returned. If the fields parameter is omitted, only the <code>paperId</code> and <code>title</code> will be returned. To fetch more references or citations per paper, reduce the number of papers in the batch with <code>limit=</code>. <p>Use a period (“.”) for subfields of <code>citations</code> and <code>references</code>.<br><br> Examples: <ul>     <li><code>fields=title,fieldsOfStudy,references</code></li>     <li><code>fields=abstract,citations.url,citations.venue</code></li> </ul> |
| `author_id` | path | yes | string |  |

**Responses**

- **404** — Bad paper id
  - Body: [`Error404`](#schema-error404)
- **400** — Bad query parameters
  - Body: [`Error400`](#schema-error400)
- **200** — List of papers with default or requested fields
  - Body: [`AuthorPaperBatch`](#schema-authorpaperbatch)

---

### `GET /paper/autocomplete` — Suggest paper query completions

To support interactive query-completion, return minimal information about papers matching a partial query

Example:
<code>https://api.semanticscholar.org/graph/v1/paper/autocomplete?query=semanti</code>

_Tags: Paper Data_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `query` | query | yes | string | Plain-text partial query string. Will be truncated to first 100 characters. |

**Responses**

- **400** — Bad query parameters
  - Body: [`Error400`](#schema-error400)
- **200** — Batch of papers with default or requested fields
  - Body: [`PaperAutocomplete`](#schema-paperautocomplete)

---

### `POST /paper/batch` — Get details for multiple papers at once

* Fields is a single-value string parameter, not a multi-value one.
* It is a query parameter, not to be submitted in the POST request's body.

In python:

    r = requests.post(
        'https://api.semanticscholar.org/graph/v1/paper/batch',
        params={'fields': 'referenceCount,citationCount,title'},
        json={"ids": ["649def34f8be52c8b66281af98ae884c09aef38b", "ARXIV:2106.15928"]}
    )
    print(json.dumps(r.json(), indent=2))

    [
      {
        "paperId": "649def34f8be52c8b66281af98ae884c09aef38b",
        "title": "Construction of the Literature Graph in Semantic Scholar",
        "referenceCount": 27,
        "citationCount": 299
      },
      {
        "paperId": "f712fab0d58ae6492e3cdfc1933dae103ec12d5d",
        "title": "Reinfection and low cross-immunity as drivers of epidemic resurgence under high seroprevalence: a model-based approach with application to Amazonas, Brazil",
        "referenceCount": 13,
        "citationCount": 0
      }
    ]

Other Examples:
<ul>
    <li><code>https://api.semanticscholar.org/graph/v1/paper/batch</code></li>
    <ul>
        <li><code>{"ids":["649def34f8be52c8b66281af98ae884c09aef38b", "ARXIV:2106.15928"]}</code></li>
        <li>Returns details for 2 papers.</li>
        <li>Each paper has its paperId and title.  </li>
    </ul>
    <li><code>https://api.semanticscholar.org/graph/v1/paper/batch?fields=title,isOpenAccess,openAccessPdf,authors</code></li>
    <ul>
        <li><code>{"ids":["649def34f8be52c8b66281af98ae884c09aef38b", "ARXIV:2106.15928"]}</code></li>
        <li>Returns all requested info plus paper IDs for 2 papers.</li>
    </ul>
</ul>
<br>
Limitations:
<ul>
    <li>Can only process 500 paper ids at a time.</li>
    <li>Can only return up to 10 MB of data at a time.</li>
    <li>Can only return up to 9999 citations at a time.</li>
    <li>For a list of supported IDs reference the "Details about a paper" endpoint.</li>
</ul>

_Tags: Paper Data_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `fields` | query | no | string | A comma-separated list of the fields to be returned. See the contents of Response Schema below for a list of all available fields that can be returned. The <code>paperId</code> field is always returned. If the fields parameter is omitted, only the <code>paperId</code> and <code>title</code> will be returned. <p>Use a period (“.”) for fields that have version numbers or subfields, such as the <code>embedding</code>, <code>authors</code>, <code>citations</code>, and <code>references</code> fields: <ul>     <li>When requesting <code>authors</code>, the <code>authorId</code> and <code>name</code> subfields are returned by default. To request other subfields, use the format <code>author.url,author.paperCount</code>, etc. See the Response Schema below for available subfields.</li>     <li>When requesting <code>citations</code> and <code>references</code>, the <code>paperId</code> and <code>title</code> subfields are returned by default. To request other subfields, use the format <code>citations.title,citations.abstract</code>, etc. See the Response Schema below for available subfields.</li>     <li>When requesting <code>embedding</code>, the default <a href="https://github.com/allenai/specter">Spector embedding version</a> is v1. Specify <code>embedding.specter_v2</code> to select v2 embeddings.</li> </ul> Examples: <ul>     <li><code>fields=title,url</code></li>     <li><code>fields=title,embedding.specter_v2</code></li>     <li><code>fields=title,authors,citations.title,citations.abstract</code></li> </ul> |

**Request body**

- Type: [`PaperBatch`](#schema-paperbatch)

**Responses**

- **400** — Bad query parameters
  - Body: [`Error400`](#schema-error400)
- **200** — List of papers with default or requested fields
  - Body: [`FullPaper`](#schema-fullpaper)

---

### `GET /paper/search` — Paper relevance search

Examples:
<ul>
  <li><code>https://api.semanticscholar.org/graph/v1/paper/search?query=covid+vaccination&offset=100&limit=3</code></li>
  <ul>
    <li>Returns with total=576278, offset=100, next=103, and data is a list of 3 papers.</li>
    <li>Each paper has its paperId and title.  </li>
  </ul>
  <li><code>https://api.semanticscholar.org/graph/v1/paper/search?query=covid&fields=url,abstract,authors</code></li>
  <ul>
    <li>Returns with total=639637, offset=0, next=100, and data is a list of 100 papers.</li>
    <li>Each paper has paperId, url, abstract, and a list of authors.</li>
    <li>Each author under that list has authorId and name.</li>
  </ul>
  <li><code>https://api.semanticscholar.org/graph/v1/paper/search?query=totalGarbageNonsense</code></li>
  <ul>
    <li>Returns with total=0, offset=0, and data is a list of 0 papers.</li>
  </ul>
  <li><code>https://api.semanticscholar.org/graph/v1/paper/search?query=covid&year=2020-2023&openAccessPdf&fieldsOfStudy=Physics,Philosophy&fields=title,year,authors</code></li>
  <ul>
    <li>Returns with total=8471, offset=0, next=10, and data is a list of 10 papers. </li>
    <li>Filters to include only papers published between 2020-2023.</li>
    <li>Filters to include only papers with open access PDFs.</li>
    <li>Filters to include only papers that have a field of study either matching Physics or Philosophy.</li>
    <li>Each paper has the fields paperId, title, year, and authors.</li>
  </ul>
  <br>
    Limitations:
    <ul>
        <li>Can only return up to 1,000 relevance-ranked results. For larger queries, see "/search/bulk" or the Datasets API.</li>
        <li>Can only return up to 10 MB of data at a time.</li>
    </ul>
</ul>

_Tags: Paper Data_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `query` | query | yes | string | A plain-text search query string. * No special query syntax is supported. * Hyphenated query terms yield no matches (replace it with space to find matches)  See our <a href="https://medium.com/ai2-blog/building-a-better-search-engine-for-semantic-scholar-ea23a0b661e7">blog post</a> for a description of our search relevance algorithm.  Example: <code>graph/v1/paper/search?query=generative ai</code> |
| `fields` | query | no | string | A comma-separated list of the fields to be returned. See the contents of the <code>data</code> array in Response Schema below for a list of all available fields that can be returned. The <code>paperId</code> field is always returned. If the fields parameter is omitted, only the <code>paperId</code> and <code>title</code> will be returned. <p>Use a period (“.”) for fields that have version numbers or subfields, such as the <code>embedding</code>, <code>authors</code>, <code>citations</code>, and <code>references</code> fields: <ul>     <li>When requesting <code>authors</code>, the <code>authorId</code> and <code>name</code> subfields are returned by default. To request other subfields, use the format <code>author.url,author.paperCount</code>, etc. See the Response Schema below for available subfields.</li>     <li>When requesting <code>citations</code> and <code>references</code>, the <code>paperId</code> and <code>title</code> subfields are returned by default. To request other subfields, use the format <code>citations.title,citations.abstract</code>, etc. See the Response Schema below for available subfields.</li>     <li>When requesting <code>embedding</code>, the default <a href="https://github.com/allenai/specter">Spector embedding version</a> is v1. Specify <code>embedding.specter_v2</code> to select v2 embeddings.</li> </ul> Examples: <ul>     <li><code>fields=title,url</code></li>     <li><code>fields=title,embedding.specter_v2</code></li>     <li><code>fields=title,authors,citations.title,citations.abstract</code></li> </ul> |
| `publicationTypes` | query | no | string | Restricts results to any of the following paper publication types: <ul>     <li>Review</li>     <li>JournalArticle</li>     <li>CaseReport</li>     <li>ClinicalTrial</li>     <li>Conference</li>     <li>Dataset</li>     <li>Editorial</li>     <li>LettersAndComments</li>     <li>MetaAnalysis</li>     <li>News</li>     <li>Study</li>     <li>Book</li>     <li>BookSection</li> </ul>  Use a comma-separated list to include papers with any of the listed publication types. <br><br> Example: <code>Review,JournalArticle</code> will return papers with publication types Review and/or JournalArticle. |
| `openAccessPdf` | query | no | string | Restricts results to only include papers with a public PDF. This parameter does not accept any values. |
| `minCitationCount` | query | no | string | Restricts results to only include papers with the minimum number of citations. <br> <br> Example: <code>minCitationCount=200</code> |
| `publicationDateOrYear` | query | no | string | Restricts results to the given range of publication dates or years (inclusive). Accepts the format <code>&lt;startDate&gt;:&lt;endDate&gt;</code> with each date in <code>YYYY-MM-DD</code> format.  <br> <br> Each term is optional, allowing for specific dates, fixed ranges, or open-ended ranges. In addition, prefixes are supported as a shorthand, e.g. <code>2020-06</code> matches all dates in June 2020. <br> <br> Specific dates are not known for all papers, so some records returned with this filter will have a <code>null</code> value for </code>publicationDate</code>. <code>year</code>, however, will always be present. For records where a specific publication date is not known, they will be treated as if published on January 1st of their publication year. <br> <br> Examples: <ul>     <li><code>2019-03-05</code> on March 5th, 2019</li>     <li><code>2019-03</code> during March 2019</li>     <li><code>2019</code> during 2019</li>     <li><code>2016-03-05:2020-06-06</code> as early as March 5th, 2016 or as late as June 6th, 2020</li>     <li><code>1981-08-25:</code> on or after August 25th, 1981</li>     <li><code>:2015-01</code> before or on January 31st, 2015</li>     <li><code>2015:2020</code> between January 1st, 2015 and December 31st, 2020</li> </ul> |
| `year` | query | no | string | Restricts results to the given publication year or range of years (inclusive). <br> <br> Examples: <ul>     <li><code>2019</code> in 2019</li>     <li><code>2016-2020</code> as early as 2016 or as late as 2020</li>     <li><code>2010-</code> during or after 2010</li>     <li><code>-2015</code> before or during 2015</li> </ul> |
| `venue` | query | no | string | Restricts results to papers published in the given venues, formatted as a comma-separated list. <br><br> Input could also be an ISO4 abbreviation. Examples include: <ul>     <li>Nature</li>     <li>New England Journal of Medicine</li>     <li>Radiology</li>     <li>N. Engl. J. Med.</li> </ul>  Example: <code>Nature,Radiology</code> will return papers from venues Nature and/or Radiology. |
| `fieldsOfStudy` | query | no | string | Restricts results to papers in the given fields of study, formatted as a comma-separated list: <ul> <li>Computer Science</li> <li>Medicine</li> <li>Chemistry</li> <li>Biology</li> <li>Materials Science</li> <li>Physics</li> <li>Geology</li> <li>Psychology</li> <li>Art</li> <li>History</li> <li>Geography</li> <li>Sociology</li> <li>Business</li> <li>Political Science</li> <li>Economics</li> <li>Philosophy</li> <li>Mathematics</li> <li>Engineering</li> <li>Environmental Science</li> <li>Agricultural and Food Sciences</li> <li>Education</li> <li>Law</li> <li>Linguistics</li> </ul>  Example: <code>Physics,Mathematics</code> will return papers with either Physics or Mathematics in their list of fields-of-study. |
| `offset` | query | no | integer | Used for pagination. When returning a list of results, start with the element at this position in the list. |
| `limit` | query | no | integer | The maximum number of results to return.<br> Must be <= 100 |

**Responses**

- **400** — Bad query parameters
  - Body: [`Error400`](#schema-error400)
- **200** — Batch of papers with default or requested fields
  - Body: [`PaperRelevanceSearchBatch`](#schema-paperrelevancesearchbatch)

---

### `GET /paper/search/bulk` — Paper bulk search

Behaves similarly to <code>/paper/search</code>, but is intended for bulk retrieval of basic paper data without search relevance:
<ul>
<li>Text query is optional and supports boolean logic for document matching.</li>
<li>Papers can be filtered using various criteria.</li>
<li>Up to 1,000 papers will be returned in each call.</li>
<li>If there are more matching papers, a continuation "token" will be present.</li>
<li>The query can be repeated with the token param added to efficiently continue fetching matching papers.</li>
</ul>
<br>
Returns a structure with an estimated total matches, batch of matching papers, and a continuation token if more results are available.
<br>
Limitations:
<ul>
<li>Nested paper data, such as citations, references, etc, is not available via this method.</li>
<li>Up to 10,000,000 papers can be fetched via this method. For larger needs, please use the <a href="datasets/">Datasets API</a> to retrieve full copies of the corpus.</li>
</ul>

_Tags: Paper Data_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `query` | query | yes | string | Text query that will be matched against the paper's title and abstract. All terms are stemmed in English. By default all terms in the query must be present in the paper.  The match query supports the following syntax: <ul> <li><code>+</code> for AND operation</li> <li><code>\|</code> for OR operation</li> <li><code>-</code> negates a term </li> <li><code>"</code> collects terms into a phrase</li> <li><code>*</code> can be used to match a prefix</li>     <li><code>(</code> and <code>)</code> for precedence</li> <li><code>~N</code> after a word matches within the edit distance of N (Defaults to 2 if N is omitted)</li> <li><code>~N</code> after a phrase matches with the phrase terms separated up to N terms apart (Defaults to 2 if N is omitted)</li> </ul>  Examples: <ul>     <li><code>fish ladder</code> matches papers that contain "fish" and "ladder"</li>     <li><code>fish -ladder</code> matches papers that contain "fish" but not "ladder"</li>     <li><code>fish \| ladder</code> matches papers that contain "fish" or "ladder"</li>     <li><code>"fish ladder"</code> matches papers that contain the phrase "fish ladder"</li>     <li><code>(fish ladder) \| outflow</code> matches papers that contain "fish" and "ladder" OR "outflow"</li>     <li><code>fish~</code> matches papers that contain "fish", "fist", "fihs", etc. </li>     <li><code>"fish ladder"~3</code> mathces papers that contain the phrase "fish ladder" or "fish is on a ladder"</li> </ul> |
| `token` | query | no | string | Used for pagination. This string token is provided when the original query returns, and is used to fetch the next batch of papers. Each call will return a new token. |
| `fields` | query | no | string | A comma-separated list of the fields to be returned. See the contents of the <code>data</code> array in Response Schema below for a list of all available fields that can be returned.<br><br> The <code>paperId</code> field is always returned. If the fields parameter is omitted, only the <code>paperId</code> and <code>title</code> will be returned.<br><br> Examples: <code>https://api.semanticscholar.org/graph/v1/paper/search/bulk?query=covid&fields=venue,s2FieldsOfStudy</code>  |
| `sort` | query | no | string | Provides the option to sort the results by the following fields: <ul>     <li><code>paperId</code></li>     <li><code>publicationDate</code></li>     <li><code>citationCount</code></li> </ul> Uses the format <code>field:order</code>. Ties are broken by <code>paperId</code>. The default field is <code>paperId</code> and the default order is asc. Records for which the sort value are not defined will appear at the end of sort, regardless of asc/desc order. <br> <br> Examples: <ul>     <li><code>publicationDate:asc</code> - return oldest papers first.</li>     <li><code>citationCount:desc</code> - return most highly-cited papers first.</li>     <li><code>paperId</code> - return papers in ID order, low-to-high.</li> </ul> <br> Please be aware that if the relevant data changes while paging through results, records can be returned in an unexpected way. The default <code>paperId</code> sort avoids this edge case. |
| `publicationTypes` | query | no | string | Restricts results to any of the following paper publication types: <ul>     <li>Review</li>     <li>JournalArticle</li>     <li>CaseReport</li>     <li>ClinicalTrial</li>     <li>Conference</li>     <li>Dataset</li>     <li>Editorial</li>     <li>LettersAndComments</li>     <li>MetaAnalysis</li>     <li>News</li>     <li>Study</li>     <li>Book</li>     <li>BookSection</li> </ul>  Use a comma-separated list to include papers with any of the listed publication types. <br><br> Example: <code>Review,JournalArticle</code> will return papers with publication types Review and/or JournalArticle. |
| `openAccessPdf` | query | no | string | Restricts results to only include papers with a public PDF. This parameter does not accept any values. |
| `minCitationCount` | query | no | string | Restricts results to only include papers with the minimum number of citations. <br> <br> Example: <code>minCitationCount=200</code> |
| `publicationDateOrYear` | query | no | string | Restricts results to the given range of publication dates or years (inclusive). Accepts the format <code>&lt;startDate&gt;:&lt;endDate&gt;</code> with each date in <code>YYYY-MM-DD</code> format.  <br> <br> Each term is optional, allowing for specific dates, fixed ranges, or open-ended ranges. In addition, prefixes are supported as a shorthand, e.g. <code>2020-06</code> matches all dates in June 2020. <br> <br> Specific dates are not known for all papers, so some records returned with this filter will have a <code>null</code> value for </code>publicationDate</code>. <code>year</code>, however, will always be present. For records where a specific publication date is not known, they will be treated as if published on January 1st of their publication year. <br> <br> Examples: <ul>     <li><code>2019-03-05</code> on March 5th, 2019</li>     <li><code>2019-03</code> during March 2019</li>     <li><code>2019</code> during 2019</li>     <li><code>2016-03-05:2020-06-06</code> as early as March 5th, 2016 or as late as June 6th, 2020</li>     <li><code>1981-08-25:</code> on or after August 25th, 1981</li>     <li><code>:2015-01</code> before or on January 31st, 2015</li>     <li><code>2015:2020</code> between January 1st, 2015 and December 31st, 2020</li> </ul> |
| `year` | query | no | string | Restricts results to the given publication year or range of years (inclusive). <br> <br> Examples: <ul>     <li><code>2019</code> in 2019</li>     <li><code>2016-2020</code> as early as 2016 or as late as 2020</li>     <li><code>2010-</code> during or after 2010</li>     <li><code>-2015</code> before or during 2015</li> </ul> |
| `venue` | query | no | string | Restricts results to papers published in the given venues, formatted as a comma-separated list. <br><br> Input could also be an ISO4 abbreviation. Examples include: <ul>     <li>Nature</li>     <li>New England Journal of Medicine</li>     <li>Radiology</li>     <li>N. Engl. J. Med.</li> </ul>  Example: <code>Nature,Radiology</code> will return papers from venues Nature and/or Radiology. |
| `fieldsOfStudy` | query | no | string | Restricts results to papers in the given fields of study, formatted as a comma-separated list: <ul> <li>Computer Science</li> <li>Medicine</li> <li>Chemistry</li> <li>Biology</li> <li>Materials Science</li> <li>Physics</li> <li>Geology</li> <li>Psychology</li> <li>Art</li> <li>History</li> <li>Geography</li> <li>Sociology</li> <li>Business</li> <li>Political Science</li> <li>Economics</li> <li>Philosophy</li> <li>Mathematics</li> <li>Engineering</li> <li>Environmental Science</li> <li>Agricultural and Food Sciences</li> <li>Education</li> <li>Law</li> <li>Linguistics</li> </ul>  Example: <code>Physics,Mathematics</code> will return papers with either Physics or Mathematics in their list of fields-of-study. |

**Responses**

- **400** — Bad query parameters
  - Body: [`Error400`](#schema-error400)
- **200** — Batch of papers with default or requested fields
  - Body: [`PaperBulkSearchBatch`](#schema-paperbulksearchbatch)

---

### `GET /paper/search/match` — Paper title search

Behaves similarly to <code>/paper/search</code>, but is intended for retrieval of a single paper based on closest title match to given query.
Examples:
<ul>
  <li><code>https://api.semanticscholar.org/graph/v1/paper/search/match?query=Construction of the Literature Graph in Semantic Scholar</code></li>
  <ul>
    <li>Returns a single paper that is the closest title match.</li>
    <li>Each paper has its paperId, title, and matchScore as well as any other requested fields.</li>
  </ul>
  <li><code>https://api.semanticscholar.org/graph/v1/paper/search/match?query=totalGarbageNonsense</code></li>
  <ul>
    <li>Returns with a 404 error and a "Title match not found" message.</li>
  </ul>
</ul>
  <br>
    Limitations:
    <ul>
        <li>Will only return the single highest match result.</li>
    </ul>
</ul>

_Tags: Paper Data_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `query` | query | yes | string | A plain-text search query string. * No special query syntax is supported.  See our <a href="https://medium.com/ai2-blog/building-a-better-search-engine-for-semantic-scholar-ea23a0b661e7">blog post</a> for a description of our search relevance algorithm.  |
| `fields` | query | no | string | A comma-separated list of the fields to be returned. See the contents of the <code>data</code> array in Response Schema below for a list of all available fields that can be returned. The <code>paperId</code> field is always returned. If the fields parameter is omitted, only the <code>paperId</code> and <code>title</code> will be returned. <p>Use a period (“.”) for fields that have version numbers or subfields, such as the <code>embedding</code>, <code>authors</code>, <code>citations</code>, and <code>references</code> fields: <ul>     <li>When requesting <code>authors</code>, the <code>authorId</code> and <code>name</code> subfields are returned by default. To request other subfields, use the format <code>author.url,author.paperCount</code>, etc. See the Response Schema below for available subfields.</li>     <li>When requesting <code>citations</code> and <code>references</code>, the <code>paperId</code> and <code>title</code> subfields are returned by default. To request other subfields, use the format <code>citations.title,citations.abstract</code>, etc. See the Response Schema below for available subfields.</li>     <li>When requesting <code>embedding</code>, the default <a href="https://github.com/allenai/specter">Spector embedding version</a> is v1. Specify <code>embedding.specter_v2</code> to select v2 embeddings.</li> </ul> Examples: <ul>     <li><code>fields=title,url</code></li>     <li><code>fields=title,embedding.specter_v2</code></li>     <li><code>fields=title,authors,citations.title,citations.abstract</code></li> </ul> |
| `publicationTypes` | query | no | string | Restricts results to any of the following paper publication types: <ul>     <li>Review</li>     <li>JournalArticle</li>     <li>CaseReport</li>     <li>ClinicalTrial</li>     <li>Conference</li>     <li>Dataset</li>     <li>Editorial</li>     <li>LettersAndComments</li>     <li>MetaAnalysis</li>     <li>News</li>     <li>Study</li>     <li>Book</li>     <li>BookSection</li> </ul>  Use a comma-separated list to include papers with any of the listed publication types. <br><br> Example: <code>Review,JournalArticle</code> will return papers with publication types Review and/or JournalArticle. |
| `openAccessPdf` | query | no | string | Restricts results to only include papers with a public PDF. This parameter does not accept any values. |
| `minCitationCount` | query | no | string | Restricts results to only include papers with the minimum number of citations. <br> <br> Example: <code>minCitationCount=200</code> |
| `publicationDateOrYear` | query | no | string | Restricts results to the given range of publication dates or years (inclusive). Accepts the format <code>&lt;startDate&gt;:&lt;endDate&gt;</code> with each date in <code>YYYY-MM-DD</code> format.  <br> <br> Each term is optional, allowing for specific dates, fixed ranges, or open-ended ranges. In addition, prefixes are supported as a shorthand, e.g. <code>2020-06</code> matches all dates in June 2020. <br> <br> Specific dates are not known for all papers, so some records returned with this filter will have a <code>null</code> value for </code>publicationDate</code>. <code>year</code>, however, will always be present. For records where a specific publication date is not known, they will be treated as if published on January 1st of their publication year. <br> <br> Examples: <ul>     <li><code>2019-03-05</code> on March 5th, 2019</li>     <li><code>2019-03</code> during March 2019</li>     <li><code>2019</code> during 2019</li>     <li><code>2016-03-05:2020-06-06</code> as early as March 5th, 2016 or as late as June 6th, 2020</li>     <li><code>1981-08-25:</code> on or after August 25th, 1981</li>     <li><code>:2015-01</code> before or on January 31st, 2015</li>     <li><code>2015:2020</code> between January 1st, 2015 and December 31st, 2020</li> </ul> |
| `year` | query | no | string | Restricts results to the given publication year or range of years (inclusive). <br> <br> Examples: <ul>     <li><code>2019</code> in 2019</li>     <li><code>2016-2020</code> as early as 2016 or as late as 2020</li>     <li><code>2010-</code> during or after 2010</li>     <li><code>-2015</code> before or during 2015</li> </ul> |
| `venue` | query | no | string | Restricts results to papers published in the given venues, formatted as a comma-separated list. <br><br> Input could also be an ISO4 abbreviation. Examples include: <ul>     <li>Nature</li>     <li>New England Journal of Medicine</li>     <li>Radiology</li>     <li>N. Engl. J. Med.</li> </ul>  Example: <code>Nature,Radiology</code> will return papers from venues Nature and/or Radiology. |
| `fieldsOfStudy` | query | no | string | Restricts results to papers in the given fields of study, formatted as a comma-separated list: <ul> <li>Computer Science</li> <li>Medicine</li> <li>Chemistry</li> <li>Biology</li> <li>Materials Science</li> <li>Physics</li> <li>Geology</li> <li>Psychology</li> <li>Art</li> <li>History</li> <li>Geography</li> <li>Sociology</li> <li>Business</li> <li>Political Science</li> <li>Economics</li> <li>Philosophy</li> <li>Mathematics</li> <li>Engineering</li> <li>Environmental Science</li> <li>Agricultural and Food Sciences</li> <li>Education</li> <li>Law</li> <li>Linguistics</li> </ul>  Example: <code>Physics,Mathematics</code> will return papers with either Physics or Mathematics in their list of fields-of-study. |

**Responses**

- **404** — No title match
  - Body: [`Error404`](#schema-error404)
- **400** — Bad query parameters
  - Body: [`Error400`](#schema-error400)
- **200** — Best Title match paper with default or requested fields
  - Body: [`PaperMatch`](#schema-papermatch)

---

### `GET /paper/{paper_id}` — Details about a paper

Examples:
<ul>
    <li><code>https://api.semanticscholar.org/graph/v1/paper/649def34f8be52c8b66281af98ae884c09aef38b</code></li>
    <ul>
        <li>Returns a paper with its paperId and title.  </li>
    </ul>
    <li><code>https://api.semanticscholar.org/graph/v1/paper/649def34f8be52c8b66281af98ae884c09aef38b?fields=url,year,authors</code></li>
    <ul>
        <li>Returns the paper's paperId, url, year, and list of authors.  </li>
        <li>Each author has authorId and name.</li>
    </ul>
    <li><code>https://api.semanticscholar.org/graph/v1/paper/649def34f8be52c8b66281af98ae884c09aef38b?fields=citations.authors</code></li>
    <ul>
        <li>Returns the paper's paperId and list of citations.  </li>
        <li>Each citation has its paperId plus its list of authors.</li>
        <li>Each author has their 2 always included fields of authorId and name.</li>
    </ul>
    <br>
    Limitations:
    <ul>
        <li>Can only return up to 10 MB of data at a time.</li>
    </ul>
</ul>

_Tags: Paper Data_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `paper_id` | path | yes | string | The following types of IDs are supported: <ul>     <li><code>&lt;sha&gt;</code> - a Semantic Scholar ID, e.g. <code>649def34f8be52c8b66281af98ae884c09aef38b</code></li>     <li><code>CorpusId:&lt;id&gt;</code> - a Semantic Scholar numerical ID, e.g. <code>CorpusId:215416146</code></li>     <li><code>DOI:&lt;doi&gt;</code> - a <a href="http://doi.org">Digital Object Identifier</a>,         e.g. <code>DOI:10.18653/v1/N18-3011</code></li>     <li><code>ARXIV:&lt;id&gt;</code> - <a href="https://arxiv.org/">arXiv.rg</a>, e.g. <code>ARXIV:2106.15928</code></li>     <li><code>MAG:&lt;id&gt;</code> - Microsoft Academic Graph, e.g. <code>MAG:112218234</code></li>     <li><code>ACL:&lt;id&gt;</code> - Association for Computational Linguistics, e.g. <code>ACL:W12-3903</code></li>     <li><code>PMID:&lt;id&gt;</code> - PubMed/Medline, e.g. <code>PMID:19872477</code></li>     <li><code>PMCID:&lt;id&gt;</code> - PubMed Central, e.g. <code>PMCID:2323736</code></li>     <li><code>URL:&lt;url&gt;</code> - URL from one of the sites listed below, e.g. <code>URL:https://arxiv.org/abs/2106.15928v1</code></li> </ul>  URLs are recognized from the following sites: <ul>     <li><a href="https://www.semanticscholar.org/">semanticscholar.org</a></li>     <li><a href="https://arxiv.org/">arxiv.org</a></li>     <li><a href="https://www.aclweb.org">aclweb.org</a></li>     <li><a href="https://www.acm.org/">acm.org</a></li>     <li><a href="https://www.biorxiv.org/">biorxiv.org</a></li> </ul> |
| `fields` | query | no | string | A comma-separated list of the fields to be returned. See the contents of Response Schema below for a list of all available fields that can be returned. The <code>paperId</code> field is always returned. If the fields parameter is omitted, only the <code>paperId</code> and <code>title</code> will be returned. <p>Use a period (“.”) for fields that have version numbers or subfields, such as the <code>embedding</code>, <code>authors</code>, <code>citations</code>, and <code>references</code> fields: <ul>     <li>When requesting <code>authors</code>, the <code>authorId</code> and <code>name</code> subfields are returned by default. To request other subfields, use the format <code>author.url,author.paperCount</code>, etc. See the Response Schema below for available subfields.</li>     <li>When requesting <code>citations</code> and <code>references</code>, the <code>paperId</code> and <code>title</code> subfields are returned by default. To request other subfields, use the format <code>citations.title,citations.abstract</code>, etc. See the Response Schema below for available subfields.</li>     <li>When requesting <code>embedding</code>, the default <a href="https://github.com/allenai/specter">Spector embedding version</a> is v1. Specify <code>embedding.specter_v2</code> to select v2 embeddings.</li> </ul> Examples: <ul>     <li><code>fields=title,url</code></li>     <li><code>fields=title,embedding.specter_v2</code></li>     <li><code>fields=title,authors,citations.title,citations.abstract</code></li> </ul> |

**Responses**

- **404** — Bad paper id
  - Body: [`Error404`](#schema-error404)
- **400** — Bad query parameters
  - Body: [`Error400`](#schema-error400)
- **200** — Paper with default or requested fields
  - Body: [`FullPaper`](#schema-fullpaper)

---

### `GET /paper/{paper_id}/authors` — Details about a paper's authors

Examples:
<ul>
    <li><code>https://api.semanticscholar.org/graph/v1/paper/649def34f8be52c8b66281af98ae884c09aef38b/authors</code></li>
    <ul>
        <li>Returns with offset=0, and data is a list of all 3 authors.</li>
        <li>Each author has their authorId and name</li>
    </ul>
    <li><code>https://api.semanticscholar.org/graph/v1/paper/649def34f8be52c8b66281af98ae884c09aef38b/authors?fields=affiliations,papers&limit=2</code></li>
    <ul>
        <li>Returns with offset=0, next=2, and data is a list of 2 authors.</li>
        <li>Each author has their authorId, affiliations, and list of papers.</li>
        <li>Each paper has its paperId and title.</li>
    </ul>
    <li><code>https://api.semanticscholar.org/graph/v1/paper/649def34f8be52c8b66281af98ae884c09aef38b/authors?fields=url,papers.year,papers.authors&offset=2</code></li>
    <ul>
        <li>Returns with offset=2, and data is a list containing the last author.</li>
        <li>This author has their authorId, url, and list of papers.</li>
        <li>Each paper has its paperId, year, and list of authors.</li>
        <li>In that list of authors, each author has their authorId and name.</li>
    </ul>
</ul>

_Tags: Paper Data_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `offset` | query | no | integer | Used for pagination. When returning a list of results, start with the element at this position in the list. |
| `limit` | query | no | integer | The maximum number of results to return.<br> Must be <= 1000 |
| `fields` | query | no | string | A comma-separated list of the fields to be returned. See the contents of the <code>data</code> array in Response Schema below for a list of all available fields that can be returned. The <code>authorId</code> field is always returned. If the fields parameter is omitted, only the <code>authorId</code> and <code>name</code> will be returned. <p>Use a period (“.”) for subfields of <code>papers</code>.<br><br> Examples: <ul>     <li><code>fields=name,affiliations,papers</code></li>     <li><code>fields=url,papers.year,papers.authors</code></li> </ul> |
| `paper_id` | path | yes | string | The following types of IDs are supported: <ul>     <li><code>&lt;sha&gt;</code> - a Semantic Scholar ID, e.g. <code>649def34f8be52c8b66281af98ae884c09aef38b</code></li>     <li><code>CorpusId:&lt;id&gt;</code> - a Semantic Scholar numerical ID, e.g. <code>CorpusId:215416146</code></li>     <li><code>DOI:&lt;doi&gt;</code> - a <a href="http://doi.org">Digital Object Identifier</a>,         e.g. <code>DOI:10.18653/v1/N18-3011</code></li>     <li><code>ARXIV:&lt;id&gt;</code> - <a href="https://arxiv.org/">arXiv.rg</a>, e.g. <code>ARXIV:2106.15928</code></li>     <li><code>MAG:&lt;id&gt;</code> - Microsoft Academic Graph, e.g. <code>MAG:112218234</code></li>     <li><code>ACL:&lt;id&gt;</code> - Association for Computational Linguistics, e.g. <code>ACL:W12-3903</code></li>     <li><code>PMID:&lt;id&gt;</code> - PubMed/Medline, e.g. <code>PMID:19872477</code></li>     <li><code>PMCID:&lt;id&gt;</code> - PubMed Central, e.g. <code>PMCID:2323736</code></li>     <li><code>URL:&lt;url&gt;</code> - URL from one of the sites listed below, e.g. <code>URL:https://arxiv.org/abs/2106.15928v1</code></li> </ul>  URLs are recognized from the following sites: <ul>     <li><a href="https://www.semanticscholar.org/">semanticscholar.org</a></li>     <li><a href="https://arxiv.org/">arxiv.org</a></li>     <li><a href="https://www.aclweb.org">aclweb.org</a></li>     <li><a href="https://www.acm.org/">acm.org</a></li>     <li><a href="https://www.biorxiv.org/">biorxiv.org</a></li> </ul> |

**Responses**

- **404** — Bad paper id
  - Body: [`Error404`](#schema-error404)
- **400** — Bad query parameters
  - Body: [`Error400`](#schema-error400)
- **200** — List of Authors with default or requested fields
  - Body: [`AuthorBatch`](#schema-authorbatch)

---

### `GET /paper/{paper_id}/citations` — Details about a paper's citations

Fetch details about the papers that cite this paper (i.e. papers in whose bibliography this paper appears)
<br><br>
Examples:
<ul>
    <li>Let's suppose that the paper in the examples below has 1600 citations...</li>
    <li><code>https://api.semanticscholar.org/graph/v1/paper/649def34f8be52c8b66281af98ae884c09aef38b/citations</code></li>
    <ul>
        <li>Returns with offset=0, next=100, and data is a list of 100 citations.</li>
        <li>Each citation has a citingPaper which contains its paperId and title.</li>
    </ul>
    <li><code>https://api.semanticscholar.org/graph/v1/paper/649def34f8be52c8b66281af98ae884c09aef38b/citations?fields=contexts,intents,isInfluential,abstract&offset=200&limit=10</code></li>
    <ul>
        <li>Returns with offset=200, next=210, and data is a list of 10 citations.</li>
        <li>Each citation has contexts, intents, isInfluential, and a citingPaper which contains its paperId and abstract.</li>
    </ul>
    <li><code>https://api.semanticscholar.org/graph/v1/paper/649def34f8be52c8b66281af98ae884c09aef38b/citations?fields=authors&offset=1500&limit=500</code></li>
    <ul>
        <li>Returns with offset=1500, and data is a list of the last 100 citations.</li>
        <li>Each citation has a citingPaper which contains its paperId plus a list of authors</li>
        <li>The authors under each citingPaper has their authorId and name.</li>
    </ul>
</ul>

_Tags: Paper Data_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `publicationDateOrYear` | query | no | string | Restricts results to the given range of publication dates or years (inclusive). Accepts the format <code>&lt;startDate&gt;:&lt;endDate&gt;</code> with each date in <code>YYYY-MM-DD</code> format.  <br> <br> Each term is optional, allowing for specific dates, fixed ranges, or open-ended ranges. In addition, prefixes are supported as a shorthand, e.g. <code>2020-06</code> matches all dates in June 2020. <br> <br> Specific dates are not known for all papers, so some records returned with this filter will have a <code>null</code> value for </code>publicationDate</code>. <code>year</code>, however, will always be present. For records where a specific publication date is not known, they will be treated as if published on January 1st of their publication year. <br> <br> Examples: <ul>     <li><code>2019-03-05</code> on March 5th, 2019</li>     <li><code>2019-03</code> during March 2019</li>     <li><code>2019</code> during 2019</li>     <li><code>2016-03-05:2020-06-06</code> as early as March 5th, 2016 or as late as June 6th, 2020</li>     <li><code>1981-08-25:</code> on or after August 25th, 1981</li>     <li><code>:2015-01</code> before or on January 31st, 2015</li>     <li><code>2015:2020</code> between January 1st, 2015 and December 31st, 2020</li> </ul> |
| `offset` | query | no | integer | Used for pagination. When returning a list of results, start with the element at this position in the list. |
| `limit` | query | no | integer | The maximum number of results to return.<br> Must be <= 1000 |
| `fields` | query | no | string | A comma-separated list of the fields to be returned. See the contents of the <code>data</code> array in Response Schema below for a list of all available fields that can be returned. If the fields parameter is omitted, only the <code>paperId</code> and <code>title</code> will be returned. <p>Request fields nested within <code>citedPaper</code> the same way as fields like <code>contexts</code>.<br><br> Examples: <ul>     <li><code>fields=contexts,isInfluential</code></li>     <li><code>fields=contexts,title,authors</code></li> </ul> |
| `paper_id` | path | yes | string | The following types of IDs are supported: <ul>     <li><code>&lt;sha&gt;</code> - a Semantic Scholar ID, e.g. <code>649def34f8be52c8b66281af98ae884c09aef38b</code></li>     <li><code>CorpusId:&lt;id&gt;</code> - a Semantic Scholar numerical ID, e.g. <code>CorpusId:215416146</code></li>     <li><code>DOI:&lt;doi&gt;</code> - a <a href="http://doi.org">Digital Object Identifier</a>,         e.g. <code>DOI:10.18653/v1/N18-3011</code></li>     <li><code>ARXIV:&lt;id&gt;</code> - <a href="https://arxiv.org/">arXiv.rg</a>, e.g. <code>ARXIV:2106.15928</code></li>     <li><code>MAG:&lt;id&gt;</code> - Microsoft Academic Graph, e.g. <code>MAG:112218234</code></li>     <li><code>ACL:&lt;id&gt;</code> - Association for Computational Linguistics, e.g. <code>ACL:W12-3903</code></li>     <li><code>PMID:&lt;id&gt;</code> - PubMed/Medline, e.g. <code>PMID:19872477</code></li>     <li><code>PMCID:&lt;id&gt;</code> - PubMed Central, e.g. <code>PMCID:2323736</code></li>     <li><code>URL:&lt;url&gt;</code> - URL from one of the sites listed below, e.g. <code>URL:https://arxiv.org/abs/2106.15928v1</code></li> </ul>  URLs are recognized from the following sites: <ul>     <li><a href="https://www.semanticscholar.org/">semanticscholar.org</a></li>     <li><a href="https://arxiv.org/">arxiv.org</a></li>     <li><a href="https://www.aclweb.org">aclweb.org</a></li>     <li><a href="https://www.acm.org/">acm.org</a></li>     <li><a href="https://www.biorxiv.org/">biorxiv.org</a></li> </ul> |

**Responses**

- **404** — Bad paper id
  - Body: [`Error404`](#schema-error404)
- **400** — Bad query parameters
  - Body: [`Error400`](#schema-error400)
- **200** — Batch of citations with default or requested fields
  - Body: [`CitationBatch`](#schema-citationbatch)

---

### `GET /paper/{paper_id}/references` — Details about a paper's references

Fetch details about the papers cited by this paper (i.e. appearing in this paper's bibliography)
<br><br>
Examples:
<ul>
    <li>Let's suppose that the paper in the examples below has 1600 references...</li>
    <li><code>https://api.semanticscholar.org/graph/v1/paper/649def34f8be52c8b66281af98ae884c09aef38b/references</code></li>
    <ul>
        <li>Returns with offset=0, next=100, and data is a list of 100 references.</li>
        <li>Each reference has a citedPaper which contains its paperId and title.</li>
    </ul>
    <li><code>https://api.semanticscholar.org/graph/v1/paper/649def34f8be52c8b66281af98ae884c09aef38b/references?fields=contexts,intents,isInfluential,abstract&offset=200&limit=10</code></li>
    <ul>
        <li>Returns with offset=200, next=210, and data is a list of 10 references.</li>
        <li>Each reference has contexts, intents, isInfluential, and a citedPaper which contains its paperId and abstract.</li>
    </ul>
    <li><code>https://api.semanticscholar.org/graph/v1/paper/649def34f8be52c8b66281af98ae884c09aef38b/references?fields=authors&offset=1500&limit=500</code></li>
    <ul>
        <li>Returns with offset=1500, and data is a list of the last 100 references.</li>
        <li>Each reference has a citedPaper which contains its paperId plus a list of authors</li>
        <li>The authors under each citedPaper has their authorId and name.</li>
    </ul>
</ul>

_Tags: Paper Data_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `offset` | query | no | integer | Used for pagination. When returning a list of results, start with the element at this position in the list. |
| `limit` | query | no | integer | The maximum number of results to return.<br> Must be <= 1000 |
| `fields` | query | no | string | A comma-separated list of the fields to be returned. See the contents of the <code>data</code> array in Response Schema below for a list of all available fields that can be returned. If the fields parameter is omitted, only the <code>paperId</code> and <code>title</code> will be returned. <p>Request fields nested within <code>citedPaper</code> the same way as fields like <code>contexts</code>.<br><br> Examples: <ul>     <li><code>fields=contexts,isInfluential</code></li>     <li><code>fields=contexts,title,authors</code></li> </ul> |
| `paper_id` | path | yes | string | The following types of IDs are supported: <ul>     <li><code>&lt;sha&gt;</code> - a Semantic Scholar ID, e.g. <code>649def34f8be52c8b66281af98ae884c09aef38b</code></li>     <li><code>CorpusId:&lt;id&gt;</code> - a Semantic Scholar numerical ID, e.g. <code>CorpusId:215416146</code></li>     <li><code>DOI:&lt;doi&gt;</code> - a <a href="http://doi.org">Digital Object Identifier</a>,         e.g. <code>DOI:10.18653/v1/N18-3011</code></li>     <li><code>ARXIV:&lt;id&gt;</code> - <a href="https://arxiv.org/">arXiv.rg</a>, e.g. <code>ARXIV:2106.15928</code></li>     <li><code>MAG:&lt;id&gt;</code> - Microsoft Academic Graph, e.g. <code>MAG:112218234</code></li>     <li><code>ACL:&lt;id&gt;</code> - Association for Computational Linguistics, e.g. <code>ACL:W12-3903</code></li>     <li><code>PMID:&lt;id&gt;</code> - PubMed/Medline, e.g. <code>PMID:19872477</code></li>     <li><code>PMCID:&lt;id&gt;</code> - PubMed Central, e.g. <code>PMCID:2323736</code></li>     <li><code>URL:&lt;url&gt;</code> - URL from one of the sites listed below, e.g. <code>URL:https://arxiv.org/abs/2106.15928v1</code></li> </ul>  URLs are recognized from the following sites: <ul>     <li><a href="https://www.semanticscholar.org/">semanticscholar.org</a></li>     <li><a href="https://arxiv.org/">arxiv.org</a></li>     <li><a href="https://www.aclweb.org">aclweb.org</a></li>     <li><a href="https://www.acm.org/">acm.org</a></li>     <li><a href="https://www.biorxiv.org/">biorxiv.org</a></li> </ul> |

**Responses**

- **404** — Bad paper id
  - Body: [`Error404`](#schema-error404)
- **400** — Bad query parameters
  - Body: [`Error400`](#schema-error400)
- **200** — Batch of references with default or requested fields
  - Body: [`ReferenceBatch`](#schema-referencebatch)

---

### `GET /snippet/search` — Text snippet search

Return the text snippets that most closely match the query. Text snippets are excerpts of approximately 500 words, drawn from a paper's title, abstract, and body text, but excluding figure captions and the bibliography.
It will return the highest ranked snippet first, as well as some basic data about the paper it was found in.
Examples:
<ul>
  <li><code>https://api.semanticscholar.org/graph/v1/snippet/search?query=The literature graph is a property graph with directed edges&limit=1</code></li>
  <ul>
    <li>Returns a single snippet that is the highest ranked match.</li>
    <li>Each snippet has text, snippetKind, section, annotation data, and score. As well as the following data about the paper it comes from: corpusId, title, authors, and openAccessInfo.</li>
  </ul>
</ul>
  <br>
    Limitations:
    <ul>
        <li>You must include a query.</li>
        <li>If you don't set a limit, it will automatically return 10 results.</li>
        <li>The max limit allowed is 1000.</li>
    </ul>
</ul>

_Tags: Snippet Text_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `fields` | query | no | string | A comma-separated list of the fields to be returned with each snippet element.  Paper info and the score are currently always returned. What you can specify using this <code>fields</code> param is which fields under the 'snippet' section (see the response schema) will be returned.  Examples: <ul>     <li><code>fields=snippet.text</code>: you'll get just the <code>text</code> field in the snippet section</li>     <li><code>fields=snippet.text,snippet.snippetKind</code>: you'll get just the <code>text</code> and <code>snippetKind</code> fields in the snippet section</li>     <li><code>fields=snippet.annotations.sentences</code>: you'll get just the sentence annotations in the snippet section</li> </ul>  In general, you can use periods to identify nested fields (as in the examples above).  Not all fields in the response schema can be identified using this <code>fields</code> param though. E.g. you can't pick what you get within <code>snippet.snippetOffset</code> - you can either get the snippet offset with all the possible snippet offset fields, or you can not get it at all. You also can't provide <code>paper</code> or <code>score</code> or anything under <code>paper</code>, since those are always provided.  If you attempt to identify a field that's not supported, you'll get an error with the relevant field name. E.g.  <code>Unrecognized or unsupported fields: [paper]</code>  If you don't specify the fields param, you'll get a default set of fields in the snippet section. These are the default fields: - <code>snippet.text</code> - <code>snippet.snippetKind</code> - <code>snippet.section</code> - <code>snippet.snippetOffset</code> (including nested <code>start</code> and <code>end</code>) - <code>snippet.annotations.refMentions</code> (including nested <code>start</code>, <code>end</code>, and <code>matchedPaperCorpusId</code> for each element) - <code>snippet.annotations.sentences</code> (including nested <code>start</code> and <code>end</code> for each element) |
| `paperIds` | query | no | string | Restricts results to snippets from specific papers. To specify papers, provide a comma-separated list of their IDs. You can provide up to approximately 100 IDs.  The following types of IDs are supported: <ul>     <li><code>&lt;sha&gt;</code> - a Semantic Scholar ID, e.g. <code>649def34f8be52c8b66281af98ae884c09aef38b</code></li>     <li><code>CorpusId:&lt;id&gt;</code> - a Semantic Scholar numerical ID, e.g. <code>CorpusId:215416146</code></li>     <li><code>DOI:&lt;doi&gt;</code> - a <a href="http://doi.org">Digital Object Identifier</a>,         e.g. <code>DOI:10.18653/v1/N18-3011</code></li>     <li><code>ARXIV:&lt;id&gt;</code> - <a href="https://arxiv.org/">arXiv.rg</a>, e.g. <code>ARXIV:2106.15928</code></li>     <li><code>MAG:&lt;id&gt;</code> - Microsoft Academic Graph, e.g. <code>MAG:112218234</code></li>     <li><code>ACL:&lt;id&gt;</code> - Association for Computational Linguistics, e.g. <code>ACL:W12-3903</code></li>     <li><code>PMID:&lt;id&gt;</code> - PubMed/Medline, e.g. <code>PMID:19872477</code></li>     <li><code>PMCID:&lt;id&gt;</code> - PubMed Central, e.g. <code>PMCID:2323736</code></li>     <li><code>URL:&lt;url&gt;</code> - URL from one of the sites listed below, e.g. <code>URL:https://arxiv.org/abs/2106.15928v1</code></li> </ul>  URLs are recognized from the following sites: <ul>     <li><a href="https://www.semanticscholar.org/">semanticscholar.org</a></li>     <li><a href="https://arxiv.org/">arxiv.org</a></li>     <li><a href="https://www.aclweb.org">aclweb.org</a></li>     <li><a href="https://www.acm.org/">acm.org</a></li>     <li><a href="https://www.biorxiv.org/">biorxiv.org</a></li> </ul> |
| `authors` | query | no | string | Restricts results to papers with authors matching the given names, formatted as a comma-separated list (<code>...?authors=name1,name2,...</code>). The search criteria are 'fuzzy', so matches that are <em>close</em> will also return results. <br><br>  Example: <code>galileo,kepler</code> will return papers that include <em>both</em> an author similar to "galileo" <em>and</em> an author similar to "kepler" as co-authors. This query will also match fuzzy variations like 'keppler' and 'Kepler' (default max 'edit distance' is 2).  <strong>Important:</strong> Multiple author names are combined with AND logic, meaning results must include <em>all</em> specified authors. Adding more authors will narrow your results, not expand them. To search for papers by <em>any</em> of several authors (OR logic), perform separate searches for each author name. The maximum number of author filters is by default <code>10</code> and will return an HTTP code 400 (Bad Request) if more than 10 are supplied. |
| `minCitationCount` | query | no | string | Restricts results to only include papers with the minimum number of citations. <br> <br> Example: <code>minCitationCount=200</code> |
| `insertedBefore` | query | no | string | Restricts results to snippets from papers inserted into the index before the provided date (excludes things inserted on the provided date).  Acceptable formats: YYYY-MM-DD, YYYY-MM, YYYY |
| `publicationDateOrYear` | query | no | string | Restricts results to the given range of publication dates or years (inclusive). Accepts the format <code>&lt;startDate&gt;:&lt;endDate&gt;</code> with each date in <code>YYYY-MM-DD</code> format.  <br> <br> Each term is optional, allowing for specific dates, fixed ranges, or open-ended ranges. In addition, prefixes are supported as a shorthand, e.g. <code>2020-06</code> matches all dates in June 2020. <br> <br> Specific dates are not known for all papers, so some records returned with this filter will have a <code>null</code> value for </code>publicationDate</code>. <code>year</code>, however, will always be present. For records where a specific publication date is not known, they will be treated as if published on January 1st of their publication year. <br> <br> Examples: <ul>     <li><code>2019-03-05</code> on March 5th, 2019</li>     <li><code>2019-03</code> during March 2019</li>     <li><code>2019</code> during 2019</li>     <li><code>2016-03-05:2020-06-06</code> as early as March 5th, 2016 or as late as June 6th, 2020</li>     <li><code>1981-08-25:</code> on or after August 25th, 1981</li>     <li><code>:2015-01</code> before or on January 31st, 2015</li>     <li><code>2015:2020</code> between January 1st, 2015 and December 31st, 2020</li> </ul> |
| `year` | query | no | string | Restricts results to the given publication year or range of years (inclusive). <br> <br> Examples: <ul>     <li><code>2019</code> in 2019</li>     <li><code>2016-2020</code> as early as 2016 or as late as 2020</li>     <li><code>2010-</code> during or after 2010</li>     <li><code>-2015</code> before or during 2015</li> </ul> |
| `venue` | query | no | string | Restricts results to papers published in the given venues, formatted as a comma-separated list. <br><br> Input could also be an ISO4 abbreviation. Examples include: <ul>     <li>Nature</li>     <li>New England Journal of Medicine</li>     <li>Radiology</li>     <li>N. Engl. J. Med.</li> </ul>  Example: <code>Nature,Radiology</code> will return papers from venues Nature and/or Radiology. |
| `fieldsOfStudy` | query | no | string | Restricts results to papers in the given fields of study, formatted as a comma-separated list: <ul> <li>Computer Science</li> <li>Medicine</li> <li>Chemistry</li> <li>Biology</li> <li>Materials Science</li> <li>Physics</li> <li>Geology</li> <li>Psychology</li> <li>Art</li> <li>History</li> <li>Geography</li> <li>Sociology</li> <li>Business</li> <li>Political Science</li> <li>Economics</li> <li>Philosophy</li> <li>Mathematics</li> <li>Engineering</li> <li>Environmental Science</li> <li>Agricultural and Food Sciences</li> <li>Education</li> <li>Law</li> <li>Linguistics</li> </ul>  Example: <code>Physics,Mathematics</code> will return papers with either Physics or Mathematics in their list of fields-of-study. |
| `query` | query | yes | string | A plain-text search query string. * No special query syntax is supported. |
| `limit` | query | no | integer | The maximum number of results to return.<br> Must be <= 1000 |

**Responses**

- **400** — Bad query parameters
  - Body: [`Error400`](#schema-error400)
- **200** — Best snippet match with default fields
  - Body: [`SnippetMatch`](#schema-snippetmatch)

---

## Schemas

### Schema: `Error404` <a id="schema-error404"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `error` | string | no | Depending on the case, error message may be any of these: <ul>     <li><code>"Paper/Author/Object not found"</code></li>     <li><code>"Paper/Author/Object with id ### not found"</code></li> </ul> |


### Schema: `Error400` <a id="schema-error400"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `error` | string | no | Depending on the case, error message may be any of these: <ul>     <li><code>"Unrecognized or unsupported fields: [bad1, bad2, etc...]"</code></li>     <li><code>"Unacceptable query params: [badK1=badV1, badK2=badV2, etc...}]"</code></li>     <li><code>"Response would exceed maximum size...."</code></li>         <ul><li>This error will occur when the response exceeds 10 MB. Suggestions to either break the request into smaller batches, or make use of the limit and offset features will be presented.</li></ul>     <li>A custom message string</li></ul> |


### Schema: `FullPaper` <a id="schema-fullpaper"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `paperId` | string | no | Semantic Scholar’s primary unique identifier for a paper. |
| `corpusId` | integer | no | Semantic Scholar’s secondary unique identifier for a paper. |
| `externalIds` | object | no | An object that contains the paper’s unique identifiers in external sources. The external sources are limited to: ArXiv, MAG, ACL, PubMed, Medline, PubMedCentral, DBLP, and DOI. |
| `url` | string | no | URL of the paper on the Semantic Scholar website. |
| `title` | string | no | Title of the paper. |
| `abstract` | string | no | The paper's abstract. Note that due to legal reasons, this may be missing even if we display an abstract on the website. |
| `venue` | string | no | The name of the paper’s publication venue. |
| `publicationVenue` | object | no | An object that contains the following information about the journal or conference in which this paper was published: id (the venue’s unique ID), name (the venue’s name), type (the type of venue), alternate_names (an array of alternate names for the venue), and url (the venue’s website). |
| `year` | integer | no | The year the paper was published. |
| `referenceCount` | integer | no | The total number of papers this paper references. |
| `citationCount` | integer | no | The total number of papers that references this paper. |
| `influentialCitationCount` | integer | no | A subset of the citation count, where the cited publication has a significant impact on the citing publication. Determined by Semantic Scholar’s algorithm: https://www.semanticscholar.org/faq#influential-citations. |
| `isOpenAccess` | boolean | no | Whether the paper is open access. More information here: https://www.openaccess.nl/en/what-is-open-access. |
| `openAccessPdf` | object | no | An object that contains the following parameters: url (a link to the paper’s PDF), status (the type of open access https://en.wikipedia.org/wiki/Open_access#Colour_naming_system), the paper's license, and a legal disclaimer. |
| `fieldsOfStudy` | array of string | no | A list of the paper’s high-level academic categories from external sources. The possible fields are: Computer Science, Medicine, Chemistry, Biology, Materials Science, Physics, Geology, Psychology, Art, History, Geography, Sociology, Business, Political Science, Economics, Philosophy, Mathematics, Engineering, Environmental Science, Agricultural and Food Sciences, Education, Law, and Linguistics. |
| `s2FieldsOfStudy` | array of object | no | An array of objects. Each object contains the following parameters: category (a field of study. The possible fields are the same as in fieldsOfStudy), and source (specifies whether the category was classified by Semantic Scholar or by an external source. More information on how Semantic Scholar classifies papers https://medium.com/ai2-blog/announcing-s2fos-an-open-source-academic-field-of-study-classifier-9d2f641949e5) |
| `publicationTypes` | array of string | no | The type of this publication. |
| `publicationDate` | string | no | The date when this paper was published, in YYYY-MM-DD format. |
| `journal` | object | no | An object that contains the following parameters, if available: name (the journal name), volume (the journal’s volume number), and pages (the page number range) |
| `citationStyles` | object | no | The BibTex bibliographical citation of the paper. |
| `authors` | array of object | no |  |
| `citations` | array of [`BasePaper`](#schema-basepaper) | no |  |
| `references` | array of object | no |  |
| `embedding` | [`Embedding`](#schema-embedding) | no |  |
| `tldr` | [`Tldr`](#schema-tldr) | no |  |
| `textAvailability` | string | no | fulltext, abstract, or none, based on what we have available for this paper |


### Schema: `AuthorInPaper` <a id="schema-authorinpaper"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `authorId` | string | no | Semantic Scholar’s unique ID for the author. |
| `externalIds` | object | no | An object that contains the ORCID/DBLP IDs for the author, if known. |
| `url` | string | no | URL of the author on the Semantic Scholar website. |
| `name` | string | no | Author’s name. |
| `affiliations` | array of string | no | Array of organizational affiliations for the author. |
| `homepage` | string | no | The author’s homepage. |
| `paperCount` | string | no | The author's total publications count. |
| `citationCount` | string | no | The author's total citations count. |
| `hIndex` | string | no | The author’s h-index, which is a measure of the productivity and citation impact of the author’s publications: https://www.semanticscholar.org/faq#h-index |
| `normalizedAffiliations` | array of [`NormalizedAffiliation`](#schema-normalizedaffiliation) | no | Array of organizational ROR-based normalized affiliations for the author (ROR - Research Organization Registry). |


### Schema: `NormalizedAffiliation` <a id="schema-normalizedaffiliation"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `rorId` | string | no | ROR (Research Organization Registry) unique ID. |
| `rorDisplayName` | string | no | Official ROR display name. |


### Schema: `BasePaper` <a id="schema-basepaper"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `paperId` | string | no | Semantic Scholar’s primary unique identifier for a paper. |
| `corpusId` | integer | no | Semantic Scholar’s secondary unique identifier for a paper. |
| `externalIds` | object | no | An object that contains the paper’s unique identifiers in external sources. The external sources are limited to: ArXiv, MAG, ACL, PubMed, Medline, PubMedCentral, DBLP, and DOI. |
| `url` | string | no | URL of the paper on the Semantic Scholar website. |
| `title` | string | no | Title of the paper. |
| `abstract` | string | no | The paper's abstract. Note that due to legal reasons, this may be missing even if we display an abstract on the website. |
| `venue` | string | no | The name of the paper’s publication venue. |
| `publicationVenue` | object | no | An object that contains the following information about the journal or conference in which this paper was published: id (the venue’s unique ID), name (the venue’s name), type (the type of venue), alternate_names (an array of alternate names for the venue), and url (the venue’s website). |
| `year` | integer | no | The year the paper was published. |
| `referenceCount` | integer | no | The total number of papers this paper references. |
| `citationCount` | integer | no | The total number of papers that references this paper. |
| `influentialCitationCount` | integer | no | A subset of the citation count, where the cited publication has a significant impact on the citing publication. Determined by Semantic Scholar’s algorithm: https://www.semanticscholar.org/faq#influential-citations. |
| `isOpenAccess` | boolean | no | Whether the paper is open access. More information here: https://www.openaccess.nl/en/what-is-open-access. |
| `openAccessPdf` | object | no | An object that contains the following parameters: url (a link to the paper’s PDF), status (the type of open access https://en.wikipedia.org/wiki/Open_access#Colour_naming_system), the paper's license, and a legal disclaimer. |
| `fieldsOfStudy` | array of string | no | A list of the paper’s high-level academic categories from external sources. The possible fields are: Computer Science, Medicine, Chemistry, Biology, Materials Science, Physics, Geology, Psychology, Art, History, Geography, Sociology, Business, Political Science, Economics, Philosophy, Mathematics, Engineering, Environmental Science, Agricultural and Food Sciences, Education, Law, and Linguistics. |
| `s2FieldsOfStudy` | array of object | no | An array of objects. Each object contains the following parameters: category (a field of study. The possible fields are the same as in fieldsOfStudy), and source (specifies whether the category was classified by Semantic Scholar or by an external source. More information on how Semantic Scholar classifies papers https://medium.com/ai2-blog/announcing-s2fos-an-open-source-academic-field-of-study-classifier-9d2f641949e5) |
| `publicationTypes` | array of string | no | The type of this publication. |
| `publicationDate` | string | no | The date when this paper was published, in YYYY-MM-DD format. |
| `journal` | object | no | An object that contains the following parameters, if available: name (the journal name), volume (the journal’s volume number), and pages (the page number range) |
| `citationStyles` | object | no | The BibTex bibliographical citation of the paper. |
| `authors` | array of [`AuthorInfo`](#schema-authorinfo) | no | Details about the paper's authors |


### Schema: `AuthorInfo` <a id="schema-authorinfo"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `authorId` | string | no | Semantic Scholar’s unique ID for the author. |
| `name` | string | no | Author’s name. |


### Schema: `Embedding` <a id="schema-embedding"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `model` | string | no | The Spector vector embedding model version: https://github.com/allenai/specter |
| `vector` | object | no | Numerical embedding vector. |


### Schema: `Tldr` <a id="schema-tldr"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `model` | string | no | The tldr model version number: https://github.com/allenai/scitldr |
| `text` | string | no | The tldr paper summary. |


### Schema: `PaperBatch` <a id="schema-paperbatch"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ids` | array of string | no |  |


### Schema: `CitationBatch` <a id="schema-citationbatch"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `offset` | integer | no | Starting position for this batch. |
| `next` | integer | no | Starting position of the next batch. Absent if no more data exists. |
| `data` | array of object | no |  |


### Schema: `Citation` <a id="schema-citation"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `contexts` | array of string | no | Array of text snippets where the reference to the paper is mentioned. |
| `intents` | array of string | no | Array of citation intents that summarizes how the reference to the paper is mentioned. Possible intents: https://www.semanticscholar.org/faq#citation-intent |
| `contextsWithIntent` | array of object | no | Array of objects that contain both contexts and the intents they are associated with. |
| `isInfluential` | boolean | no | Whether the citing paper is highly influential. See more about influential citations: https://www.semanticscholar.org/faq#influential-citations |
| `citingPaper` | object | no | Details about the citing paper |


### Schema: `ReferenceBatch` <a id="schema-referencebatch"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `offset` | integer | no | Starting position for this batch. |
| `next` | integer | no | Starting position of the next batch. Absent if no more data exists. |
| `data` | array of object | no |  |


### Schema: `Reference` <a id="schema-reference"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `contexts` | array of string | no | Array of text snippets where the reference to the paper is mentioned. |
| `intents` | array of string | no | Array of citation intents that summarizes how the reference to the paper is mentioned. Possible intents: https://www.semanticscholar.org/faq#citation-intent |
| `contextsWithIntent` | array of object | no | Array of objects that contain both contexts and the intents they are associated with. |
| `isInfluential` | boolean | no | Whether the citing paper is highly influential. See more about influential citations: https://www.semanticscholar.org/faq#influential-citations |
| `citedPaper` | object | no | Details about the cited paper |


### Schema: `AuthorBatch` <a id="schema-authorbatch"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `offset` | integer | no | Starting position for this batch. |
| `next` | integer | no | Starting position of the next batch. Absent if no more data exists. |
| `data` | array of object | no |  |


### Schema: `AuthorInPaperWithPapers` <a id="schema-authorinpaperwithpapers"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `authorId` | string | no | Semantic Scholar’s unique ID for the author. |
| `externalIds` | object | no | An object that contains the ORCID/DBLP IDs for the author, if known. |
| `url` | string | no | URL of the author on the Semantic Scholar website. |
| `name` | string | no | Author’s name. |
| `affiliations` | array of string | no | Array of organizational affiliations for the author. |
| `homepage` | string | no | The author’s homepage. |
| `paperCount` | string | no | The author's total publications count. |
| `citationCount` | string | no | The author's total citations count. |
| `hIndex` | string | no | The author’s h-index, which is a measure of the productivity and citation impact of the author’s publications: https://www.semanticscholar.org/faq#h-index |
| `normalizedAffiliations` | array of [`NormalizedAffiliation`](#schema-normalizedaffiliation) | no | Array of organizational ROR-based normalized affiliations for the author (ROR - Research Organization Registry). |
| `papers` | array of object | no |  |


### Schema: `PaperRelevanceSearchBatch` <a id="schema-paperrelevancesearchbatch"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `total` | string | no | Approximate number of matching search results.  Because of the subtleties of finding partial phrase matches in different parts of the document, be cautious about interpreting the total field as a count of documents containing any particular word in the query. |
| `offset` | integer | no | Starting position for this batch. |
| `next` | integer | no | Starting position of the next batch. Absent if no more data exists. |
| `data` | array of object | no |  |


### Schema: `PaperBulkSearchBatch` <a id="schema-paperbulksearchbatch"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `total` | string | no | Approximate number of matching search results.  Because of the subtleties of finding partial phrase matches in different parts of the document, be cautious about interpreting the total field as a count of documents containing any particular word in the query. |
| `token` | string | no | A continuation token that must be provided to fetch the next page of results. Present only when more results can be fetched. |
| `data` | array of object | no |  |


### Schema: `PaperMatch` <a id="schema-papermatch"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `data` | array of [`Title Match Paper`](#schema-title match paper) | no |  |


### Schema: `Title Match Paper` <a id="schema-title match paper"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `matchScore` | integer | no |  |
| `paperId` | string | no | Semantic Scholar’s primary unique identifier for a paper. |
| `corpusId` | integer | no | Semantic Scholar’s secondary unique identifier for a paper. |
| `externalIds` | object | no | An object that contains the paper’s unique identifiers in external sources. The external sources are limited to: ArXiv, MAG, ACL, PubMed, Medline, PubMedCentral, DBLP, and DOI. |
| `url` | string | no | URL of the paper on the Semantic Scholar website. |
| `title` | string | no | Title of the paper. |
| `abstract` | string | no | The paper's abstract. Note that due to legal reasons, this may be missing even if we display an abstract on the website. |
| `venue` | string | no | The name of the paper’s publication venue. |
| `publicationVenue` | object | no | An object that contains the following information about the journal or conference in which this paper was published: id (the venue’s unique ID), name (the venue’s name), type (the type of venue), alternate_names (an array of alternate names for the venue), and url (the venue’s website). |
| `year` | integer | no | The year the paper was published. |
| `referenceCount` | integer | no | The total number of papers this paper references. |
| `citationCount` | integer | no | The total number of papers that references this paper. |
| `influentialCitationCount` | integer | no | A subset of the citation count, where the cited publication has a significant impact on the citing publication. Determined by Semantic Scholar’s algorithm: https://www.semanticscholar.org/faq#influential-citations. |
| `isOpenAccess` | boolean | no | Whether the paper is open access. More information here: https://www.openaccess.nl/en/what-is-open-access. |
| `openAccessPdf` | object | no | An object that contains the following parameters: url (a link to the paper’s PDF), status (the type of open access https://en.wikipedia.org/wiki/Open_access#Colour_naming_system), the paper's license, and a legal disclaimer. |
| `fieldsOfStudy` | array of string | no | A list of the paper’s high-level academic categories from external sources. The possible fields are: Computer Science, Medicine, Chemistry, Biology, Materials Science, Physics, Geology, Psychology, Art, History, Geography, Sociology, Business, Political Science, Economics, Philosophy, Mathematics, Engineering, Environmental Science, Agricultural and Food Sciences, Education, Law, and Linguistics. |
| `s2FieldsOfStudy` | array of object | no | An array of objects. Each object contains the following parameters: category (a field of study. The possible fields are the same as in fieldsOfStudy), and source (specifies whether the category was classified by Semantic Scholar or by an external source. More information on how Semantic Scholar classifies papers https://medium.com/ai2-blog/announcing-s2fos-an-open-source-academic-field-of-study-classifier-9d2f641949e5) |
| `publicationTypes` | array of string | no | The type of this publication. |
| `publicationDate` | string | no | The date when this paper was published, in YYYY-MM-DD format. |
| `journal` | object | no | An object that contains the following parameters, if available: name (the journal name), volume (the journal’s volume number), and pages (the page number range) |
| `citationStyles` | object | no | The BibTex bibliographical citation of the paper. |
| `authors` | array of object | no |  |
| `citations` | array of [`BasePaper`](#schema-basepaper) | no |  |
| `references` | array of object | no |  |
| `embedding` | [`Embedding`](#schema-embedding) | no |  |
| `tldr` | [`Tldr`](#schema-tldr) | no |  |
| `textAvailability` | string | no | fulltext, abstract, or none, based on what we have available for this paper |


### Schema: `PaperAutocomplete` <a id="schema-paperautocomplete"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `matches` | array of [`Autocomplete Paper`](#schema-autocomplete paper) | no |  |


### Schema: `Autocomplete Paper` <a id="schema-autocomplete paper"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `id` | string | no | The paper's primary unique identifier. |
| `title` | string | no | Title of the paper. |
| `authorsYear` | string | no | Summary of the authors of the paper and year of publication. |


### Schema: `AuthorWithPapers` <a id="schema-authorwithpapers"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `authorId` | string | no | Semantic Scholar’s unique ID for the author. |
| `externalIds` | object | no | An object that contains the ORCID/DBLP IDs for the author, if known. |
| `url` | string | no | URL of the author on the Semantic Scholar website. |
| `name` | string | no | Author’s name. |
| `affiliations` | array of string | no | Array of organizational affiliations for the author. |
| `homepage` | string | no | The author’s homepage. |
| `paperCount` | string | no | The author's total publications count. |
| `citationCount` | string | no | The author's total citations count. |
| `hIndex` | string | no | The author’s h-index, which is a measure of the productivity and citation impact of the author’s publications: https://www.semanticscholar.org/faq#h-index |
| `papers` | array of object | no |  |


### Schema: `AuthorIdList` <a id="schema-authoridlist"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `ids` | array of string | no |  |


### Schema: `AuthorPaperBatch` <a id="schema-authorpaperbatch"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `offset` | integer | no | Starting position for this batch. |
| `next` | integer | no | Starting position of the next batch. Absent if no more data exists. |
| `data` | array of object | no |  |


### Schema: `PaperWithLinks` <a id="schema-paperwithlinks"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `paperId` | string | no | Semantic Scholar’s primary unique identifier for a paper. |
| `corpusId` | integer | no | Semantic Scholar’s secondary unique identifier for a paper. |
| `externalIds` | object | no | An object that contains the paper’s unique identifiers in external sources. The external sources are limited to: ArXiv, MAG, ACL, PubMed, Medline, PubMedCentral, DBLP, and DOI. |
| `url` | string | no | URL of the paper on the Semantic Scholar website. |
| `title` | string | no | Title of the paper. |
| `abstract` | string | no | The paper's abstract. Note that due to legal reasons, this may be missing even if we display an abstract on the website. |
| `venue` | string | no | The name of the paper’s publication venue. |
| `publicationVenue` | object | no | An object that contains the following information about the journal or conference in which this paper was published: id (the venue’s unique ID), name (the venue’s name), type (the type of venue), alternate_names (an array of alternate names for the venue), and url (the venue’s website). |
| `year` | integer | no | The year the paper was published. |
| `referenceCount` | integer | no | The total number of papers this paper references. |
| `citationCount` | integer | no | The total number of papers that references this paper. |
| `influentialCitationCount` | integer | no | A subset of the citation count, where the cited publication has a significant impact on the citing publication. Determined by Semantic Scholar’s algorithm: https://www.semanticscholar.org/faq#influential-citations. |
| `isOpenAccess` | boolean | no | Whether the paper is open access. More information here: https://www.openaccess.nl/en/what-is-open-access. |
| `openAccessPdf` | object | no | An object that contains the following parameters: url (a link to the paper’s PDF), status (the type of open access https://en.wikipedia.org/wiki/Open_access#Colour_naming_system), the paper's license, and a legal disclaimer. |
| `fieldsOfStudy` | array of string | no | A list of the paper’s high-level academic categories from external sources. The possible fields are: Computer Science, Medicine, Chemistry, Biology, Materials Science, Physics, Geology, Psychology, Art, History, Geography, Sociology, Business, Political Science, Economics, Philosophy, Mathematics, Engineering, Environmental Science, Agricultural and Food Sciences, Education, Law, and Linguistics. |
| `s2FieldsOfStudy` | array of object | no | An array of objects. Each object contains the following parameters: category (a field of study. The possible fields are the same as in fieldsOfStudy), and source (specifies whether the category was classified by Semantic Scholar or by an external source. More information on how Semantic Scholar classifies papers https://medium.com/ai2-blog/announcing-s2fos-an-open-source-academic-field-of-study-classifier-9d2f641949e5) |
| `publicationTypes` | array of string | no | The type of this publication. |
| `publicationDate` | string | no | The date when this paper was published, in YYYY-MM-DD format. |
| `journal` | object | no | An object that contains the following parameters, if available: name (the journal name), volume (the journal’s volume number), and pages (the page number range) |
| `citationStyles` | object | no | The BibTex bibliographical citation of the paper. |
| `authors` | array of [`AuthorInfo`](#schema-authorinfo) | no | Details about the paper's authors |
| `citations` | array of [`PaperInfo`](#schema-paperinfo) | no |  |
| `references` | array of object | no |  |


### Schema: `PaperInfo` <a id="schema-paperinfo"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `paperId` | string | no | Semantic Scholar’s primary unique identifier for a paper. |
| `corpusId` | integer | no | Semantic Scholar’s secondary unique identifier for a paper. |
| `url` | string | no | URL of the paper on the Semantic Scholar website. |
| `title` | string | no | Title of the paper. |
| `venue` | string | no | The name of the paper’s publication venue. |
| `publicationVenue` | object | no | An object that contains the following information about the journal or conference in which this paper was published: id (the venue’s unique ID), name (the venue’s name), type (the type of venue), alternate_names (an array of alternate names for the venue), and url (the venue’s website). |
| `year` | integer | no | The year the paper was published. |
| `authors` | array of [`AuthorInfo`](#schema-authorinfo) | no | Details about the paper's authors |


### Schema: `AuthorSearchBatch` <a id="schema-authorsearchbatch"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `total` | string | no | Approximate number of matching search results.  Because of the subtleties of finding partial phrase matches in different parts of the document, be cautious about interpreting the total field as a count of documents containing any particular word in the query. |
| `offset` | integer | no | Starting position for this batch. |
| `next` | integer | no | Starting position of the next batch. Absent if no more data exists. |
| `data` | array of object | no |  |


### Schema: `SnippetMatch` <a id="schema-snippetmatch"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `data` | array of [`Snippet Match`](#schema-snippet match) | no |  |
| `retrievalVersion` | string | no | A rough representation of the retrieval approach we've used to get the results. We'll usually bump this if we change something about how we get results. Note that the same retrievalVersion value doesn't guarantee that you'll get the same results for the same query at different times, and a different retrievalVersion value doesn't always mean you'll get different results. |


### Schema: `Snippet Match` <a id="schema-snippet match"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `snippet` | [`snippet`](#schema-snippet) | no |  |
| `score` | number | no |  |
| `paper` | [`paper`](#schema-paper) | no |  |


### Schema: `snippet` <a id="schema-snippet"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `text` | string | no | The direct quote or snippet from the paper relevant to the query. |
| `snippetKind` | string | no | Where the snippet is located, options are: title, abstract, or body.  |
| `section` | string | no | Only applies to snippets from the body, refers to the section of the paper where the snippet is located. |
| `snippetOffset` | object | no | The location of the snippet within the paper. |
| `annotations` | [`annotations`](#schema-annotations) | no |  |


### Schema: `annotations` <a id="schema-annotations"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `sentences` | array of [`sentence`](#schema-sentence) | no |  |
| `refMentions` | array of [`refMention`](#schema-refmention) | no |  |


### Schema: `sentence` <a id="schema-sentence"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `start` | integer | no |  |
| `end` | integer | no |  |


### Schema: `refMention` <a id="schema-refmention"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `start` | integer | no |  |
| `end` | integer | no |  |
| `matchedPaperCorpusId` | string | no |  |


### Schema: `paper` <a id="schema-paper"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `corpusId` | string | no | Semantic Scholar’s secondary unique identifier for a paper. |
| `title` | string | no | Title of the paper. |
| `authors` | array of string | no |  |
| `openAccessInfo` | [`openAccessInfo`](#schema-openaccessinfo) | no |  |


### Schema: `openAccessInfo` <a id="schema-openaccessinfo"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `license` | string | no | The license attached to the paper. |
| `status` | string | no | Paper's status (the type of open access https://en.wikipedia.org/wiki/Open_access#Colour_naming_system) |
| `disclaimer` | string | no | A disclaimer about the open access use of this paper. |

