# Automated maintenance

Scheduled, autonomous maintenance for luthien-proxy. Runs the full check
suite, sweeps for documentation drift, optionally tries to fix what it
finds, and publishes a static HTML dashboard of recent runs.

Designed to be portable: works on macOS (launchd) and Linux (systemd
user timers) with the same scripts. The scheduler is the only OS-specific
piece; everything else is plain bash + python3.

## What it runs

On each scheduled run, the job:

1. Pulls the latest `main` into a dedicated state-dir clone.
2. Runs `scripts/dev_checks.sh` (dependency sync + shellcheck + ruff
   format/lint + pyright + unit tests + radon complexity report).
3. Runs the configured e2e tiers — `MAINT_CHECKS` default is
   `dev_checks,e2e_sqlite,e2e_mock,doc_drift` (no `e2e_real`, since
   that one needs `ANTHROPIC_API_KEY` and costs money on every run).
   Add `e2e_real` to `MAINT_CHECKS` in `automated_maintenance.env` to
   include it.
4. Runs a doc-drift sweep via headless `claude` — finds stale references
   in markdown/config relative to current code.
5. If anything failed and `AUTOFIX_ENABLED=true`, runs one headless
   `claude` fix session **per failing concern** (each failing check is a
   concern), and opens a separate single-concern draft PR for each on its
   own branch `maint-fix/<concern>/<run_id>`. A concern that already has an
   open autofix PR is skipped (no duplicate while a fix is in review); a
   novel concern still gets its own PR.
6. Renders/updates a static dashboard at `$MAINT_PUBLIC_DIR/index.html`.
7. Tears down any docker compose stack the e2e tier brought up.

A run that finds nothing wrong is silent (apart from the dashboard
update). A run with failures leaves logs on disk and, if autofix is
enabled, opens a draft PR per failing concern.

## Layout

```
scripts/automated_maintenance/
├── automated_maintenance.sh                     # main entry point
├── automated_maintenance.env.example            # copy → automated_maintenance.env
├── lib/
│   ├── config.sh                  # env loader, defaults
│   ├── checks.sh                  # dev_checks + e2e tiers
│   ├── doc_drift.sh               # headless claude sweep
│   ├── autofix.sh                 # headless claude fix attempt
│   └── dashboard.py               # static HTML renderer
└── deploy/
    ├── install.sh                 # OS-detecting installer
    ├── launchd/...                # macOS template
    └── systemd/...                # Linux template
```

State (everything written by the job) lives outside the repo at
`$MAINT_STATE_DIR` (default `$HOME/.luthien/automated_maintenance`):

```
$MAINT_STATE_DIR/
├── repo/                          # the clone the job operates on
├── runs/<YYYY-MM-DD-HHMM>/        # one dir per run
│   ├── results.json
│   ├── dev_checks.log
│   ├── e2e_*.log
│   ├── doc_drift.md               # only if drift found
│   └── autofix_*.<concern>.*      # per failing concern, only if autofix ran
│                                  #   (brief, session.log, summary.md, pr_url.txt)
├── public/                        # dashboard, point your web server here
└── logs/                          # scheduler stdout/stderr
```

## Install

### Pre-reqs

Required: `bash`, `git`, `python3`, `uv`.
For e2e_real: `docker` + `ANTHROPIC_API_KEY`.
For autofix: `claude` CLI (logged in), `gh` CLI (authenticated).

### Setup

```bash
cd scripts/automated_maintenance
cp automated_maintenance.env.example automated_maintenance.env
$EDITOR automated_maintenance.env                 # set repo URL, secrets, autofix flag
```

Smoke test once before scheduling:

```bash
./automated_maintenance.sh
```

This will clone the repo to `$MAINT_STATE_DIR/repo`, run all configured
checks, render the dashboard. First run takes a while (clone + e2e).

### Schedule

```bash
HOUR=2 MINUTE=30 deploy/install.sh
```

