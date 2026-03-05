# SimpleLLMPolicy Spec

## Overview

A new policy that applies arbitrary plain-English instructions to LLM response content using a configurable judge LLM. Each content block (text or tool_use) in the response is buffered, sent to the judge LLM with the instructions and accumulated context from prior blocks, and either passed through unchanged or replaced with new content. The judge can change block types (e.g. replace a tool call with conversational text) and can output multiple replacement blocks. A structured JSON protocol with a fast `{"action": "pass"}` path avoids regenerating content when no modification is needed.

## Goals

- Plain-English instruction-driven response modification for all content types (text, tool_use)
- Fast no-op path: judge returns `{"action": "pass"}` without regenerating original content
- Cross-type replacement: tool_use blocks can become text blocks and vice versa
- Block-by-block streaming: each completed block is judged and emitted before the next
- Accumulated context: each block evaluation includes prior (possibly replaced) blocks
- Works for both OpenAI and Anthropic API formats
- Configurable judge LLM via LiteLLM (model, api_base, api_key)
- Configurable error behavior (fail-open or fail-secure)
- Automatic stop_reason/finish_reason correction when block types change

## Non-Goals

- Request modification (only response blocks are evaluated)
- Multiple sequential instruction sets within one policy instance (use MultiSerialPolicy to chain)
- Streaming within a block (blocks are fully buffered before judge evaluation)
- Thinking/extended thinking block modification (pass through unchanged)

## Requirements

### Configuration (YAML)

```yaml
policy:
  class: "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy"
  config:
    config:
      model: "claude-haiku-4-5"           # Judge LLM model
      api_base: null                       # Optional API base URL
      api_key: null                        # Optional API key (falls back to env)
      instructions: "Remove sycophantic language. Never start with 'Great question'."
      temperature: 0.0
      max_tokens: 4096                     # Max tokens for judge response
      on_error: "pass"                     # "pass" (fail-open) or "block" (fail-secure)
```

### Judge LLM Protocol

The judge receives a system prompt containing the user's instructions and a structured prompt describing the current block and accumulated context. It responds with JSON.

**Judge response schema:**

```json
// No-op (fast path):
{"action": "pass"}

// Replace with one or more blocks:
{"action": "replace", "blocks": [
  {"type": "text", "text": "Replacement content"},
  {"type": "tool_use", "name": "tool_name", "input": {"key": "value"}}
]}
```

- `pass`: emit the original block unchanged
- `replace`: emit the replacement blocks instead of the original
- Use JSON mode (`response_format: {"type": "json_object"}`) when calling the judge via LiteLLM

### Judge Prompt Structure

The judge system prompt includes:
1. The user-configured `instructions`
2. Role description: "You are evaluating a response block from an AI assistant"
3. Output format instructions (the JSON schema above)

The judge user message includes:
1. Previously emitted blocks (showing replacements, not originals) as context
2. The current block to evaluate (type, content/tool details)
3. Instruction to respond with JSON

### Block Processing

1. Buffer incoming streaming content per-block (text deltas, tool_use input JSON deltas)
2. When a block completes, construct the judge prompt with accumulated context + current block
3. Call the judge LLM
4. Parse the JSON response:
   - `pass` -> emit original block events
   - `replace` -> emit replacement block events, update accumulated context with replacement
5. Move to next block

### Error Handling

When the judge LLM call fails (timeout, malformed JSON, API error):
- `on_error: "pass"` (default): emit the original block unchanged, log warning
- `on_error: "block"`: suppress the block entirely, log error

### stop_reason / finish_reason Correction

After all blocks are processed:
- If all `tool_use` blocks were replaced with non-tool blocks, change `stop_reason` from `"tool_use"` to `"end_turn"` (Anthropic) or `finish_reason` from `"tool_calls"` to `"stop"` (OpenAI)
- If text blocks were replaced with tool_use blocks, adjust correspondingly

### API Format Support

**Anthropic (via AnthropicExecutionInterface):**
- `run_anthropic`: stream from backend, buffer blocks via stream events, judge on content_block_stop, emit transformed events
- Non-streaming: iterate content blocks, judge each, return modified response

**OpenAI (via OpenAIPolicyInterface + PolicyProtocol streaming hooks):**
- `on_chunk_received`: suppress (don't auto-forward)
- `on_content_complete` / `on_tool_call_complete`: judge the block, emit result
- `on_stream_complete`: emit corrected finish_reason
- `on_openai_response`: non-streaming path, judge each block in response

## Technical Approach

### Class Structure

```python
class SimpleLLMPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicExecutionInterface):
    """Policy that applies plain-English instructions to response blocks via a judge LLM."""
```

Follows the same pattern as `ToolCallJudgePolicy`:
- Pydantic config model (`SimpleLLMConfig`)
- Request-scoped state via `PolicyContext.get_request_state()`
- LiteLLM for judge calls (reuse `tool_call_judge_utils` patterns or factor out shared LLM-calling code)
- Observability events via `PolicyContext.record_event()`

### Request-Scoped State

```python
@dataclass
class _SimpleLLMState:
    # Accumulated context: list of blocks that have been emitted (after judge processing)
    emitted_blocks: list[dict] = field(default_factory=list)
    # Track whether any tool_use blocks were replaced (for stop_reason correction)
    tool_blocks_replaced: bool = False
    original_had_tool_use: bool = False
```

### Judge Call

Use LiteLLM `acompletion()` with:
- `response_format={"type": "json_object"}` for JSON mode
- Configurable model, api_base, api_key, temperature, max_tokens
- Parse response, validate against expected schema
- On parse failure: treat as error (apply on_error policy)

### File Organization

- `src/luthien_proxy/policies/simple_llm_policy.py` - main policy
- `tests/unit_tests/policies/test_simple_llm_policy.py` - unit tests

## Open Questions

1. Should there be a token/cost budget limit per request to prevent runaway judge calls on responses with many blocks?
2. Should the judge prompt include the original user request/system prompt for richer context? (Currently scoped to response-only per interview, but could be a future config flag.)
3. For very large text blocks, should there be truncation before sending to the judge?

## Acceptance Criteria

- [ ] `SimpleLLMPolicy` class implements `BasePolicy + OpenAIPolicyInterface + AnthropicExecutionInterface`
- [ ] Pydantic config model with all specified fields, loadable from YAML
- [ ] Judge LLM called via LiteLLM with JSON mode
- [ ] `{"action": "pass"}` returns original block unchanged without regeneration
- [ ] `{"action": "replace", "blocks": [...]}` replaces block with specified content
- [ ] Cross-type replacement works (tool_use -> text, text -> tool_use)
- [ ] Accumulated context from prior (replaced) blocks sent to judge for each evaluation
- [ ] Streaming works for both OpenAI and Anthropic formats with per-block emission
- [ ] Non-streaming works for both formats
- [ ] stop_reason/finish_reason auto-corrected when block types change
- [ ] Configurable on_error behavior (pass/block) with default "pass"
- [ ] Observability events emitted for judge calls (started, complete, failed, replaced, passed)
- [ ] Unit tests covering: pass-through, text replacement, tool->text replacement, text->tool replacement, multi-block replacement, error handling (both modes), stop_reason correction
- [ ] `freeze_configured_state()` validates immutability
- [ ] dev_checks.sh passes
