---
category: Fixes
---

**Span events no longer spread the whole event payload into OTel attributes** (`observability/emitter.py`)
  - Nested dicts (`payload`, `original_request`, `final_request`) and `None` fields (`user_id`, `session_id`) made the SDK log `Invalid type dict|NoneType for attribute ...` for every such key on every event and drop the key anyway.
  - The span event now carries the payload's scalar fields (and homogeneous scalar lists); the full payload still reaches the stdout, database and publisher sinks.
