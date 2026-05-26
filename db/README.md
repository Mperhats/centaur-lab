# db

Local-only helpers for the centaur Postgres. Nothing here ships to the cluster.

## Setup (once)

```bash
brew install uv
cd db
uv sync
uv run python -m ipykernel install --user \
    --name centaur-db --display-name "centaur-db (.venv)"
uv run nbstripout --install --attributes ../.gitattributes
uv tool install pre-commit
cd .. && pre-commit install
```

- `ipykernel install` registers `db/.venv` as a named Jupyter kernel that
  Cursor / VS Code can find. The notebooks under `db/notebooks/` are
  pinned to this kernel, so they auto-select the right interpreter on open.
- `nbstripout --install` registers a git **clean filter** for `*.ipynb`
  (path set in the committed `.gitattributes`). Notebook outputs are
  silently stripped on every `git add` — outputs stay visible in your
  working copy; git only ever sees the cleaned version. `--required` is
  set, so if the filter is somehow missing on a clone, `git add` of a
  notebook fails loudly rather than passing through unfiltered.
- `pre-commit install` registers a git pre-commit hook (config lives in
  the committed `.pre-commit-config.yaml`) that runs `nbstripout` over
  every staged notebook as a **second-layer safety net** for the rare
  case where the clean filter is bypassed. The hook fails the commit if
  any staged notebook still contains outputs.

## Using it

Two purpose-specific notebooks live under `db/notebooks/`:

- [`slack.ipynb`](notebooks/slack.ipynb) — Slack ETL projection in
  `company_context_documents`, raw `slack_sync_*` tables, BM25 search via
  ParadeDB.
- [`papers.ipynb`](notebooks/papers.ipynb) — Semantic Scholar rows written
  by the `save_papers` and `research_brief` workflows, briefs joined to
  their child papers, BM25 over the indexed lane.

Open either in Cursor. Run the cells — port-forward, password fetch, and
queries are all wired up.

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
`.centaur/docs/public/md/operate/slack-etl.md`. Toggle it via the chart's
`api.slackEtlEnabled` + `api.slackSyncBackfillLookbackDays` keys in
`clusters/centaur-lab/argocd/values/centaur.yaml` (or your local
`values.local.yaml` override) and let the workflows tick on their
chart-configured interval.

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
