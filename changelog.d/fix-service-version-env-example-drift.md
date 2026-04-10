---
category: Fixes
pr: 527
---

**Restore `.env.example` and add regression guard**: The file was accidentally wiped to zero lines in PR #519 (commit `1b28e4d5`), leaving main's CI dev_checks job red because the clean-tree gate caught the generator producing 150 lines of output on every run. Regenerated the committed copy from `scripts/generate_env_example.py` and added a unit test (`test_env_example_matches_generator`) that asserts the committed file matches the generator's output. This test runs in the fast unit tier and also guards against the original "hardcoded SERVICE_VERSION=2.0.0" class of bug — any config field whose committed default drifts from `config_fields.py` will now be caught before CI.
