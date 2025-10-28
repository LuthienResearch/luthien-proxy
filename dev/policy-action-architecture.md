# Policy Action Architecture

**Date:** 2025-10-28
**Status:** Design
**Goal:** Refactor policy execution to use action-based control flow

---

## Overview

Currently, policies directly modify requests/responses and return them. This design proposes policies return **action objects** that describe what should happen next, giving policies more control over the entire request lifecycle.

## Current Flow

```python
# Policy returns modified data directly
async def on_request(request, context):
    # Modify and return
    return modified_request

# Gateway always makes backend call
response = await litellm.acompletion(...)
```

**Limitations:**
- Policy can't decide to skip backend call
- Policy can't send immediate response
- All control logic lives in gateway, not policy

## Proposed Flow

```python
# Policy returns an action
async def on_request(request, context):
    if should_block:
        return SendResponse(blocked_response)
    else:
        return SendRequest(modified_request)

# Gateway executes action
action = await policy.on_request(request, context)
if isinstance(action, SendRequest):
    response = await litellm.acompletion(action.request)
    # Pass result to policy for next action
    next_action = await policy.on_response(response, context)
elif isinstance(action, SendResponse):
    # Send immediately, skip backend
    return action.response
```

## Action Types

### Base Action

```python
@dataclass
class PolicyAction:
    """Base class for all policy actions."""
    pass
```

### Request Actions

```python
@dataclass
class SendRequest(PolicyAction):
    """Make a backend LLM request."""
    request: RequestMessage

@dataclass
class SendResponse(PolicyAction):
    """Send immediate response without calling backend."""
    response: ModelResponse
```

### Response Actions (for streaming)

```python
@dataclass
class SendStreamingResponse(PolicyAction):
    """Continue streaming from backend to client."""
    # Default: passthrough streaming
    pass

@dataclass
class BlockStream(PolicyAction):
    """Stop streaming and send final message."""
    final_response: ModelResponse
```

## Policy Interface

### EventBasedPolicy with Actions

```python
class EventBasedPolicy:
    # Request phase
    async def on_request(
        self,
        request: RequestMessage,
        context: PolicyContext
    ) -> SendRequest | SendResponse:
        """
        Process incoming request.

        Returns:
            SendRequest: Modified request to send to backend
            SendResponse: Immediate response (skip backend)

        Default: return SendRequest(request) - pass through unchanged
        """
        return SendRequest(request)

    # Streaming response phase
    async def on_stream_start(
        self,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> SendStreamingResponse | BlockStream:
        """
        Called when streaming response starts.

        Returns:
            SendStreamingResponse: Continue streaming
            BlockStream: Stop and send final response

        Default: return SendStreamingResponse() - allow streaming
        """
        return SendStreamingResponse()

    # Existing streaming hooks remain unchanged
    async def on_content_delta(self, delta, stream_state, context, streaming_ctx):
        # Still push to outgoing queue directly
        await streaming_ctx.send_text(delta)

    # ... other hooks
```

## Gateway Orchestration

### Request Handler

```python
async def openai_chat_completions(request: Request, ...):
    call_id = str(uuid.uuid4())

    with tracer.start_as_current_span("gateway.chat_completions") as span:
        # Verify auth
        _token = verify_token(request, credentials)

        # Parse request
        data = await request.json()
        original_request = RequestMessage(**data)

        # Execute policy on request
        context = PolicyContext(call_id=call_id, span=span, request=original_request)
        action = await policy.on_request(original_request, context)

        # Execute action
        if isinstance(action, SendRequest):
            # Make backend call
            final_request = action.request

            # Record
            emit_request_event(call_id, original_request, final_request, db_pool)

            if final_request.stream:
                # Streaming path
                llm_stream = await litellm.acompletion(**final_request.model_dump())

                # Check if policy wants to block streaming
                streaming_action = await policy.on_stream_start(context, streaming_ctx)

                if isinstance(streaming_action, BlockStream):
                    # Consume stream but don't send
                    async for _ in llm_stream:
                        pass
                    return JSONResponse(streaming_action.final_response.model_dump())
                else:
                    # Continue with normal streaming
                    return FastAPIStreamingResponse(
                        stream_with_policy_control(...)
                    )
            else:
                # Non-streaming path
                response = await litellm.acompletion(**final_request.model_dump())

                # Policy processes response
                response_action = await policy.on_response(response, context)

                if isinstance(response_action, SendResponse):
                    return JSONResponse(response_action.response.model_dump())

        elif isinstance(action, SendResponse):
            # Skip backend, send immediate response
            emit_request_event(call_id, original_request, None, db_pool)
            emit_response_event(call_id, None, action.response, db_pool)
            return JSONResponse(action.response.model_dump())
```

## Migration Path

### Phase 1: Add Optional Actions (Non-Breaking)

