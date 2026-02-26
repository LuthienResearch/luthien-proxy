# Objective

Create a comprehensive regression test suite for the proxy's "Invisible Unless Needed" requirement (Uber Requirement 1). This covers 15+ historical bugs where the proxy broke requests that work fine without it.

## Acceptance Criteria

- [ ] `tests/unit_tests/test_requirement1_regression.py` exists with tests for all 8 bug categories
- [ ] All tests pass (`uv run pytest tests/unit_tests/test_requirement1_regression.py -v`)
- [ ] Tests are meaningful (not placeholders) - they exercise real pipeline code
- [ ] Each test has a docstring linking to the original issue
- [ ] Draft PR created
