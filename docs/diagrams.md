# Luthien Dataflow Diagrams

This document provides visual representations of how data flows through Luthien's proxy and control plane architecture.

## Non-Streaming Request Flow

Shows the complete journey of a standard (non-streaming) LLM request through Luthien:

```mermaid
graph TB
    Start([LLM Request]) --> Callback[LiteLLM Callback<br/>unified_callback.py]
    Callback -->|HTTP POST| ControlPlane[Control Plane<br/>hooks_routes.py::hook_generic]

    ControlPlane --> PrepPayload[1. Prepare Payload<br/>Extract call_id, trace_id]
    PrepPayload --> LogOriginal[Log Original to<br/>DEBUG_LOG_QUEUE]
    LogOriginal --> DebugLogs1[(debug_logs table)]

    PrepPayload --> InvokePolicy[2. Invoke Policy<br/>policy.hook_name]
    InvokePolicy --> PolicyTransform{Policy<br/>transforms?}
    PolicyTransform -->|Yes| TransformedResult[Transformed Result]
    PolicyTransform -->|No| OriginalResult[Original Payload]

    TransformedResult --> PublishResult[3. log_and_publish_hook_result]
    OriginalResult --> PublishResult

    PublishResult --> ThreeWay{Three Destinations}
    ThreeWay --> DebugQueue[DEBUG_LOG_QUEUE]
    ThreeWay --> EventQueue[CONVERSATION_EVENT_QUEUE<br/>record_conversation_events]
    ThreeWay --> PubQueue[CONVERSATION_EVENT_QUEUE<br/>publish_conversation_event]

    DebugQueue --> DebugLogs2[(debug_logs table)]
    EventQueue --> ConvEvents[(conversation_events<br/>table)]
    PubQueue --> Redis[(Redis Pub/Sub<br/>luthien:conversation:call_id)]

    PublishResult --> Return[4. Return Result<br/>to Callback]
    Return --> End([Response to Client])

    style Start fill:#e1f5ff
    style End fill:#e1f5ff
    style PublishResult fill:#fff4e1
    style ThreeWay fill:#ffe1e1
    style DebugLogs1 fill:#e8f5e9
    style DebugLogs2 fill:#e8f5e9
    style ConvEvents fill:#e8f5e9
    style Redis fill:#e8f5e9
```

### Key Components

- **Blue nodes**: Request entry and exit points
- **Yellow nodes**: Key processing functions (`log_and_publish_hook_result`)
- **Red diamonds**: Decision points and multi-destination routing
- **Green cylinders**: Persistent storage (database tables, Redis)

### Files Referenced

- `config/unified_callback.py` - LiteLLM callback that POSTs to control plane
- `src/luthien_proxy/control_plane/hooks_routes.py` - Main hook handler (`hook_generic`)
- `src/luthien_proxy/control_plane/hook_result_handler.py` - Result processing helper

---

## Streaming Request Flow

Shows how streaming LLM requests flow through Luthien, processing chunks in real-time:

```mermaid
graph TB
    Start([LLM Streaming Request]) --> Callback[LiteLLM Callback<br/>unified_callback.py]
    Callback -->|WebSocket| Orchestrator[Stream Orchestrator<br/>stream_orchestrator.py]

    Orchestrator -->|WebSocket| ControlPlane[Control Plane<br/>streaming_routes.py::policy_stream_endpoint]

    ControlPlane --> InitPublisher[Initialize<br/>_StreamEventPublisher]
    InitPublisher --> ChunkLoop{For Each Chunk}

    ChunkLoop --> ReceiveChunk[1. Receive CHUNK<br/>from WebSocket]
    ReceiveChunk --> RecordOriginal[2. publisher.record_original<br/>Log to debug_logs]
    RecordOriginal --> DebugLogs1[(debug_logs table)]

    RecordOriginal --> YieldPolicy[3. Yield to<br/>policy.generate_response_stream]
    YieldPolicy --> PolicyProcess{Policy processes<br/>and yields 0..N chunks}

    PolicyProcess --> RecordResult[4. publisher.record_result<br/>Log chunk]
    RecordResult --> DebugLogs2[(debug_logs table)]

    RecordResult --> SendBack[5. Send CHUNK<br/>back via WebSocket]
    SendBack --> Orchestrator
    Orchestrator --> ChunkLoop

    ChunkLoop -->|Stream ends| Finish[publisher.finish<br/>Build summary event]
    Finish --> DebugLogs3[(debug_logs table)]
    Finish --> FinalPublish[Publish to Redis]
    FinalPublish --> FinalRedis[(Redis Pub/Sub<br/>luthien:conversation:call_id)]
    FinalPublish --> SendEnd[Send END to client]
    SendEnd --> End([Response to Client])

    style Start fill:#e1f5ff
    style End fill:#e1f5ff
    style InitPublisher fill:#fff4e1
    style RecordResult fill:#fff4e1
    style Finish fill:#fff4e1
    style FinalPublish fill:#fff4e1
    style DebugLogs1 fill:#e8f5e9
    style DebugLogs2 fill:#e8f5e9
    style DebugLogs3 fill:#e8f5e9
    style FinalRedis fill:#e8f5e9
```

