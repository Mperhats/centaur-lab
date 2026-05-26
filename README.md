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
docker build -t centaur-overlay:dev .    # smoke test; CI publishes to GHCR on merge to main
```

Production deploys run in [CI](.github/workflows/overlay.yml)

## Repository map

```text
.agents/skills/         # sandbox-loaded skills
services/               # overlay-side migrations + sandbox prompt
tools/                  # API-discovered tool plugins (pdf, semantic_scholar)
workflows/              # durable workflow handlers
tests/                  # ACME-style root pytest suite
pyproject.toml          # single dev/test venv shared by tools + workflows
.centaur/               # pinned upstream centaur submodule
centaur_sdk/            # dev-only symlink → .centaur/centaur_sdk
```

The repo follows the
[`paradigmxyz/centaur-acme`](https://github.com/paradigmxyz/centaur-acme)
overlay layout. For background on the model itself, see
[Using an overlay](https://centaur.run/extend/overlay).

## Updating the SDK

The `centaur_sdk` symlink resolves to whatever `.centaur` is pinned at,
so syncing the SDK == bumping the submodule pin. To advance the pin to
upstream's latest, run the tests against it, and stage the bump:

```bash
uv run scripts/sync_sdk.py
git commit -m "bump .centaur to <sha>"
```

## License

[Apache-2.0 OR MIT](LICENSE).
