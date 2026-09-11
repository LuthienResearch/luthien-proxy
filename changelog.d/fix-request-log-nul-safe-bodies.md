---
category: Fixes
---

**Request-log NUL sanitization**: Preserve request-log rows by replacing U+0000 body characters with U+FFFD before JSONB insertion.