### Key Differences from Non-Streaming

- **Bidirectional WebSocket**: Proxy and control plane communicate in real-time
- **Per-chunk logging only**: Each chunk is logged to `debug_logs` but NOT written to `conversation_events` (performance optimization)
- **Summary at end**: Only when the stream completes does `publisher.finish()` publish a summary event to Redis
- **Loop structure**: Process continues until stream ends
- **Publisher object**: `_StreamEventPublisher` maintains state across chunks

**Why different?** Streaming avoids write amplification - a 1000-chunk response would create 1000 database rows. Instead, we log each chunk for debugging but only publish one summary event at the end.

### Files Referenced

- `src/luthien_proxy/proxy/stream_orchestrator.py` - Manages bidirectional WebSocket communication
- `src/luthien_proxy/control_plane/streaming_routes.py` - Streaming endpoint (`policy_stream_endpoint`)
- `src/luthien_proxy/control_plane/streaming_routes.py` - `_StreamEventPublisher` class

---

## Hook Timeline (Sequence Diagram)

Shows the temporal sequence of hooks coordinating between LiteLLM, control plane, and backend:

```mermaid
sequenceDiagram
    participant C as Client
    participant L as LiteLLM
    participant CB as Callback
    participant CP as Control Plane
    participant P as Policy
    participant B as Backend

    Note over C,B: Non-Streaming Request
    C->>L: POST /v1/chat/completions
    L->>CB: async_pre_call_hook()
    CB->>CP: POST /api/hooks/async_pre_call_hook
    CP->>P: policy.async_pre_call_hook()
    P-->>CP: None (log only)
    CP-->>CB: None
    L->>B: Forward request (unchanged)
    B-->>L: Response
    L->>CB: async_post_call_success_hook()
    CB->>CP: POST /api/hooks/async_post_call_success_hook
    CP->>P: policy.async_post_call_success_hook()
    P-->>CP: transformed_response
    CP-->>CB: transformed_response
    CB-->>L: Apply transformation
    L-->>C: Modified response

    Note over C,B: Streaming Request
    C->>L: POST /v1/chat/completions (stream=true)
    L->>B: Stream request
    B->>L: chunk1
    L->>CB: async_post_call_streaming_iterator_hook()
    CB->>CP: WebSocket START
    CB->>CP: WebSocket CHUNK
    CP->>P: policy.generate_response_stream(chunk1)
    P-->>CP: transformed_chunk1
    CP->>CB: WebSocket CHUNK
    CB->>C: transformed_chunk1
    B->>L: chunk2
    CB->>CP: WebSocket CHUNK
    CP->>P: yield chunk2
    P-->>CP: transformed_chunk2
    CP->>CB: WebSocket CHUNK
    CB->>C: transformed_chunk2
    B->>L: END
    CB->>CP: WebSocket END
    CP->>P: stream complete
    CP->>CB: WebSocket END
    CB->>C: END
```

**Key insight**: This timeline view shows call ordering that flowchart diagrams don't capture - particularly useful for understanding pre-call vs post-call hook timing.

---

## Result Handling Pattern

Non-streaming and streaming use different strategies optimized for their use cases:

```mermaid
graph LR
    subgraph "Non-Streaming (per request)"
        NS_Result[Hook Result] --> NS_Helper[log_and_publish_hook_result<br/>ONE function call]
        NS_Helper --> NS_Debug[(debug_logs)]
        NS_Helper --> NS_Conv[(conversation_events)]
        NS_Helper --> NS_Redis[(Redis Pub/Sub)]
    end

    subgraph "Streaming (per chunk)"
        S_Chunk[Each Chunk] --> S_Publisher[_StreamEventPublisher.record_result<br/>Logs only]
        S_Publisher --> S_Debug[(debug_logs)]
    end

    subgraph "Streaming (at end)"
        S_End[Stream Complete] --> S_Finish[_StreamEventPublisher.finish<br/>Summary only]
        S_Finish --> S_Redis[(Redis Pub/Sub)]
    end

    style NS_Helper fill:#fff4e1
    style S_Publisher fill:#fff4e1
    style S_Finish fill:#fff4e1
    style NS_Debug fill:#e8f5e9
    style NS_Conv fill:#e8f5e9
    style NS_Redis fill:#e8f5e9
    style S_Debug fill:#e8f5e9
    style S_Redis fill:#e8f5e9
```

### Result Handling Comparison

**Non-Streaming**: Three destinations per request
1. **`debug_logs` table** - Raw logging for debugging and auditing
2. **`conversation_events` table** - Structured events for analysis and reconstruction
3. **Redis Pub/Sub** - Real-time streaming to UI and monitoring tools

**Streaming**: Two-phase approach (performance optimization)
- **Per-chunk**: Only `debug_logs` (avoids write amplification)
- **At stream end**: Only Redis pub/sub with summary

### Architecture Comparison

| Aspect | Non-Streaming | Streaming |
|--------|---------------|-----------|
| **Processing unit** | Complete request | Individual chunk + summary |
| **Handler** | `log_and_publish_hook_result()` function | `_StreamEventPublisher.record_result()` (per chunk)<br/>`_StreamEventPublisher.finish()` (at end) |
| **When called** | Once per request | Per chunk + once at end |
| **debug_logs writes** | 1 per request | N+1 per request (N chunks + 1 summary) |
| **conversation_events writes** | 1 per request | 0 (never) |
| **Redis pub/sub** | 1 per request | 1 at stream end only |

**Key insight**: Streaming sacrifices structured event storage (conversation_events) to avoid database write amplification, while still providing debug logs for troubleshooting.

---

## Task Queue Flow

Shows how background tasks are processed:

```mermaid
graph LR
    Submit[Queue.submit] --> CreateTask[asyncio.create_task]
    CreateTask --> Background[Runs in background<br/>Best-effort]
    Background --> Success{Success?}
    Success -->|Yes| Complete[Task completes]
    Success -->|No| Silent[Fails silently<br/>Logged only]

    style Submit fill:#fff4e1
    style Background fill:#ffe1e1
    style Silent fill:#ffcccc
```

**Important**: Both `DEBUG_LOG_QUEUE` and `CONVERSATION_EVENT_QUEUE` are **best-effort**. Failures in logging/publishing do not block the main request flow.

---

## Provider Normalization (Streaming)

Shows how different LLM provider formats are normalized:

```mermaid
graph LR
    OpenAI[OpenAI SSE Stream] --> Adapter1{Format?}
    Anthropic[Anthropic SSE Stream] --> Adapter2[AnthropicToOpenAIAdapter]

    Adapter1 -->|Already OpenAI| Normalized
    Adapter2 --> Normalized[Normalized OpenAI Format]

    Normalized --> Policy[Policy receives<br/>consistent format]

    style Normalized fill:#e8f5e9
    style Policy fill:#fff4e1
```

**Files**: `src/luthien_proxy/proxy/stream_normalization.py`

---

## How to Use These Diagrams

1. **For onboarding**: Start with the result handling pattern, then explore non-streaming flow
2. **For debugging**: Follow the flow from entry point to identify where data goes
3. **For development**: Reference when adding new policies or modifying result handling
4. **For architecture review**: See the complete picture of data movement
5. **For timeline understanding**: Use sequence diagram to see hook call ordering

All diagrams use [Mermaid](https://mermaid.js.org/) syntax and render natively in:
- GitHub markdown
- GitLab markdown
- Many IDE markdown previewers
- Documentation sites (MkDocs, Docusaurus, etc.)