- **macOS:** writes a LaunchAgent at
  `~/Library/LaunchAgents/com.luthien.automated_maintenance.plist` and loads it.
- **Linux:** writes a user systemd unit + timer at
  `~/.config/systemd/user/luthien-automated-maintenance.{service,timer}` and enables it.
  If you're on a server you'll log out from, run
  `sudo loginctl enable-linger $USER` so the timer survives logout.

Verify:

```bash
# macOS
launchctl list | grep com.luthien.automated_maintenance

# Linux
systemctl --user list-timers luthien-automated-maintenance.timer
```

### Serve the dashboard

The job writes static HTML to `$MAINT_PUBLIC_DIR`. Point any web server
at it. Examples:

**Caddy:**
```caddyfile
maintenance.example.com {
    root * /home/user/.luthien/automated_maintenance/public
    file_server
}
```

**nginx:**
```nginx
server {
    listen 80;
    server_name maintenance.example.com;
    root /home/user/.luthien/automated_maintenance/public;
}
```

**Quick local check:**
```bash
python3 -m http.server -d "$HOME/.luthien/automated_maintenance/public" 8080
```

## Configuration

All values live in `automated_maintenance.env`. See `automated_maintenance.env.example` for the full
list. Highlights:

| Var | Default | Purpose |
|---|---|---|
| `MAINT_REPO_URL` | `https://github.com/LuthienResearch/luthien-proxy.git` | Upstream to clone |
| `MAINT_REPO_BRANCH` | `main` | Branch to track |
| `MAINT_STATE_DIR` | `$HOME/.luthien/automated_maintenance` | Root for state |
| `MAINT_CHECKS` | `dev_checks,e2e_sqlite,e2e_mock,doc_drift` | Comma-separated checks to run |
| `MAINT_RUN_RETENTION` | `30` | How many runs to keep on disk + dashboard |
| `AUTOFIX_ENABLED` | `false` | Opt-in autonomous fix attempts |
| `MAINT_WEBHOOK_URL` | _unset_ | Optional Slack/ntfy/etc. for completion ping |

Available checks: `dev_checks`, `e2e_sqlite`, `e2e_mock`, `e2e_real`, `doc_drift`.

## Operating

### Where the runner lives

`deploy/install.sh` points the scheduler unit at the
`automated_maintenance.sh` path it was invoked from — typically your dev
checkout. The scheduler then runs *that file* every night. The state-dir
clone isolates the **target** of the checks (we clone-and-reset it on
every run), but the **runner** is still your dev checkout.

What this means in practice:

- Edits to `scripts/automated_maintenance/**` in your dev checkout take
  effect on the next scheduled fire. Good for iteration, but be aware.
- `git checkout other-branch` in the dev checkout silently changes what
  the scheduled job runs. If you want stable runner behavior, copy
  `scripts/automated_maintenance/` to `~/.luthien/runner/` and rerun
  `deploy/install.sh` with `MAINT_SH=~/.luthien/runner/automated_maintenance.sh`
  (not currently supported out of the box; manual edit of the rendered
  unit file works).

### Run a single check (debugging)

```bash
./automated_maintenance.sh --once doc_drift
```

### Inspect a run

```bash
ls "$HOME/.luthien/automated_maintenance/runs/"
jq . "$HOME/.luthien/automated_maintenance/runs/<id>/results.json"
```

### Force re-render the dashboard

```bash
python3 lib/dashboard.py \
    --runs-dir "$HOME/.luthien/automated_maintenance/runs" \
    --public-dir "$HOME/.luthien/automated_maintenance/public"
```

### Disable temporarily

```bash
# macOS
launchctl unload ~/Library/LaunchAgents/com.luthien.automated_maintenance.plist

# Linux
systemctl --user disable --now luthien-automated-maintenance.timer
```

## Autofix safety notes

