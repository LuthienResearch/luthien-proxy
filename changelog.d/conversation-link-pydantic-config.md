---
category: Refactors
---

**ConversationLinkPolicy uses Pydantic config**: Constructor now accepts `ConversationLinkPolicyConfig` (matching the rest of the codebase) instead of a scalar `base_url` kwarg, making the admin UI render it via the Pydantic form path.
  - Removed the legacy JS config-form renderer (`renderLegacyConfigFormInner` / `bindLegacyConfigInputs`) from `policy_config.js` — `ConversationLinkPolicy` was its last consumer.
