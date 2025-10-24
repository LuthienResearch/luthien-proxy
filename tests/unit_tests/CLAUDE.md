# Unit Testing Guidelines

## Structure

- **Mirror source structure**: `src/luthien_proxy/policies/noop.py` → `tests/unit_tests/policies/test_noop.py`
- **Split when needed**: Complex modules can have multiple test files for special cases or verbose setups
- **Keep tests concise**: Test files should not exceed 3x the length of code being tested (ideally much less)

## Core Philosophy

### Test Outcomes, Not Implementation

- Write tests that validate **what** functions do, not **how** they do it
- Tests should only reference public APIs (signatures, names, docstrings)
- Tests should survive refactors that don't change behavior

### Minimal Mocking

- Only mock when necessary: external dependencies (network, DB) or verifying critical invocations
- Prefer real implementations to reflect actual behavior
- Heavy mocking creates test scenarios that diverge from production

### Test What Matters

- Focus on functional requirements, not incidental details
- Don't assert exact strings unless functionally critical
- Test edge cases and error paths, not just happy paths

## Writing Tests

### Keep Tests Simple and Confident

- **No conditional logic**: Tests should almost never use `if/else` - it's a major code smell suggesting uncertainty
- **Type certainty**: Tests must be completely confident about types
  - Never write `if isinstance(obj, TypeA)` or `obj if obj else default_obj`
  - Use parameterization to explicitly test each type/scenario
- **Iterative logic sparingly**: `for`/`while` loops are occasionally okay (e.g., iterating chunks) but should be rare
- **Linear flow**: Setup → Execute → Assert

### Keep Tests Stateless

- Create test data inline or via fixtures that return fresh instances
- Avoid class-level `setup_method`/`teardown_method` - they create implicit state dependencies
- Prefer inline setup for clarity

### Use Fixtures and Parameterization

- Check `conftest.py` for existing fixtures before creating new ones
- Use `@pytest.mark.parametrize` to test multiple scenarios without duplication
- Keep fixtures focused on one logical resource

## What to Test

- Public API behavior, edge cases, error handling
- Core business logic
- Integration points (using real implementations when possible)

**Don't test:**

- Private methods (test indirectly through public APIs)
- Implementation details, third-party behavior, trivial getters/setters
- Exact log messages or internal strings (unless functionally critical)

## Performance and Timeouts

**Unit tests must be fast.** Slow tests indicate integration-level testing or inefficient test design.

### Speed Requirements

- **Target: < 0.05 seconds per test** - most unit tests should complete in milliseconds
- **Hard limit: 3 seconds** - any test exceeding this is considered failed
- **Strong justification required** for tests > 0.05s - typically indicates:
  - Excessive mocking setup (refactor to fixtures)
  - Async timing tests (consider removing or using integration tests)
  - Real I/O that should be mocked
  - Unnecessary `sleep()` calls

### Timeout Configuration

- **All unit tests run with 3-second timeout by default** via pytest-timeout
- **E2E tests are exempt** from this timeout (use `-m e2e` marker)
- If a test hangs or exceeds timeout:
  1. Identify the blocking operation
  2. Mock it or refactor to be non-blocking
  3. If truly necessary, document why and use `@pytest.mark.timeout(N)` override

### Common Patterns to Avoid

- ❌ `asyncio.sleep()` in unit tests - use mocks with immediate returns
- ❌ Real network calls - mock HTTP clients
- ❌ Real database queries - use in-memory fixtures or mocks
- ❌ Event loop timing dependencies - mock time-based behavior

## Coverage

- **Aim for comprehensive coverage** of all reasonable functionality
- **Meaningful coverage** over line coverage - test behaviors, not just execute code
- **Cover edge cases**: boundary conditions, empty inputs, error paths

### When Code is Hard to Test

- **Flag it**: Call out when implementation makes testing unnecessarily difficult
- **Suggest isomorphic refactors**: Preserve behavior while improving testability
  - Extract dependencies for injection
  - Break large functions into testable units
  - Separate I/O from business logic

Example: Instead of hardcoded dependencies, use optional parameters with defaults - same production behavior, testable with mocks.
