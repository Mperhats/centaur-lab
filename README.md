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
docker build -t centaur-overlay:dev .
```

That's it. The image at `centaur-overlay:dev` is what Centaur's Helm chart
mounts into the API and sandbox pods.

## Repository map

```text
.agents/skills/         # sandbox-loaded skills
services/               # overlay-side migrations + sandbox prompt
tools/                  # API-discovered tool plugins (pdf, semantic_scholar)
workflows/              # durable workflow handlers
tests/                  # ACME-style root pytest suite
cloudflared/            # laptop-only Cloudflare Tunnel + launchd setup
docs/                   # backlog + overlay-db migration guide
.centaur/               # pinned upstream centaur submodule
```

The repo follows the
[`paradigmxyz/centaur-acme`](https://github.com/paradigmxyz/centaur-acme)
overlay layout. For background on the model itself, see
[Using an overlay](https://centaur.run/extend/overlay).

## License

[Apache-2.0 OR MIT](LICENSE).
