# User Story 1: Solo Developer - Intelligent Safety with Context Awareness

## Persona

**Alex** - A senior developer who uses Claude Code daily for complex, multi-step tasks.

## Context

Alex works on a large codebase and often has long conversations with Claude spanning many requests. A single tool call might be dangerous in isolation but perfectly safe given the conversation history (e.g., "delete that file" after Claude just created a temp file). Alex also uses Claude's thinking mode for complex reasoning and wants that preserved.

## Story

> As Alex, I want the proxy to understand the full context of my conversation—not just individual requests—so that safety decisions are intelligent and don't block legitimate multi-step workflows.

## Scenario

1. Alex starts a session and asks Claude to refactor a module
2. Claude creates several temporary files during the refactoring process
3. Alex asks Claude: "Now clean up those temp files you created"
4. Claude generates `rm ./tmp_refactor_*.py`
5. The proxy's policy evaluates this **with conversation context**:
   - Sees Claude created these exact files 3 messages ago
   - Determines this is cleanup of agent-created artifacts, not user data
   - Allows the operation (would have blocked without context)
6. Later, Alex asks Claude to "delete the old config files"
7. The proxy evaluates **with context**:
   - No prior creation of config files in this conversation
   - This affects user data, not agent artifacts
   - Blocks and **injects a message into the response**: "I've paused this operation. Deleting config files could affect your project. Please confirm by listing the specific files you want removed."
8. Alex sees the injected message in Claude Code's output naturally
9. Alex can view the full conversation history in the **Conversation Viewer UI** at `localhost:8000/conversations/{session_id}`
10. The UI shows the message flow, highlights interventions, and lets Alex drill into policy decisions

## Acceptance Criteria

- [ ] Proxy tracks conversation context across multiple requests (session/thread ID)
- [ ] Policies receive full conversation history, not just current request
- [ ] Policy decisions can reference prior messages ("this file was created in message 3")
- [ ] Interventions inject explanatory messages into the response stream
- [ ] Injected messages appear naturally in Claude Code output (not as errors)
- [ ] Conversation Viewer UI shows full message history with intervention highlights
- [ ] Model parameters (thinking, verbosity, temperature) are passed through to backend
- [ ] Thinking blocks are preserved and visible when enabled

## Required Features

### Core Infrastructure

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-5sr` | Conversation context tracking across requests | open | P1 |
| `luthien-proxy-fsb` | Message injection into response stream | open | P1 |
| `luthien-proxy-mfs` | thinking and verbosity model flags not respected | open | P2 |

### UI Components

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-edl` | Conversation Viewer UI | open | P1 |

### Policy Framework

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-3yp` | Context-aware policy base class | open | P2 |

## Technical Touchpoints

- `storage/conversation_events`: Session-based conversation tracking
- `policy_core/policy_context.py`: Conversation history access
- `streaming/policy_executor`: Message injection into SSE stream
- `ui/conversation_viewer`: New UI for conversation inspection
- `llm/client.py`: Pass-through of all model parameters (thinking, verbosity)
- `gateway_routes.py`: Session ID extraction and threading

## Implementation Status

**Overall Progress**: Not Started

### Phase 1: Foundation (Conversation Tracking)
- [ ] Implement session ID extraction from requests
- [ ] Store conversation events with session linkage
- [ ] Expose conversation history to policy context

### Phase 2: Message Injection
- [ ] Design injection protocol for SSE streams
- [ ] Implement injection in PolicyExecutor
- [ ] Test with Claude Code client

### Phase 3: Model Parameter Pass-through
- [ ] Fix thinking/verbosity flag handling
- [ ] Add integration tests for parameter preservation

### Phase 4: Conversation Viewer UI
- [ ] Design conversation viewer interface
- [ ] Implement conversation list and detail views
- [ ] Add intervention highlighting

## Dependencies

```
luthien-proxy-5sr (Conversation context tracking)
    └── luthien-proxy-edl (Conversation Viewer UI)
    └── luthien-proxy-3yp (Context-aware policy base class)
```

## Notes

- Session ID should be extracted from Claude Code's conversation threading (if available) or generated per-connection
- Message injection must not break SSE protocol compliance
- Consider caching conversation history in Redis for performance
