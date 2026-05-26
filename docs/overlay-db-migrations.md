# Overlay DB migrations

This overlay ships its own Postgres schema changes alongside upstream
Centaur's migrations. Both run via `dbmate` on every API pod startup,
but they are tracked in **separate** tables so the two streams version
independently.

## How it works at runtime

`api.db.run_migrations()` (in `.centaur/services/api/api/db.py`) runs
each `MigrationSet` returned by `get_migration_sets()` in order:

1. **Core set** — `.centaur/services/api/db/migrations/`, tracked in
   `schema_migrations`. Owned by upstream Centaur.
2. **Overlay set** — `${CENTAUR_OVERLAY_DIR}/services/api/db/migrations/`,
   tracked in `schema_migrations_overlay`. Only added when the env var
   is set AND the directory exists on disk.

`CENTAUR_OVERLAY_DIR` is `/app/overlay/org` in the API container (the
mount path documented in
`.centaur/docs/public/md/extend/overlay.md:45`), so the overlay
migrations dir resolves to
`/app/overlay/org/services/api/db/migrations/`.

## How files reach the pod

`overlay/Dockerfile` must `COPY services /overlay/services` so the
chart's `overlay-bootstrap` initContainer can mount the migrations
directory inside the API pod. Verify after a deploy with:

```bash
kubectl exec -n centaur-system deploy/centaur-centaur-api -c api -- \
  ls /app/overlay/org/services/api/db/migrations/
```

If the directory is missing, the API pod logs
`migrations_dir_missing` (warn level) at startup and skips the
overlay set — applications that depend on overlay-only tables will
then fail with `relation "<name>" does not exist`.

## Authoring a migration

File names follow dbmate's timestamped convention:

```
overlay/services/api/db/migrations/YYYYMMDDHHMMSS_<short_name>.sql
```

Each file has `-- migrate:up` and `-- migrate:down` sections. Example:
`overlay/services/api/db/migrations/20260526000001_add_paper_archives.sql`.

dbmate is **strictly forward-only at runtime**: it applies any version
not present in `schema_migrations_overlay`, in lexical order, then
inserts the version. It never rolls back or re-runs an already-applied
version, even if the source file changed.

## Pitfalls

- **Adding `COPY services` after a migration was already manually
  applied to the cluster.** The version is in
  `schema_migrations_overlay` but the table may have been dropped
  (e.g. by a Postgres restore). dbmate skips the migration on the
  next pod start because the marker says it's applied, so the table
  stays missing. Fix: `DELETE FROM schema_migrations_overlay WHERE
  version = '<version>';` and roll the API pod so dbmate re-runs it.
- **Renaming or rewriting a published migration file.** Same trap —
  the marker pins the old version. Always write a new dated file
  instead of editing an applied one.
- **Deleting an applied migration file.** dbmate doesn't complain
  (it only iterates files on disk), so the schema becomes
  unreproducible from the repo. Don't do this unless the version was
  never deployed anywhere.
