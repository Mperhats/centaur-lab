# Cloudflare Tunnel

Stable public URL for the local Centaur stack so Slack and workflow webhook
providers (GitHub, etc.) can deliver events to your laptop.

- **Public URL:** `https://centaur.local-labs.xyz`
- **Routing (single hostname, two backends via path):**
  - `/api/webhooks/slack` → Slackbot (`localhost:3001`)
  - everything else (workflow webhooks, `/workflows/runs`, `/agent/*`, `/healthz`) → Centaur API (`localhost:8000`)

## Day-to-day

The tunnel runs as a launchd user agent (`com.local-labs.centaur-tunnel`),
auto-starts on login, and auto-restarts on crash. You don't manage it
per-session. The only per-session thing is the two `kubectl port-forward`s
backing the tunnel's local targets — `just dev` owns both:

```bash
just dev   # backgrounds port-forwards (3001 + 8000), tails Slackbot logs in foreground
```

Ctrl-C `just dev` to stop the port-forwards; the tunnel keeps running.

## Managing the tunnel agent

All commands live in this directory's Justfile, invoked via the `cloudflared`
module from the repo root. Service recipes are gated by Just's `[macos]`
attribute — on Linux they're simply hidden, and you'd write a sibling
systemd-user-unit variant (`[linux]`) when needed.

```bash
just cloudflared::status                 # is the agent loaded? running? pid?
just cloudflared::logs                   # tail ~/Library/Logs/centaur-tunnel.log
just cloudflared::install-service        # idempotent install / re-install
just cloudflared::uninstall-service      # remove the agent (confirms)
just --yes cloudflared::uninstall-service  # skip the confirm prompt
just cloudflared::run                    # foreground run for debugging (uninstall first to avoid connector race)
```

## One-time setup on a fresh machine

The repo holds the tunnel's *routing* (`config.yml`) and *launch agent
template* (`com.local-labs.centaur-tunnel.plist`). The tunnel's *identity*
(UUID + credentials JSON) is per-Cloudflare-account and per-machine. You need
both.

1. Install cloudflared: `brew install cloudflared`.
2. Authenticate to the Cloudflare zone that owns `local-labs.xyz`:

   ```bash
   cloudflared tunnel login
   ```

   Pick the `local-labs.xyz` zone in the browser. Writes
   `~/.cloudflared/cert.pem`.

3. Either **create a new tunnel** (if this is the first machine) or **reuse
   the existing one**:

   - First machine ever:

     ```bash
     cloudflared tunnel create centaur-dev
     cloudflared tunnel route dns centaur-dev centaur.local-labs.xyz
     ```

     This writes `~/.cloudflared/<UUID>.json` (the credentials) and
     auto-creates the DNS record.

   - Additional machine, reusing the same tunnel: copy the existing
     `~/.cloudflared/<UUID>.json` from the original machine into the same
     path on the new one. (Don't run the tunnel from two machines at the
     same time unless you want Cloudflare to round-robin between them.)

4. Confirm the routing config is valid:

   ```bash
   cloudflared tunnel --config cloudflared/config.yml ingress validate
   ```

5. Install the launch agent (one-time per machine):

   ```bash
   just cloudflared::install-service
   ```

6. Verify it's connected:

   ```bash
   just cloudflared::status   # should show state = running, pid = N
   just cloudflared::logs     # should show "Registered tunnel connection" lines
   ```

## Why a hand-rolled plist instead of `cloudflared service install`?

`cloudflared service install` is broken for locally-managed (config-file)
tunnels: it writes bare `cloudflared` into `ProgramArguments` (no subcommand),
the daemon exits immediately, and the workarounds (symlink config + `plutil`
patch) end up bigger than just writing the plist ourselves.

The template uses absolute paths everywhere so the daemon doesn't depend on
launchd's minimal environment; `install-service` substitutes the cloudflared
binary path, the repo config path, and the log path before loading.

## How it routes

```
Slack Events                              GitHub / arbitrary workflow webhooks
  POST /api/webhooks/slack                  POST /api/webhooks/<workflow-slug>
  └─> https://centaur.local-labs.xyz/...
        └─> Cloudflare edge
              └─> cloudflared (launchd agent, single hostname)
                    │
                    ├── path /api/webhooks/slack ──> localhost:3001 ──> Slackbot pod
                    │
                    └── all other paths          ──> localhost:8000 ──> Centaur API pod
                                                                          ├─ /api/webhooks/<workflow-slug>
                                                                          ├─ /workflows/runs
                                                                          ├─ /agent/*
                                                                          └─ /healthz
```

Reorder the path rules in `config.yml` carefully — cloudflared matches in
declaration order, and a hostname-only rule above the `/api/webhooks/slack`
rule would shadow the Slackbot route.

After editing `config.yml`, reload the agent:

```bash
just --yes cloudflared::uninstall-service
just cloudflared::install-service
```

## Tearing it down

To stop the tunnel agent: `just cloudflared::uninstall-service`.

To delete the tunnel entirely (e.g. rotating it):

```bash
just cloudflared::uninstall-service
cloudflared tunnel delete centaur-dev
```

This removes the launch agent, the tunnel, and its DNS record. You'd need to
redo the one-time setup if you want to use it again.
