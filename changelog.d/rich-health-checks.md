---
category: Features
---

**Rich health checks**: `/health` endpoint now reports DB and Redis health with latency measurements. Overall status reflects infrastructure state: `healthy`, `degraded` (redis down), or `unhealthy` (db down).