When `AUTOFIX_ENABLED=true`, each failing concern spawns its own headless
`claude` session with **broad permissions**: `--permission-mode bypassPermissions`
and `--allowedTools "Read Edit Write Glob Grep Bash"`. Each session can:

- Edit, create, and delete files anywhere the scheduler user can write
  (in practice: the state-dir clone, plus anything else the user has
  access to on the host).
- Run **arbitrary shell commands** as the scheduler user, with network
  access. The intended use is `git`, `pytest`, `ruff`, etc., but the
  session is not sandboxed beyond the user's filesystem permissions.
- Hit external HTTP services and consume API tokens. `AUTOFIX_MAX_BUDGET_USD`
  and `AUTOFIX_TIMEOUT` cap each **per-concern** session, so worst-case spend
  and wall-clock for a run scale with the number of failing concerns. With the
  default 5 checks failing at `AUTOFIX_MAX_BUDGET_USD=5` / `AUTOFIX_TIMEOUT=1800`,
  a single run can spend up to ~$25 and run ~2.5h serially — keep that in mind
  when tuning those env vars.

It will NOT push directly — the orchestrator pushes each concern's branch
and opens a single-concern **draft** PR for human review.

Risks, in order:

1. **First-order**: the session itself does something unexpected (deletes
   files outside the clone, runs `gh` against your other repos, etc.).
   Schedule autofix only on hosts you'd trust to run unattended `claude`.
2. **Second-order**: bad fixes get pushed as draft PRs. Your CI still
   gates them.
3. **Third-order**: an autofix session that times out leaves a partial
   branch. The next run wipes the state-dir clone and starts fresh, so
   this self-heals.

**On per-concern dedup**: the "skip a concern that already has an open PR"
check queries `gh pr list` and is not atomic. Two maintenance runs firing
close together (a manual rerun during the cron fire, or two hosts pointed at
the same repo) could both query before either opens its PR, and both open a
duplicate for the same concern. The single-host nightly-cron deployment this
is built for doesn't hit that (the run lock in `automated_maintenance.sh`
serializes runs on one host); it's a deliberate trade-off, not a guarantee.
The dedup is also age-blind: an open autofix PR for a concern suppresses new
autofixes for that concern *indefinitely*, so a stale one languishing in
review will keep blocking fresh fixes — close stale autofix PRs to unblock.

**On account scope**: `gh pr create` runs as whatever account `gh` is
logged in as. If you enable autofix on a workstation, autofix PRs will
be authored by your personal account, and the session can `gh` against
any repo you can write to (not just luthien-proxy). For shared/CI
deployments, set this up under a dedicated machine user with a scoped
GitHub token (`repo` for the luthien-proxy org only), not your personal
account.

If you don't want any of this, leave `AUTOFIX_ENABLED=false`. The dashboard
will still show what failed; you fix it manually.

## Troubleshooting

**Job didn't run at the scheduled time (macOS):** the Mac was asleep. Plain
`StartCalendarInterval` doesn't wake the machine. Either keep the machine
awake (caffeinate, energy settings) or use `pmset` to schedule a wake.

**Job didn't run at the scheduled time (Linux user systemd):** you logged
out and lingering isn't enabled. Run `sudo loginctl enable-linger $USER`.

**`uv: command not found` inside the launchd job:** PATH wasn't propagated.
Edit the rendered plist and add the directory containing `uv` to the
`EnvironmentVariables.PATH` value, then `launchctl unload && load`.

**`docker: command not found`:** Docker Desktop on macOS doesn't always
add itself to non-interactive shells. Add `/Applications/Docker.app/Contents/Resources/bin`
to PATH in the unit file.

**E2e fails because of a port conflict:** another stack is up. The job
calls `docker compose down -v` at teardown but only on its own clone —
your dev clone's containers aren't touched. If you also have a stack up
in your dev clone, stop it before scheduled run time, or assign different
ports via `quick_start.sh`'s auto-port logic.
