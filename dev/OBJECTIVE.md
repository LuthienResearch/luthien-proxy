# Current Objective

Fix broken migration script that prevents migrations from running properly.

## Problem

The `docker/run-migrations.sh` script uses psql variable substitution incorrectly, causing all migrations to be skipped even on fresh databases. The query to check if a migration has been applied fails with syntax errors.

## Acceptance Criteria

- [ ] Migration script correctly checks if migrations have been applied
- [ ] Fresh database runs all migrations successfully
- [ ] Re-running migrations correctly skips already-applied ones
- [ ] No SQL syntax errors in migration logs
- [ ] Test with `docker compose down -v && docker compose up -d`
