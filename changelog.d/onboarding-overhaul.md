---
category: Features
---

**Onboarding policy**: New `OnboardingPolicy` appends a welcome message with config links to the first response in a conversation, then becomes inert on subsequent turns.
  - `luthien onboard` now uses the onboarding policy by default (no more policy selection step)
  - `luthien claude` pre-seeds the first message and opens the config page in the browser
