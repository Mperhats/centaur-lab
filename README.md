<h4 align="center">
    Scientific-research overlay for Centaur.
</h4>

<p align="center">
  Autonomous AI-Scientist-v2 BFTS experiment trees driven through Centaur's
  durable agent + sandbox API, with Semantic Scholar lookups for
  literature grounding.
</p>

## Quickstart

```bash
git submodule update --init --recursive
uv sync --all-packages && uv run pytest tests/
docker build -t centaur-overlay:dev .   # smoke test; CI publishes to GHCR on merge to main
```

`--all-packages` is what tells uv to install every workspace member's
runtime deps (the per-tool `tools/*/pyproject.toml` files plus
`packages/bfts_sdk/`) into the root `.venv`. Plain `uv sync` only
installs the dev group, which leaves overlay code at import-time errors.

## Repository map

```text
.agents/skills/                  # sandbox-loaded skills (academic-research)
services/                        # overlay-side migrations + sandbox prompt overlay
tools/                           # API-discovered tool plugins (one pyproject.toml per dir)
  bfts_executor/                 #   agent-sandbox Sandbox CRD driver
  bfts_vlm/                      #   VLM plot review
  semantic_scholar/              #   S2 Graph API client + projections/ + utils.py
workflows/                       # durable workflow handlers (bfts_*, ideation, save_papers, ...)
packages/                        # versioned-but-in-tree Python packages
  bfts_sdk/                      #   BFTS controller internals (config / state / expand / metric / …);
                                 #     imported as `packages.bfts_sdk.X`
  centaur_sdk → ../.centaur/centaur_sdk  # dev-only symlink for IDE / pytest resolution
tests/                           # overlay-invariant + smoke pytest suite
Dockerfile                       # COPY . /overlay (alpine)
Dockerfile.bfts-executor         # python:3.11-slim BFTS sandbox runtime image
pyproject.toml                   # uv workspace; aggregates tool + bfts_sdk deps into the dev venv
.centaur/                        # pinned upstream centaur submodule
```

The repo follows the
[`paradigmxyz/centaur-acme`](https://github.com/paradigmxyz/centaur-acme)
overlay layout. For background on the model itself, see
[Using an overlay](https://centaur.run/extend/overlay). BFTS internals
are ported from
[SakanaAI/AI-Scientist-v2](https://github.com/SakanaAI/AI-Scientist-v2)
(no longer a submodule — clone separately if you need to cross-reference
the line-cited code).

## Deploying

This repo only ships the **overlay image** (and a sibling
**bfts-executor image** that BFTS sandbox pods boot into). Deploys live
in the sibling `centaur-scientist-infra` repo (Argo CD apps + Helm
values). The contract is:

1. Merge to `main` here. CI publishes two images:
   - `ghcr.io/<owner>/centaur-scientist/centaur-overlay:sha-<sha>` (and `:latest`).
   - `ghcr.io/<owner>/centaur-scientist/bfts-executor:sha-<sha>` (and `:latest`).
2. In `centaur-scientist-infra`, bump `overlay.image.tag` and
   `BFTS_EXECUTOR_IMAGE` (in `api.extraEnv`) to the new SHA. Argo CD
   reconciles, the API pod restarts, and overlay migrations apply at
   startup (see [`docs/overlay-db-migrations.md`](docs/overlay-db-migrations.md)).

Cluster bring-up, secret bootstrap, and the `kubectl port-forward`s for
local dev are owned by the infra repo too — this repo carries no
`values.yaml`, no Argo CD manifests, and no cluster credentials.

## Updating the SDK

The `packages/centaur_sdk` symlink resolves to whatever `.centaur` is
pinned at, so syncing the SDK == bumping the submodule pin. To advance
the pin to upstream's latest, run the tests against it, and stage the
bump:

```bash
uv run scripts/sync_sdk.py
git commit -m "bump .centaur to <sha>"
```

## Adding or updating a tool

`tools/<name>/pyproject.toml` is the single source of truth for that
tool's runtime deps — both the API pod's `entrypoint.sh` (at startup)
and the local uv workspace (at `uv sync --all-packages`) read it.

**New tool.** `mkdir tools/<name>/`, drop a `pyproject.toml` with
`[project].dependencies` and `[tool.uv].package = false`. The root
`pyproject.toml`'s `[tool.uv.workspace].members = ["tools/*", "packages/bfts_sdk"]`
glob picks it up automatically; no edits to root needed.

**Bumping a tool's dep version.** Edit `tools/<name>/pyproject.toml`
only. Then `uv lock --upgrade-package <pkg>` if you want a fresh resolve.

## License

[Apache-2.0 OR MIT](LICENSE).
