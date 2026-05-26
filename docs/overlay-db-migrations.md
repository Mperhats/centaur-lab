# Overlay DB migrations

Overlay-owned Postgres schema changes run on every API pod startup,
tracked separately from upstream's. This doc covers only the bits
specific to migrations — the overlay layout, mount paths, and image
packaging are documented upstream in
[`.centaur/docs/public/md/extend/overlay.md`](../.centaur/docs/public/md/extend/overlay.md).
**Read that first.** Then come back here.

## How dbmate finds overlay migrations

`api.db.run_migrations()`
(`.centaur/services/api/api/db.py:115-174`) iterates the
`MigrationSet`s returned by `get_migration_sets()`:

| Set | Source dir | Tracking table |
|-----|-----------|----------------|
| `core` | `.centaur/services/api/db/migrations/` | `schema_migrations` |
| `overlay` | `${CENTAUR_OVERLAY_DIR}/services/api/db/migrations/` | `schema_migrations_overlay` |

`CENTAUR_OVERLAY_DIR` resolves to `/app/overlay/org` in the API
container, so the overlay migrations dir resolves to
`/app/overlay/org/services/api/db/migrations/`. The overlay set is
added only when the env var is set AND the directory exists on disk.

Separate tracking tables mean the two streams version independently —
upstream can release a `037_*.sql` while we ship `20260601000001_*.sql`
without collision.

## Authoring a migration

dbmate's timestamp convention:

```
overlay/services/api/db/migrations/YYYYMMDDHHMMSS_<short_name>.sql
```

Each file has `-- migrate:up` and `-- migrate:down`. dbmate is
**strictly forward-only at runtime**: any version not present in
`schema_migrations_overlay` is applied in lexical order, then the
version is inserted into the tracking table. It never rolls back or
re-runs an already-applied version — even if the source changed.

Example:
[`overlay/services/api/db/migrations/20260526000001_add_paper_archives.sql`](../overlay/services/api/db/migrations/20260526000001_add_paper_archives.sql).

## How files reach the pod

The overlay image must include `services/`. Upstream's canonical
`COPY . /overlay` Dockerfile pattern ships every overlay-extensible
surface automatically; per-directory `COPY tools / COPY workflows / ...`
selective patterns are an anti-pattern — they silently drop any future
surface upstream adds. (See
[`overlay/Dockerfile`](../overlay/Dockerfile) for the active recipe and
[`overlay/.dockerignore`](../overlay/.dockerignore) for what's
excluded.)

Verify after a deploy:

```bash
kubectl exec -n centaur-system deploy/centaur-centaur-api -c api -- \
  ls /app/overlay/org/services/api/db/migrations/
```

If the directory is missing, the API pod logs `migrations_dir_missing`
(warn level) at startup and silently skips the overlay set —
applications that depend on overlay-only tables then fail at runtime
with `relation "<name>" does not exist`.

## Pitfalls

### Stale tracking-table markers

If a migration version is recorded in `schema_migrations_overlay` but
the table the migration was supposed to create no longer exists in the
DB (e.g. someone applied it manually from a laptop, then the table got
dropped during a Postgres restore), dbmate will silently skip the
version on the next pod start. Fix:

```sql
DELETE FROM schema_migrations_overlay WHERE version = '<version>';
```

Roll the API pod and dbmate will re-apply.

### Rewriting an already-applied migration

Same trap — the tracking-table version pins the old definition.
**Always write a new dated file** instead of editing an applied one.

### Deleting an already-applied migration file

dbmate doesn't complain (it only iterates files on disk and inserts
markers; it doesn't audit that every applied version still has a
file). The schema then becomes unreproducible from the repo. Don't do
this unless the version was never deployed anywhere.