```python
class EventBasedPolicy:
    async def on_request(self, request, context) -> RequestMessage | PolicyAction:
        """Can return either RequestMessage (old) or PolicyAction (new)."""
        return request  # Default: old behavior
```

Gateway checks return type:
```python
result = await policy.on_request(request, context)
if isinstance(result, PolicyAction):
    # New action-based flow
    action = result
else:
    # Old flow: treat as SendRequest
    action = SendRequest(result)
```

### Phase 2: Deprecate Direct Returns

Add warnings when policies return `RequestMessage` directly.

### Phase 3: Remove Old Interface

Require all policies to return `PolicyAction`.

## Sane Defaults

```python
class EventBasedPolicy:
    async def on_request(self, request, context):
        # Default: pass through to backend
        return SendRequest(request)

    async def on_response(self, response, context):
        # Default: pass through to client
        return SendResponse(response)

    async def on_stream_start(self, context, streaming_ctx):
        # Default: allow streaming
        return SendStreamingResponse()
```

Policies only need to override when they want different behavior.

## Example Policies

### 1. Block Requests Policy

```python
class BlockRequestsPolicy(EventBasedPolicy):
    async def on_request(self, request, context):
        if self._should_block(request):
            blocked_response = ModelResponse(
                choices=[{
                    "message": {"role": "assistant", "content": "Request blocked by policy"},
                    "finish_reason": "stop"
                }]
            )
            return SendResponse(blocked_response)
        else:
            return SendRequest(request)
```

### 2. Stop After 3 Tools Policy

```python
class StopAfter3ToolsPolicy(EventBasedPolicy):
    async def on_stream_start(self, context, streaming_ctx):
        # Set up counter in scratchpad
        context.scratchpad["tool_count"] = 0
        return SendStreamingResponse()

    async def on_tool_call_complete(self, stream_state, context, streaming_ctx):
        context.scratchpad["tool_count"] += 1

        if context.scratchpad["tool_count"] >= 3:
            # Stop streaming
            final_response = ModelResponse(
                choices=[{
                    "message": {
                        "role": "assistant",
                        "content": "Stopped after 3 tool calls"
                    },
                    "finish_reason": "policy_stop"
                }]
            )
            return BlockStream(final_response)

        # Continue streaming
        await streaming_ctx.send(stream_state.just_completed)
```

### 3. Cache Response Policy

```python
class CacheResponsePolicy(EventBasedPolicy):
    async def on_request(self, request, context):
        # Check cache
        cached = await self.cache.get(request)
        if cached:
            return SendResponse(cached)
        else:
            return SendRequest(request)

    async def on_response(self, response, context):
        # Store in cache
        await self.cache.set(context.request, response)
        return SendResponse(response)
```

## Questions to Resolve

### 1. Action Return from Streaming Hooks?

**Option A:** Only on_stream_start returns actions
```python
async def on_stream_start(...) -> SendStreamingResponse | BlockStream
async def on_content_delta(...) -> None  # No return, push to queue
```

**Option B:** All hooks can return actions
```python
async def on_content_delta(...) -> ContinueStreaming | BlockStream
async def on_tool_call_complete(...) -> ContinueStreaming | BlockStream
```

**Recommendation:** Option A - cleaner, less complex. Streaming control via `streaming_ctx.mark_output_finished()`.

### 2. Non-Streaming Response Actions?

For non-streaming, does `on_response` need actions?

```python
async def on_response(self, response, context) -> ModelResponse | SendResponse
```

Or just keep returning `ModelResponse` (simpler)?

**Recommendation:** Start with `ModelResponse` return, add actions later if needed.

### 3. How to Handle Format Conversion?

Actions contain internal (OpenAI) format. Gateway still handles conversion at edges.

```
Client (Anthropic)
  → Gateway converts to OpenAI
  → Policy returns SendResponse(openai_format)
  → Gateway converts to Anthropic
  → Client
```

### 4. Error Handling?

What if policy raises exception?

```python
try:
    action = await policy.on_request(request, context)
except Exception as e:
    # Send error response?
    return SendResponse(error_response)
```

Should there be an `ErrorResponse` action type?

## Implementation Steps

1. **Define action types** (`v2/policies/actions.py`)
2. **Update EventBasedPolicy** to return actions (with backwards compatibility)
3. **Update gateway** to handle actions
4. **Write test policies** using new pattern
5. **Update documentation**
6. **Migrate existing policies**

## Success Metrics

- [ ] Policies can skip backend calls
- [ ] Policies can send immediate responses
- [ ] Policies can block streaming mid-stream
- [ ] All existing tests pass
- [ ] New test policies demonstrate capabilities
- [ ] Documentation updated

## Related Documents

- [dev/gateway-end-to-end-flow.md](./gateway-end-to-end-flow.md) - Current flow
- [dev/state-refactoring-plan.md](./state-refactoring-plan.md) - State management
