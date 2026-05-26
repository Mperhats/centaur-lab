# Recommendations API

_Version 1.0_

Base URL: `https://api.semanticscholar.org/recommendations/v1`

Get Semantic Scholar's recommended papers given other papers as input. All methods will return up to LIMIT recommendations if they are available.

Default response media types: `application/json`

Default request media types: `application/json`

## Endpoints

### `POST /papers/` — Get recommended papers for lists of positive and negative example papers

_Tags: Paper Recommendations_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `limit` | query | no | integer | How many recommendations to return. Maximum 500. |
| `fields` | query | no | string | A comma-separated list of the fields to be returned. See the contents of the <code>recommendedPapers</code> array in Response Schema below for a list of all available fields that can be returned.<br><br> The <code>paperId</code> field is always returned. If the fields parameter is omitted, only the <code>paperId</code> and <code>title</code> will be returned.<br><br> Examples: <code>http://api.semanticscholar.org/recommendations/v1/papers?fields=title,url,authors</code> |

**Request body**

- Type: [`Paper%20Input`](#schema-paper%20input)

**Responses**

- **404** — Input papers not found
  - Body: [`Error404`](#schema-error404)
- **400** — Bad query parameters
  - Body: [`Error400`](#schema-error400)
- **200** — List of recommendations with default or requested fields
  - Body: [`Paper%20Recommendations`](#schema-paper%20recommendations)

---

### `GET /papers/forpaper/{paper_id}` — Get recommended papers for a single positive example paper

_Tags: Paper Recommendations_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `from` | query | no | string enum: recent, all-cs | Which pool of papers to recommend from. |
| `limit` | query | no | integer | How many recommendations to return. Maximum 500. |
| `fields` | query | no | string | A comma-separated list of the fields to be returned. See the contents of the <code>recommendedPapers</code> array in Response Schema below for a list of all available fields that can be returned.<br><br> The <code>paperId</code> field is always returned. If the fields parameter is omitted, only the <code>paperId</code> and <code>title</code> will be returned.<br><br> Examples: <code>http://api.semanticscholar.org/recommendations/v1/papers?fields=title,url,authors</code> |
| `paper_id` | path | yes | string |  |

**Responses**

- **404** — Input papers not found
  - Body: [`Error404`](#schema-error404)
- **400** — Bad query parameters
  - Body: [`Error400`](#schema-error400)
- **200** — List of recommendations with default or requested fields
  - Body: [`Paper%20Recommendations`](#schema-paper%20recommendations)

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


### Schema: `Paper Recommendations` <a id="schema-paper recommendations"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `recommendedPapers` | array of [`BasePaper`](#schema-basepaper) | no |  |


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


### Schema: `Paper Input` <a id="schema-paper input"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `positivePaperIds` | array of string | no |  |
| `negativePaperIds` | array of string | no |  |

