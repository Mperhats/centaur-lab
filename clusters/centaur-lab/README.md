# clusters/centaur-lab

GitOps manifests for the **production** centaur-lab cluster, organized to
match [`paradigmxyz/centaur-acme-infra`](https://github.com/paradigmxyz/centaur-acme-infra).

This directory is not used during local laptop development — see the root
[`README.md`](../../README.md) and root `Justfile` for that path. On a
laptop, `just up` does Helm directly with `values.dev.yaml + values.org.yaml
+ values.local.yaml`.

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
