<h4 align="center">
    Scientific-research overlay for Centaur.
</h4>

<p align="center">
  Autonomous AI-Scientist-v2 BFTS experiment trees driven through Centaur's
  durable agent + sandbox API, with Semantic Scholar lookups and
  BM25-searchable paper archives for literature grounding.
</p>

## Quickstart

```bash
git submodule update --init --recursive
uv sync && uv run pytest tests/
docker build -t centaur-overlay:dev .   # smoke test; CI publishes to GHCR on merge to main
```

Production deploys run from CI; cluster Helm values + Argo CD apps live in
a sibling `centaur-scientist-infra` repo.

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
Dockerfile                       # COPY . /overlay (alpine)
Dockerfile.bfts-executor         # python:3.11-slim BFTS sandbox runtime image
pyproject.toml                   # single dev/test venv aggregating tool + workflow + bfts_sdk deps
.centaur/                        # pinned upstream centaur submodule
.scientist/                      # pinned AI-Scientist-v2 submodule (BFTS reference)
```

The repo follows the
[`paradigmxyz/centaur-acme`](https://github.com/paradigmxyz/centaur-acme)
overlay layout. For background on the model itself, see
[Using an overlay](https://centaur.run/extend/overlay). BFTS internals are
ported from
[SakanaAI/AI-Scientist-v2](https://github.com/SakanaAI/AI-Scientist-v2).

## License

[Apache-2.0 OR MIT](LICENSE).
