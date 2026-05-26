<h4 align="center">
    Academic-research overlay for Centaur.
</h4>

<p align="center">
  Find, summarize, and persist peer-reviewed papers and preprints into durable,
  BM25-searchable rows via Centaur's overlay model.
</p>

## Quickstart

```bash
git submodule update --init --recursive
uv sync && uv run pytest tests/
docker build -t centaur-overlay:dev .   # smoke test; CI publishes to GHCR on merge to main
```

Production deploys come from CI, not your laptop. Every push to `main`
publishes `ghcr.io/<owner>/centaur-lab/centaur-overlay:sha-<git>` to GHCR;
the sibling [`centaur-lab-infra`](https://github.com/paradigmxyz/centaur-acme-infra)
repo pins one of those tags in cluster Helm values to roll the API + sandbox
pods.

## Repository map

```text
.agents/skills/         # sandbox-loaded skills
services/               # overlay-side migrations + sandbox prompt
tools/                  # API-discovered tool plugins (pdf, semantic_scholar)
workflows/              # durable workflow handlers
tests/                  # ACME-style root pytest suite
.centaur/               # pinned upstream centaur submodule
```

The repo follows the
[`paradigmxyz/centaur-acme`](https://github.com/paradigmxyz/centaur-acme)
overlay layout. For background on the model itself, see
[Using an overlay](https://centaur.run/extend/overlay).

## License

[Apache-2.0 OR MIT](LICENSE).
