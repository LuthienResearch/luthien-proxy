# CLI Auto-Fetch Proxy Design

**Date:** 2026-03-17
**Status:** Approved

## Problem

`luthien onboard` requires a pre-existing local git checkout of luthien-proxy and prompts the user for its path. The goal is `pipx install luthien-cli && luthien onboard` with zero pre-setup.

## Design

### New module: `luthien_cli/repo.py`

Manages a proxy artifact directory at `~/.luthien/luthien-proxy/`.

**`ensure_repo() -> str`** — Main entry point. Returns the managed repo path.
- If directory doesn't exist: download files, return path.
- If directory exists with `.version` file: compare stored commit SHA against GitHub API's latest main SHA. If different, prompt user to update or stay on current version. Return path.
- If directory exists without `.version`: treat as fresh download (overwrite).

**`_download_files(dest: Path)`** — Fetches two files from raw GitHub URLs (main branch):
- `docker-compose.yaml` — post-processed to remove the `./src:/app/src:ro` volume mount (not needed when running GHCR images; the image has source baked in). The `./config:/app/config:ro` mount is kept.
- `.env.example` — used as template for `.env` generation.

Creates `config/` subdirectory for policy YAML written by onboard.

Writes the current main HEAD SHA to `.version` after successful download.

**`_get_remote_sha() -> str`** — Hits GitHub API (`repos/LuthienResearch/luthien-proxy/commits/main`) to get the latest commit SHA. Uses `Accept: application/vnd.github.sha` header for a lightweight response.

Uses `httpx` (already a CLI dependency) for all HTTP requests. No git dependency.

### Changes to `onboard.py`

- Replace the `repo_path` prompt + `docker-compose.yaml` existence check with a single call to `ensure_repo()`.
- `_write_policy()` and `_ensure_env()` write into the managed directory as before.
- `config.repo_path` is set to the managed path automatically so `up`/`down`/`logs` work.
- Replace `docker compose up -d --build` with `docker compose pull && docker compose up -d` (GHCR images, no local build).

### Changes to `up.py`

- When `repo_path` is not set, call `ensure_repo()` instead of prompting for a path. This means `luthien up` also Just Works after onboard.

### No changes needed

- `down.py` and `logs.py` — already work via `config.repo_path`, which onboard sets.
- `config.py` — `repo_path` field stays, just gets auto-populated.
- `claude.py` and `status.py` — don't use repo_path.

### Version tracking

- `~/.luthien/luthien-proxy/.version` contains the commit SHA of the downloaded artifacts.
- On re-run of `ensure_repo()`, compare against remote SHA.
- If different: prompt "A newer version is available. Update? [Y/n]"
- If same or user declines: proceed with current files.
- Network errors during SHA check are non-fatal — proceed with current files and warn.

### Files downloaded

Only two files from `https://raw.githubusercontent.com/LuthienResearch/luthien-proxy/main/`:
1. `docker-compose.yaml`
2. `.env.example`

### docker-compose.yaml post-processing

Remove the `./src:/app/src:ro` volume mount line from the gateway service. This mount is for local development (hot-reload source into container). The GHCR image already has source baked in. The `./config:/app/config:ro` mount stays so the CLI-generated policy config is picked up.

Also remove `build:` blocks (context + dockerfile lines) from all services. The managed install uses pre-built GHCR images exclusively — build sections reference local Dockerfiles that don't exist in the managed directory. Docker Compose uses the `image:` field when no build context is present, but removing the blocks avoids confusing errors if a user runs `docker compose build` by accident.

Approach: simple string filtering. Both the volume mount and build blocks have stable, distinctive patterns in our docker-compose.yaml.

### Error handling

- **Fresh install, network down:** Fail with a clear error ("Could not download proxy files from GitHub. Check your internet connection."). Cannot proceed without the files.
- **Version check, network down:** Non-fatal. Warn and proceed with existing files.
- **Download of individual file fails (non-network):** Fail with error showing the URL and HTTP status.
- **GitHub rate limit (403):** Same as network error — clear message suggesting retry later.

### Testing

Unit tests for `repo.py`:
- Fresh download (mock httpx, verify files written and `.version` created)
- Version match (no prompt, no download)
- Version mismatch + user accepts update (re-downloads)
- Version mismatch + user declines (keeps current)
- Network error on SHA check with existing install (warns, proceeds)
- Network error on fresh install (fails with clear message)

Update `test_onboard.py` to mock `ensure_repo()` instead of prompting for repo path.
