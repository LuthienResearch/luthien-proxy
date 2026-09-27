---
category: Fixes
pr: 812
---

**`docker/run-migrations.sh` no longer swallows failed migrations**: `psql -f`
ran without `-v ON_ERROR_STOP=1`, so a migration file with a failing
statement would print the error, keep executing later statements in the same
file, and still exit 0 -- letting the migration get recorded as applied in
`_migrations` despite having failed. Every `psql` call in the script now goes
through an `ON_ERROR_STOP=1` wrapper, and the one remaining pipe that could
mask a `psql` failure (the "already applied?" check, previously
`psql ... | tr -d ' '`) was replaced with `-t -A` so nothing sits between
`psql`'s exit code and the script.
  - Also closes the `CREATE INDEX CONCURRENTLY IF NOT EXISTS` retry trap:
    that clause matches an existing index by name only, so an index left
    INVALID by a previously interrupted concurrent build (dropped
    connection, OOM, crash) is silently accepted as "already there" on a
    retried deploy, with `ON_ERROR_STOP` never seeing an error to catch.
    After a migration applies, the runner now scans it for any
    `CREATE INDEX CONCURRENTLY` statements and refuses to record the
    migration as applied unless `pg_index.indisvalid` is true for every
    index it names. This specifically protects `idx_request_logs_created_at`
    (#811) from silently shipping invalid.
