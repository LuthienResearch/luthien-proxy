# luthien-cli

CLI tool for managing luthien-proxy gateways. Installed via `pipx install luthien-cli`.

## Versioning

Version is derived from git tags at build time via `hatch-vcs`. No hardcoded version number.

- Tags use the `cli-v*` prefix (e.g. `cli-v0.1.8`)
- Patch bumps are automatic: merging CLI changes to main triggers `auto-tag-cli.yml`
- Minor/major bumps are manual: `git tag cli-v0.2.0 && git push --tags`
- `_version.py` is generated at build time and gitignored — do not create or edit it

## PyPI Publishing

Publishing is automatic via GitHub Actions:
1. `auto-tag-cli.yml` — creates a new `cli-v*` tag when CLI code changes land on main
2. `release-cli.yml` — builds and publishes to PyPI when a `cli-v*` tag is pushed

Uses PyPI trusted publishing (OIDC) — no API tokens stored in GitHub secrets.

## Development

- Build: `cd src/luthien_cli && uv build`
- Test: `uv run pytest tests/luthien_cli/ -v` (from repo root)
- The version shown by `luthien --version` comes from `importlib.metadata.version("luthien-cli")`
