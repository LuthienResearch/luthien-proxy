---
category: Features
---

**Unified config system**: All gateway configuration defined in one place (`config_fields.py`) with layered resolution (CLI > env > DB > defaults) and provenance tracking. New `/config` dashboard shows where every value comes from. Admin API endpoints for viewing and editing config at runtime. CLI flags auto-generated for all settings. `.env.example` auto-generated from config spec.
