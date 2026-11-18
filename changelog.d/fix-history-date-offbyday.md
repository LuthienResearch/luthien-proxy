---
category: Fixes
pr: 828
---

**History time filter on SQLite**: the session list `from`/`to` bounds are compared as timestamps through `strftime`, not as raw text
  - a row stored with a space separator (sqlite3's datetime adapter) was dropped by a `from` bound earlier on the same date, and a later same-day row passed a `to` bound
  - the filter takes a session's latest event in time order, not by text, when stored formats are mixed
  - milliseconds are kept, so results inside the boundary second match Postgres down to the millisecond
