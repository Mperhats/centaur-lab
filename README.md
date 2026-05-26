<h4 align="center">
    Academic-research overlay for Centaur.
</h4>

<p align="center">
  Find, summarize, and persist peer-reviewed papers and preprints into durable,
  BM25-searchable rows via Centaur's overlay model.
</p>

<p align="center">
  <a href="#what-this-is">What this is</a> •
  <a href="#repository-map">Map</a> •
  <a href="#build-the-overlay-image">Build</a> •
  <a href="#use-with-helm">Helm</a> •
  <a href="#verify-in-a-running-deployment">Verify</a>
</p>

## What this is

`centaur-lab` is the organization overlay for our [Centaur](https://github.com/paradigmxyz/centaur)
deployment, specialized for academic research — Semantic Scholar lookups, PDF
parsing, and BM25-indexed paper archives in `company_context_documents`.

The repo mirrors the
[`paradigmxyz/centaur-acme`](https://github.com/paradigmxyz/centaur-acme)
overlay shape. The cluster GitOps that pins the image this repo publishes
lives in a sibling `centaur-lab-infra` repo (shaped after
[`paradigmxyz/centaur-acme-infra`](https://github.com/paradigmxyz/centaur-acme-infra)).

The overlay image is copied into Centaur at runtime:

```text
centaur-lab repo
    |
    v
overlay image (~4 MiB)
    |
    +-- /app/overlay/org in the API
    +-- /home/agent/overlay/org in sandbox pods
```

## Repository Map

```text
.
├── .agents/skills/academic-research/    # sandbox skill loaded with the overlay
├── services/sandbox/SYSTEM_PROMPT.md    # org sandbox prompt overlay
├── services/api/db/migrations/          # overlay-owned SQL (dbmate)
├── tools/
│   ├── pdf/                             # public-HTTP PDF fetch + parse
│   └── semantic_scholar/                # S2 Graph API + research-brief projections
├── workflows/                           # save_papers, research_brief, archive_papers, search_and_archive_papers
├── tests/                               # ACME-style root pytest suite
├── cloudflared/                         # laptop-only Cloudflare Tunnel + launchd setup
├── docs/                                # TODO + overlay-db-migrations
├── Dockerfile                           # copies the overlay to /overlay
├── pyproject.toml + uv.lock             # single-root uv project
└── .centaur/                            # pinned upstream centaur submodule
```

## Build the overlay image

```bash
docker build -t ghcr.io/<owner>/centaur-lab/centaur-overlay:local .
```

The image copies this repository to `/overlay`. Centaur's Helm chart mounts
that path at `/app/overlay/org` in the API and `/home/agent/overlay/org` in
sandbox pods.

CI publishes `ghcr.io/<owner>/centaur-lab/centaur-overlay:sha-<git>` on every
push to `main` — see [`.github/workflows/overlay.yml`](.github/workflows/overlay.yml).
The follow-on tag-bump that rolls cluster pods lives in `centaur-lab-infra`,
not here.

## Use with Helm

```yaml
overlay:
  image:
    repository: ghcr.io/<owner>/centaur-lab/centaur-overlay
    tag: sha-0000000
    pullPolicy: IfNotPresent
    sourcePath: /overlay
```

Cluster Helm values + Argo CD apps live in `centaur-lab-infra`. For local
laptop runs, layer a gitignored `values.local.yaml` over upstream's
`.centaur/contrib/chart/values.dev.yaml`.

## Included examples

| Path | Purpose |
|------|---------|
| [`tools/semantic_scholar/`](tools/semantic_scholar) | Search papers, fetch metadata, walk the citation graph, build research-brief bundles |
| [`tools/pdf/`](tools/pdf) | Fetch + parse open-access PDFs to Markdown (pymupdf4llm → pymupdf → pypdf fallback) |
| [`workflows/research_brief.py`](workflows/research_brief.py) | Search S2 → render lit-review → upsert to `company_context_documents` |
| [`workflows/archive_papers.py`](workflows/archive_papers.py) | Fetch PDF → parse → persist to `paper_archives` |
| [`workflows/save_papers.py`](workflows/save_papers.py) | Idempotent metadata upsert |
| [`workflows/search_and_archive_papers.py`](workflows/search_and_archive_papers.py) | Search-then-archive every match |
| [`.agents/skills/academic-research/SKILL.md`](.agents/skills/academic-research/SKILL.md) | Sandbox playbook for academic-research turns |
| [`services/sandbox/SYSTEM_PROMPT.md`](services/sandbox/SYSTEM_PROMPT.md) | Overlay sandbox prompt |

## Verify in a running deployment

From the API pod:

```bash
kubectl exec -n centaur-system deploy/centaur-centaur-api -- sh -lc \
  'echo "$TOOL_DIRS"; echo "$WORKFLOW_DIRS"; ls -la /app/overlay/org'
```

From a sandbox:

```bash
echo "$CENTAUR_OVERLAY_DIR"
ls "$CENTAUR_OVERLAY_DIR"
ls "$CENTAUR_OVERLAY_DIR/.agents/skills"
```

## Local checks

```bash
git submodule update --init --recursive
uv sync
uv run pytest tests/
uv run ruff check .
docker build -t centaur-overlay:dev .
```

## Notes

- **`.centaur/` submodule.** Pulls quadruple-duty: the `centaur_sdk`
  package (path-imported via pytest's `pythonpath = [".centaur"]` because
  upstream's `[tool.hatch.build.targets.wheel] packages = ["."]` flattens
  the wheel root and breaks `pip install`), the upstream Helm chart at
  `.centaur/contrib/chart/`, the upstream `Justfile` (`bootstrap-secrets`,
  `up`, `build`), and reference migrations. Bump the pin to track upstream
  Centaur releases.
- **`cloudflared/`** is laptop ops, not GitOps — the launchd plist + tunnel
  config that forwards public webhook traffic to a single Mac running the
  local cluster. Excluded from the overlay image. See
  [`cloudflared/README.md`](cloudflared/README.md).
- **Credential hygiene.** No secrets, `.env` files, or Helm values are
  committed here. Tools resolve credentials via `secret("…")` placeholders
  resolved by iron-proxy / iron-token-broker at the network boundary.

## License

[Apache-2.0 OR MIT](LICENSE).
