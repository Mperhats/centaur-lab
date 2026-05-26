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
.agents/skills/         # sandbox-loaded skills (academic-research)
services/               # overlay-side migrations + sandbox prompt overlay
tools/                  # API-discovered tool plugins
  bfts_executor/        #   agent-sandbox Sandbox CRD driver
  bfts_vlm/             #   VLM plot review
  semantic_scholar/     #   S2 Graph API + research-brief
workflows/              # durable workflow handlers (bfts_*, ideation, save_papers, ...)
centaur_lab/            # shared renderer / persistence / metrics helpers
tests/                  # ACME-style root pytest suite
Dockerfile              # COPY . /overlay (alpine)
Dockerfile.bfts-executor # python:3.11-slim BFTS sandbox runtime image
pyproject.toml          # single dev/test venv shared by tools + workflows
.centaur/               # pinned upstream centaur submodule
.scientist/             # pinned AI-Scientist-v2 submodule (BFTS reference)
centaur_sdk/            # dev-only symlink → .centaur/centaur_sdk
```

The repo follows the
[`paradigmxyz/centaur-acme`](https://github.com/paradigmxyz/centaur-acme)
overlay layout. For background on the model itself, see
[Using an overlay](https://centaur.run/extend/overlay). BFTS internals are
ported from
[SakanaAI/AI-Scientist-v2](https://github.com/SakanaAI/AI-Scientist-v2).

## License

[Apache-2.0 OR MIT](LICENSE).
