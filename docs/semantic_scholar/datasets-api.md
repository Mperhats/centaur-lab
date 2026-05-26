# S2AG Datasets

_Version 1.0_

Base URL: `https://api.semanticscholar.org/datasets/v1`

Download full-corpus datasets from the Semantic Scholar Academic Graph (S2AG)
    <br><br>
    Some python demonstrating usage of the datasets API:
    
    r1 = requests.get('https://api.semanticscholar.org/datasets/v1/release').json()
    print(r1[-3:])
    ['2023-03-14', '2023-03-21', '2023-03-28']

    r2 = requests.get('https://api.semanticscholar.org/datasets/v1/release/latest').json()
    print(r2['release_id'])
    2023-03-28

    print(json.dumps(r2['datasets'][0], indent=2))
    {
        "name": "abstracts",
        "description": "Paper abstract text, where available. 100M records in 30 1.8GB files.",
        "README": "Semantic Scholar Academic Graph Datasets The "abstracts" dataset provides..."
    }

    r3 = requests.get('https://api.semanticscholar.org/datasets/v1/release/latest/dataset/abstracts').json()
    print(json.dumps(r3, indent=2))
    {
      "name": "abstracts",
      "description": "Paper abstract text, where available. 100M records in 30 1.8GB files.",
      "README": "Semantic Scholar Academic Graph Datasets The "abstracts" dataset provides...",
      "files": [
        "https://ai2-s2ag.s3.amazonaws.com/dev/staging/2023-03-28/abstracts/20230331_0..."
      ]
    }

Default response media types: `application/json`

Default request media types: `application/json`

## Endpoints

### `GET /diffs/{start_release_id}/to/{end_release_id}/{dataset_name}` — Download Links for Incremental Diffs

Full datasets can be updated from one release to another to avoid
downloading and processing data that hasn't changed. This method returns
a list of all the "diffs" that are required to catch a given dataset up
from its current release to a newer one.

Each "diff" represents changes between two sequential releases, and
contains two lists of files, an "updated" list and a "deleted" list.
Records in the "updated" list need to be inserted or replaced by their
primary key. Records in the "deleted" list should be removed.

Example code for updating a database or key/value store:

    difflist = requests.get('https://api.semanticscholar.org/datasets/v1/diffs/2023-08-01/to/latest/papers').json()
    for diff in difflist['diffs']:
        for url in diff['update_files']:
            for json_line in requests.get(url).iter_lines():
                record = json.loads(json_line)
                datastore.upsert(record['corpusid'], record)
        for url in diff['delete_files']:
            for json_line in requests.get(url).iter_lines():
                record = json.loads(json_line)
                datastore.delete(record['corpusid'])

Example code for updating via a join in Spark:

    current = sc.textFile('s3://curr-dataset-location').map(json.loads).keyBy(lambda x: x['corpusid'])
    updates = sc.textFile('s3://diff-updates-location').map(json.loads).keyBy(lambda x: x['corpusid'])
    deletes = sc.textFile('s3://diff-deletes-location').map(json.loads).keyBy(lambda x: x['corpusid'])

    updated = current.fullOuterJoin(updates).mapValues(lambda x: x[1] if x[1] is not None else x[0])
    updated = updated.fullOuterJoin(deletes).mapValues(lambda x: None if x[1] is not None else x[0]).filter(lambda x: x[1] is not None)
    updated.values().map(json.dumps).saveAsTextFile('s3://updated-dataset-location')

_Tags: Incremental Updates_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `dataset_name` | path | yes | string | Name of the dataset |
| `end_release_id` | path | yes | string | ID of the release the client wishes to update to, or 'latest' for the most recent release |
| `start_release_id` | path | yes | string | ID of the release held by the client |

**Responses**

- **200** — List of download links for one dataset between given releases
  - Body: [`Dataset%20Diff%20List`](#schema-dataset%20diff%20list)

---

### `GET /release/` — List of Available Releases

Releases are identified by a date stamp such as "2023-08-01". Each release contains full data for each dataset.

_Tags: Release Data_
**Responses**

- **200** — List of Available Releases
  - Body: array of string

---

### `GET /release/{release_id}` — List of Datasets in a Release

Metadata describing a particular release, including a list of datasets available.

_Tags: Release Data_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `release_id` | path | yes | string | ID of the release |

**Responses**

- **200** — Contents of the release with the given ID
  - Body: [`Release%20Metadata`](#schema-release%20metadata)

---

### `GET /release/{release_id}/dataset/{dataset_name}` — Download Links for a Dataset

Datasets are partitioned and stored on S3. Clients can retrieve them by requesting this list
of pre-signed download urls and fetching all the partitions.

_Tags: Release Data_

**Parameters**

| Name | In | Required | Type | Description |
| --- | --- | --- | --- | --- |
| `dataset_name` | path | yes | string | Name of the dataset |
| `release_id` | path | yes | string | ID of the release |

**Responses**

- **200** — Description and download links for the given dataset within the given release
  - Body: [`Dataset%20Metadata`](#schema-dataset%20metadata)

---

## Schemas

### Schema: `Release Metadata` <a id="schema-release metadata"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `release_id` | string | no |  |
| `README` | string | no | License and usage |
| `datasets` | array of [`Dataset Summary`](#schema-dataset summary) | no | Dataset metadata |


### Schema: `Dataset Summary` <a id="schema-dataset summary"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | no | Dataset name |
| `description` | string | no | Description of the data in the dataset |
| `README` | string | no | Documentation and attribution for the dataset |


### Schema: `Dataset Metadata` <a id="schema-dataset metadata"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | no | Name of the dataset |
| `description` | string | no | Description of the data contained in this dataset. |
| `README` | string | no | License and usage |
| `files` | array of string | no | Temporary, pre-signed download links for dataset files |


### Schema: `Dataset Diff List` <a id="schema-dataset diff list"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `dataset` | string | no | Dataset these diffs are for. |
| `start_release` | string | no | Beginning release, i.e. the release currently held by the client. |
| `end_release` | string | no | Ending release, i.e. the release the client wants to update to. |
| `diffs` | array of [`Dataset Diff`](#schema-dataset diff) | no | List of diffs that need to be applied to bring the dataset at 'start_release' up to date with 'end_release'. |


### Schema: `Dataset Diff` <a id="schema-dataset diff"></a>

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `from_release` | string | no | Basline release for this diff. |
| `to_release` | string | no | Target release for this diff. |
| `update_files` | array of string | no | List of files that contain updates to the dataset. Each record in these files needs to be insterted or updated. |
| `delete_files` | array of string | no | List of files that contain deletes from the dataset. Each record in these files needs to be deleted. |

