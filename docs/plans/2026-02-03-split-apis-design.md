# Split APIs Design: Independent Anthropic & OpenAI Paths

**Date:** 2026-02-03
**Branch:** `split-apis`
**Status:** Approved

## Motivation

1. **Fidelity concerns** — Converting between Anthropic and OpenAI formats loses information and causes subtle bugs (thinking blocks, tool calls, edge cases)
2. **Complexity concerns** — Conversion code is hard to maintain and test

## Architecture

### Current (being replaced)
```
Client (any format) → Convert to OpenAI → Policies (OpenAI) → LiteLLM → Convert back → Client
```

### New
```
Anthropic Client → Anthropic Policies → Anthropic SDK → Anthropic Client
OpenAI Client   → OpenAI Policies   → LiteLLM      → OpenAI Client
```

Each API gets its own end-to-end path with **no format conversion**. Paths share infrastructure (routing, auth, observability) but have separate:
- Request/response types (native Pydantic models)
- Policy protocols (hooks receive native types)
- Backend clients (Anthropic SDK vs LiteLLM)
- Streaming formatters

## Scope

**Phase 1 (this branch):** Anthropic end-to-end path only
**Phase 2 (later):** OpenAI path
**Phase 3 (later):** Tooling to wrap policies between formats

## Components to Build

### 1. Anthropic Types
`src/luthien_proxy/llm/types/anthropic.py` — expand with:
- `AnthropicRequest` — messages, model, max_tokens, system, tools, etc.
- `AnthropicMessage` — role, content (list of content blocks)
- `AnthropicContentBlock` — text, tool_use, tool_result, thinking
- `AnthropicResponse` — non-streaming response
- `AnthropicStreamEvent` — message_start, content_block_delta, etc.

### 2. Anthropic Backend Client
`src/luthien_proxy/llm/anthropic_client.py` — wraps Anthropic SDK:
- `complete(AnthropicRequest) → AnthropicResponse`
- `stream(AnthropicRequest) → AsyncIterator[AnthropicStreamEvent]`

### 3. Anthropic Policy Protocol
`src/luthien_proxy/policy_core/anthropic_protocol.py`:
- `on_request(AnthropicRequest) → AnthropicRequest`
- `on_response(AnthropicResponse) → AnthropicResponse`
- Streaming hooks for content blocks, deltas, etc.

### 4. Anthropic Stream Executor
`src/luthien_proxy/streaming/anthropic_executor.py`:
- Receives native Anthropic stream events from SDK
- Calls policy hooks
- Outputs events for client formatter

### 5. Anthropic Policies
`src/luthien_proxy/policies/anthropic/`:
- `noop.py` — pass-through (validates infrastructure)
- `allcaps.py` — transforms text (validates modification)

### 6. Gateway Integration
- Anthropic route (`/v1/messages`) uses new path
- OpenAI route returns 501 Not Implemented (temporary)

## Cleanup (delete as code becomes unused)

- `anthropic_to_openai_request()` in `llm_format_utils.py`
- `openai_to_anthropic_response()` in `llm_format_utils.py`
- `AnthropicSSEAssembler`
- Old conversion logic in `processor.py`
- Old policies (after Anthropic versions exist)

## Keep for Future OpenAI Path

- `llm/types/openai.py`
- `llm/litellm_client.py`
- `streaming/client_formatter/openai.py`

## Implementation Order

1. Add Anthropic types + tests
2. Add Anthropic SDK client + tests
3. Add Anthropic policy protocol + tests
4. Add Anthropic NoOp policy + tests
5. Add Anthropic stream executor + tests
6. Wire gateway → new path (e2e working)
7. Add AllCaps policy + tests
8. Migrate remaining policies
9. Clean up unused code

## Development Process

- **TDD:** Write tests before implementation for each step
- **Progress tracking:** Update `dev/NOTES.md` with status, problems, decisions
- **Subagents:** Dispatch parallel agents where components are independent
- **Commits:** Frequent, after each working step

## Conversation Events

Storage should be generic enough to handle native formats from either API without forcing a common schema. May need refactoring as part of this work.
