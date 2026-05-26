# clusters/centaur-lab

GitOps manifests for the **production** centaur-lab cluster, organized to
match [`paradigmxyz/centaur-acme-infra`](https://github.com/paradigmxyz/centaur-acme-infra).

This directory is not used during local laptop development — see the root
[`README.md`](../../README.md) for that path. On a laptop, `helm upgrade
--install` runs directly against `.centaur/contrib/chart/values.dev.yaml`
plus an optional gitignored `values.local.yaml` you write yourself
(start by copying [`argocd/values/centaur.yaml`](argocd/values/centaur.yaml)
and paring it down to local overrides).

## Layout

```text
clusters/centaur-lab/
├── README.md           # this file
└── argocd/
    ├── bootstrap/
    │   └── centaur.yaml  # Argo CD Application (chart + overlay images)
    ├── values/
    │   └── centaur.yaml  # Helm values overlay for prod
    └── apps/             # optional raw manifests
```

## Production deploy

1. Fill placeholders in `argocd/bootstrap/centaur.yaml` and `argocd/values/centaur.yaml`:
   - `<CENTAUR_CHART_SHA>` — pin to the same commit as the `.centaur` submodule
     (currently `0656aeb56c9e6e98507494cfb1c0408ffbf57b65`).
   - `<CENTAUR_IMAGE_TAG>` — sha tag from the upstream Centaur image build.
   - `<OVERLAY_TAG>` — sha tag from this repo's CI overlay publish job
     (`.github/workflows/overlay.yml` — pushes to `ghcr.io/<org>/<repo>/centaur-overlay:sha-*`).
   - `<ORG>` / `<INFRA_REF>` — Git repo + ref the values source pulls from.
2. Build and push base images via upstream Centaur CI.
3. Create `centaur-infra-env` in the `centaur-system` namespace
   (Slack, model keys, 1Password Connect credentials).
4. After Argo CD is installed:
   ```bash
   kubectl apply -f clusters/centaur-lab/argocd/bootstrap/centaur.yaml
   ```

## Pattern reference

[`paradigmxyz/centaur-acme-infra`](https://github.com/paradigmxyz/centaur-acme-infra)
documents the canonical `clusters/<name>/argocd/{bootstrap,values,apps}/`
layout this directory mirrors.
