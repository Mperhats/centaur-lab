---
title: Using an overlay - Centaur
description: Package and mount organization-specific Centaur tools, workflows, skills, personas, and prompts without forking the base repo.
url: https://centaur.run/extend/overlay
site: Centaur
generator: Waku
---

[![Logo](/brand/lockup-black.svg)![Logo](/brand/lockup-white.svg)](/)

[![Logo](/brand/lockup-black.svg)![Logo](/brand/lockup-white.svg)](/)

Search...

⌘

K

# Using an overlay

Use an overlay when your deployment needs organization-specific tools, workflows, skills, personas, prompts, or sandbox files without turning the base Centaur repo into a fork.

An overlay is a separate repo packaged as an image. The Helm chart mounts that image into the API and into sandbox pods. API-loaded extension points, such as tools and workflows, use the API mount. Sandbox-loaded extension points, such as skills and prompts, use the sandbox mount.

## Overlay layout

```
centaur-overlay/
├── Dockerfile
├── tools/
│   └── warehouse/
│       ├── client.py
│       └── pyproject.toml
├── workflows/
│   └── nightly_report.py
├── .agents/
│   └── skills/
│       └── incident-response/
│           └── SKILL.md
└── services/
    └── sandbox/
        └── SYSTEM_PROMPT.md
```

Only include the directories your deployment needs.

## Mount paths

The same overlay image is mounted in two places:

| Runtime | Mount | Used for |
| --- | --- | --- |
| API | `/app/overlay/org` | Tool discovery, workflow discovery, overlay migrations, API-side prompt assembly. |
| Sandbox | `/home/agent/overlay/org` | Skills, persona files, sandbox prompt overlay, runtime files available to agents. |

Do not use the sandbox path when debugging API discovery. If a tool or workflow is missing, inspect `/app/overlay/org` in the API container. If a skill or prompt overlay is missing, inspect `/home/agent/overlay/org` in the sandbox.

## Discovery paths

When `overlay.image.repository` is configured, the chart adds the overlay to the API discovery paths:

```
TOOL_DIRS=/app/tools:/app/overlay/org/tools
WORKFLOW_DIRS=/app/workflows:/app/overlay/org/workflows
```

Later directories can shadow earlier entries. That means an overlay can intentionally replace a base tool or workflow with the same name.

Sandbox pods receive:

```
CENTAUR_OVERLAY_DIR=/home/agent/overlay/org
```

The sandbox entrypoint copies overlay skills from `$CENTAUR_OVERLAY_DIR/.agents/skills` into the agent workspace during startup. The active deployment block in the sandbox prompt also states whether an overlay is loaded and where it is mounted.

## Package the image

Use an image that copies the overlay repo into `/overlay`:

```
FROM alpine:3.20
WORKDIR /overlay
COPY . /overlay
```

Configure the chart with the image and source path:

```
overlay:
  image:
    repository: ghcr.io/your-org/centaur-overlay
    tag: sha-abc123
    pullPolicy: IfNotPresent
    sourcePath: /overlay
```

## Verify the overlay

Check the runtime payload for a thread:

```
curl -s "$CENTAUR_API_URL/agent/runtime?key=$THREAD_KEY" \
  -H "X-Api-Key: $CENTAUR_API_KEY" | jq '.overlay'
```

For API-loaded extensions, verify from the API deployment:

```
kubectl exec -n centaur-system deploy/centaur-centaur-api -- \
  sh -lc 'echo "$TOOL_DIRS"; echo "$WORKFLOW_DIRS"; ls -la /app/overlay/org'
```

For sandbox-loaded extensions, verify from a sandbox or ask the running agent to inspect:

```
echo "$CENTAUR_OVERLAY_DIR"
ls "$CENTAUR_OVERLAY_DIR"
ls "$CENTAUR_OVERLAY_DIR/.agents/skills"
```

If something is missing, check the overlay image contents first, then the chart values, image tag, `sourcePath`, and the API or sandbox mount path relevant to the extension type.

Copy page for AI

Ask AI...

⌘

I

---

Powered by [curl.md](https://curl.md)