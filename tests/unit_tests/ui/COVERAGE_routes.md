# Coverage Documentation: v2/ui/routes.py

**Module:** `src/luthien_proxy/v2/ui/routes.py`
**Coverage:** 74%

## Coverage Gaps (26%)

- **Lines 34-41:** `trace_viewer()` endpoint - FastAPI route returning Jinja2 templates
- **Line 58:** Error handling in `activity_monitor()` endpoint
- **Line 67:** SSE streaming in `activity_monitor()` endpoint

## Why Limited Unit Testing?

UI routes have limited unit test coverage by design.

### Rationale

- UI routes return Jinja2 templates and require FastAPI app context
- Testing template rendering properly requires:
  1. Mocked FastAPI Request/Response objects (brittle, diverges from reality)
  2. Integration tests with actual HTTP client (better approach)
- SSE streaming endpoints are best tested end-to-end with real HTTP connections

### Integration Test Coverage

Integration tests cover:
- Template rendering with correct context
- Activity monitor SSE streaming
- Error responses
- Full request/response cycle

## Potential Refactoring Opportunities

If these components were extracted as pure functions, they could be unit tested:

1. **Template context building** - Extract logic that prepares data for templates
2. **SSE event formatting** - Extract formatting from streaming logic

Example refactor:

```python
# Pure function - easily testable
def build_trace_viewer_context(trace_id: str) -> dict[str, Any]:
    """Build context dict for trace viewer template."""
    return {
        "trace_id": trace_id,
        "endpoint": "/v2/trace",
        # ... other context
    }

# Route becomes simpler
@router.get("/trace/{trace_id}")
async def trace_viewer(request: Request, trace_id: str):
    context = build_trace_viewer_context(trace_id)
    return templates.TemplateResponse("trace_viewer.html", {**context, "request": request})
```

These would be isomorphic refactors preserving current behavior.

## Adding Unit Tests

If helpers are extracted, create tests in this file:

- `tests/unit_tests/v2/ui/test_routes_helpers.py`

Follow guidelines in [tests/unit_tests/CLAUDE.md](../../CLAUDE.md)
