# db

Local-only helpers for the centaur Postgres. Nothing here ships to the cluster.

## Setup (once)

```bash
brew install uv
cd db
uv sync
uv run python -m ipykernel install --user \
    --name centaur-db --display-name "centaur-db (.venv)"
```

The `ipykernel install` registers `db/.venv` as a named Jupyter kernel that
Cursor / VS Code can find. `notebooks/explore.ipynb` is pinned to this
kernel, so once it's registered the notebook auto-selects the right
interpreter on open.

## Using it

Open `db/notebooks/explore.ipynb` in Cursor. Run the cells. Everything
needed (port-forward to in-cluster Postgres, password fetch, BM25 example)
is in the notebook.

```python
import centaur_db as db

conn = db.connect()
db.query(conn, "SELECT count(*) FROM api_keys")
```

`db.connect()` spawns a `kubectl port-forward` to the in-cluster Postgres
on `localhost:5432` if one isn't already running, and tears it down on
kernel shutdown.

## Slack ETL & BM25

Slack ingestion is wholly owned by centaur's scheduler — see
`.centaur/docs/public/md/operate/slack-etl.md`. Toggle it in
`values.local.yaml` (`api.slackEtlEnabled` + `api.slackSyncBackfillLookbackDays`)
and let the workflows tick on their chart-configured interval.

Centaur's Postgres is the `paradedb/paradedb` image with `pg_search`
preloaded, so BM25 operators work natively:

```sql
SELECT title, paradedb.score(document_id) AS score
FROM company_context_documents
WHERE body @@@ 'your query'
ORDER BY score DESC LIMIT 5;
```

## External GUI (rare)

If you want to point Postico / DBeaver / pgcli at the database instead of
using the notebook:

```bash
kubectl port-forward -n centaur-system svc/centaur-centaur-postgres 5432:5432 &
kubectl get secret -n centaur-system centaur-infra-env \
    -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d
```

Then connect to `postgres://tempo:<password>@localhost:5432/ai_v2`.
