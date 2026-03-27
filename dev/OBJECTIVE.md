## Objective

Add local Docker build fallback when GHCR image pull fails during `luthien onboard --docker`.

### Acceptance Criteria

- When `docker compose pull` fails, user is prompted to build locally instead
- If accepted, repo is cloned to `~/.luthien/luthien-proxy-src/` and `docker compose build` runs
- If declined or build fails, user is directed to local (non-Docker) mode
- Existing clone is reused/updated on subsequent runs
- Unit tests cover all fallback paths
