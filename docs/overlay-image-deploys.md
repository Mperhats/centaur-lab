# Overlay image deploys

The centaur-overlay image carries every org-specific tool, workflow and skill
(see `overlay/`). On every push to `main` the
[`Overlay` workflow](../.github/workflows/overlay.yml) builds and pushes:

- `ghcr.io/mperhats/centaur-lab/centaur-overlay:sha-<7char>` — immutable
- `ghcr.io/mperhats/centaur-lab/centaur-overlay:latest`     — tracks `main`

The chart references the image through `overlay.image.{repository,tag}` and
the API pod's `overlay-bootstrap` initContainer copies it into a shared
`overlay-root` volume the API + sandboxes mount as `/app/overlay/org`.

## The three deploy modes

`just deploy` (and the wrappers that call it) resolves the overlay image
tag with the following precedence, top wins:

| # | Trigger                            | Repository                                            | Tag                                  |
|---|------------------------------------|-------------------------------------------------------|--------------------------------------|
| 1 | `$OVERLAY_TAG` env var set         | `values.org.yaml` default (GHCR)                      | `$OVERLAY_TAG`                       |
| 2 | `overlay/.tag` file exists         | overridden to bare `centaur-overlay` (local Docker)   | contents of `overlay/.tag`           |
| 3 | neither                            | `values.org.yaml` default (GHCR)                      | `sha-<git rev-parse --short=7 origin/main>` |

Mode 3 is the default and the recommended steady-state workflow.

## After every merge to main: `just refresh-overlay`

CI publishes a new GHCR image within ~3 minutes of merging to main. Bring
the cluster onto it with:

```bash
just refresh-overlay
```

The recipe:

1. Removes `overlay/.tag` so a stale local build from a sibling worktree
   can't pin the deploy to an image that isn't in GHCR.
2. Runs `just deploy` in mode 3 — fetches `origin/main`, resolves its short
   sha, and `helm upgrade --install`s the chart with
   `overlay.image.tag=sha-<sha>`.
3. `kubectl rollout restart deployment/<release>-centaur-api` so the
   `overlay-bootstrap` initContainer re-runs against the new tag.
4. `just overlay::clean-sandboxes slack` so the next Slack turn cold-spawns
   a sandbox with the freshly-mounted overlay.

To deploy a specific historical sha:

```bash
OVERLAY_TAG=sha-1b6cb08 just deploy
kubectl rollout restart deployment/centaur-centaur-api -n centaur-system
```

## First-time setup: GHCR pull secret

The `mperhats/centaur-lab/centaur-overlay` package is private, so the chart's
`global.imagePullSecrets: [{name: ghcr-pull}]` references a
docker-registry Secret the kubelet uses on every overlay-image pull.

Set `GHCR_USERNAME` and `GHCR_TOKEN` (see `.env.example`), then run:

```bash
just bootstrap-ghcr-pull-secret
```

This is also wired into `just bootstrap-secrets` (which `just up` calls), so
fresh clones get the secret on their first `just up`.

If `GHCR_TOKEN` is unset, the recipe prints a `skip` line and exits 0.
You can still run in mode 2 (local Docker build) without it.

If pods get stuck on `ImagePullBackOff` after a `refresh-overlay`, check:

```bash
kubectl get events -n centaur-system --field-selector reason=Failed --sort-by=.lastTimestamp | tail -10
kubectl get secret ghcr-pull -n centaur-system -o yaml
```

A `401 Unauthorized` or `denied: denied` means `GHCR_TOKEN` is missing
`read:packages` or has expired. Rotate the PAT, re-export it, re-run
`just bootstrap-ghcr-pull-secret`, then `kubectl rollout restart` the
affected deployment.

## Local-build (mode 2): fast inner loop

For sub-minute "edit overlay code → see it in the cluster" iterations on
Docker Desktop, skip GHCR entirely:

```bash
just overlay::reload
```

That recipe `docker build`s `centaur-overlay:sha-<HEAD>` into the host
Docker daemon, writes the sha to `overlay/.tag`, runs `just deploy` (which
takes the mode-2 path: bare repository + local tag, `pullPolicy: IfNotPresent`),
restarts the API, and recycles Slack sandboxes. No GHCR access required.

The `.tag` file is `.gitignore`'d. Delete it (or run `just refresh-overlay`)
to flip back to mode 3.

## Why this matters when you run multiple worktrees

Before this change the chart referenced `centaur-overlay:<tag>` without a
registry prefix. The kubelet (in `pullPolicy: IfNotPresent`) resolved it
against the host Docker daemon first, so the most recent `docker build` in
*any* worktree won — overlay from worktree A would silently overwrite
worktree B's deployment. Mode 1/3 fully qualify the repository as
`ghcr.io/mperhats/centaur-lab/centaur-overlay`, which the kubelet can only
satisfy by pulling from GHCR. Mode 2 keeps the bare-repo fallback for the
local-Docker-Desktop dev loop, but only when you explicitly opt in by
running `just overlay::build` (or `just overlay::reload`) first.
