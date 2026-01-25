# Notes: CSV Backfill

## Claude Code Session Storage

Sessions are stored as JSONL files in `~/.claude/projects/<project-path>/`:
- `-Users-scottwofford-build-luthien-proxy/` - main work sessions
- `-Users-scottwofford-build/` - general ~/build sessions
- `-private-tmp/` and pytest temp dirs - test artifacts (skip these)

## JSONL Structure

Each line is a JSON object with:
- `type`: "user" or "assistant" or "file-history-snapshot"
- `timestamp`: ISO format "2026-01-25T18:50:09.347Z"
- `message.content`: string (user) or array of content blocks (assistant)
- `sessionId`: UUID identifying the session

## CSV Template Format

```
Start_session description: <topic>,,,,
End_session_description:,,,,
,,,,
logged_by_luthien,created_at,prompt_or_response,comments,content
N,2025-12-16 14:24:45+00,PROMPT,<optional comment>,<message>
N,2025-12-16 14:24:51+00,RESPONSE,,<message>
```

## Approach

1. Count sessions per project directory
2. Identify which sessions are real work vs test artifacts
3. Export real sessions (luthien-proxy + ~/build) to CSV
4. Skip sessions that already have CSV exports
5. Run import script

## Completed Work (2026-01-25)

### Export Script Created
`scripts/export_claude_sessions.py` - exports Claude Code JSONL sessions to CSV format
- Filters out test sessions (short prompts, "Say X", warmup, etc.)
- Supports `--verbose`, `--include-tests`, `--skip-existing` flags
- Exports from luthien-proxy (58 sessions) and ~/build (8 sessions)

### Results
- **37 CSVs created** (31 from luthien-proxy, 6 from ~/build)
- **28 sessions skipped** (test sessions or no content)
- **2014 events imported** to database
- **50 sessions visible** in /history API

### Commands Used
```bash
# Export luthien-proxy sessions
uv run python scripts/export_claude_sessions.py --verbose

# Export ~/build sessions
uv run python scripts/export_claude_sessions.py --sessions-dir ~/.claude/projects/-Users-scottwofford-build --verbose

# Import to database
DATABASE_URL="postgresql://luthien:luthien_dev_password@localhost:5432/luthien_control" uv run python scripts/import_session_csvs.py /Users/scottwofford/build/luthien-private-session-logs/
```

---

*Previous objective notes moved to [PR #134](https://github.com/LuthienResearch/luthien-proxy/pull/134) description (2026-01-24)*
