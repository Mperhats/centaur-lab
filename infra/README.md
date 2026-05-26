# centaur-lab production infra (skeleton)

GitOps manifests for a **production** Centaur cluster. This directory is not used on a laptop.

## Local development

On your machine, use the root `Justfile`:

```bash
just up    # helm: values.dev.yaml + values.org.yaml + values.local.yaml
```

No Argo CD locally. Overlay images are built with `just overlay::build` and tagged `sha-<short>` (see `overlay/Justfile`).

## Production

1. Fill placeholders in `argocd/application.yaml` and `argocd/values/centaur.yaml`.
2. Build and push Centaur base images and `ghcr.io/<org>/centaur-overlay:sha-<tag>` from CI.
3. Pin `targetRevision` on the Centaur chart source to the same commit as the `.centaur` submodule (example: `6a96324cec90a63f20723945a8a82de0bf4ec97f`).
4. Create `centaur-infra-env` in `centaur-system` (Slack, 1Password Connect, model keys via Centaur credential sources).
5. After Argo CD is installed: `kubectl apply -f infra/argocd/application.yaml`.

Pattern reference: [Centaur ACME example](https://centaur.run/extend/acme-example) and `paradigmxyz/centaur-acme-infra`.

## Layout

```text
infra/
├── README.md
└── argocd/
    ├── application.yaml      # Argo Application (template)
    └── values/
        └── centaur.yaml      # Helm values overlay for prod
```
