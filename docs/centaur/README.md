# Centaur — Local Documentation Mirror

Snapshot of [centaur.run](https://centaur.run/) pulled from the site's native
markdown endpoints (`/md/*.md`) plus the bundled `llms.txt` / `llms-full.txt`
files. Directory layout mirrors the URL paths.

> Pages were sourced from `/md/` (native markdown the site publishes for
> agents) rather than HTML-to-markdown conversion, so they preserve frontmatter
> and any MDX components used on the live site.

## Bundles

| File | Source | Notes |
| --- | --- | --- |
| [`llms.txt`](./llms.txt) | https://centaur.run/llms.txt | Short index of every page with one-line descriptions. Use this as a sitemap. |
| [`llms-full.txt`](./llms-full.txt) | https://centaur.run/llms-full.txt | Every page concatenated into a single agent-friendly file. |

## Pages

| Path | Source |
| --- | --- |
| [`index.md`](./index.md) | https://centaur.run/ |
| [`what-is-centaur.md`](./what-is-centaur.md) | https://centaur.run/what-is-centaur/ |
| [`quickstart.md`](./quickstart.md) | https://centaur.run/quickstart/ |
| [`architecture.md`](./architecture.md) | https://centaur.run/architecture/ |
| [`deploying-in-production.md`](./deploying-in-production.md) | https://centaur.run/deploying-in-production/ |
| [`security.md`](./security.md) | https://centaur.run/security/ |
| [`brand.md`](./brand.md) | https://centaur.run/brand/ |
| [`demo.md`](./demo.md) | https://centaur.run/demo/ |
| [`extend/acme-example.md`](./extend/acme-example.md) | https://centaur.run/extend/acme-example/ |
| [`extend/apps.md`](./extend/apps.md) | https://centaur.run/extend/apps/ |
| [`extend/overlay.md`](./extend/overlay.md) | https://centaur.run/extend/overlay/ |
| [`extend/skills.md`](./extend/skills.md) | https://centaur.run/extend/skills/ |
| [`extend/tools.md`](./extend/tools.md) | https://centaur.run/extend/tools/ |
| [`extend/workflows.md`](./extend/workflows.md) | https://centaur.run/extend/workflows/ |
| [`operate/slack-etl.md`](./operate/slack-etl.md) | https://centaur.run/operate/slack-etl/ |
| [`reference/configuration.md`](./reference/configuration.md) | https://centaur.run/reference/configuration/ |
| [`secrets/environment.md`](./secrets/environment.md) | https://centaur.run/secrets/environment/ |
| [`secrets/onepassword.md`](./secrets/onepassword.md) | https://centaur.run/secrets/onepassword/ |
| [`secrets/advanced-permissioning.md`](./secrets/advanced-permissioning.md) | https://centaur.run/secrets/advanced-permissioning/ (work-in-progress upstream) |
| [`secrets/aws-kms.md`](./secrets/aws-kms.md) | https://centaur.run/secrets/aws-kms/ (work-in-progress upstream) |
| [`secrets/gcp-secret-manager.md`](./secrets/gcp-secret-manager.md) | https://centaur.run/secrets/gcp-secret-manager/ (work-in-progress upstream) |

## Refreshing this snapshot

The site publishes a `/md/<page>.md` for every page listed in `llms.txt`, so the
fastest refresh is to walk that index:

```bash
mkdir -p docs/centaur
cd docs/centaur

curl -fsSL https://centaur.run/llms.txt      -o llms.txt
curl -fsSL https://centaur.run/llms-full.txt -o llms-full.txt

python3 - <<'PY'
import os, re, subprocess
paths = sorted(set(re.findall(r"\]\((/[^)]+)\)", open("llms.txt").read())))
for p in paths:
    out = "index.md" if p == "/index" else f"{p.lstrip('/')}.md"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    subprocess.run(
        ["curl", "-fsSL", f"https://centaur.run/md{p}.md", "-o", out],
        check=True,
    )
PY
```

> The sitemap at `https://centaur.run/sitemap.xml` only lists 11 of the 21
> documented pages, so prefer `llms.txt` as the source of truth when crawling.
