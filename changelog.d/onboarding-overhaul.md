---
category: Features
---

**Onboarding policy**: New `OnboardingPolicy` appends a welcome message with config links to the first response in a conversation, then becomes inert on subsequent turns.
  - `luthien onboard` now uses the onboarding policy by default (no more policy selection step)
  - `luthien onboard` pre-seeds the onboarding prompt and opens the config page after gateway setup
