# Streaming Pipeline Instrumentation Plan

## Objective
Build debugging and inspection capabilities at every step of the streaming response pipeline to diagnose why streaming judge policy blocks aren't reaching clients.

## Pipeline Steps

### Step 1: LiteLLM Callback Invocation ✅ COMPLETE
**Location**: `config/litellm_callback.py:async_post_call_streaming_iterator_hook()`
- **Receives**: Backend stream, request metadata
- **Returns**: Async generator of chunks to send to client
- **Tool Built**: `@instrument_callback` decorator in `src/luthien_proxy/proxy/callback_instrumentation.py`
- **Status**: ✅ Can log callback inputs, outputs, and yielded chunks
- **Tests**: E2E tests in `tests/e2e_tests/test_callback_invocation.py`

### Step 2: LiteLLM → Control Plane WebSocket Communication ✅ COMPLETE
**Location**: `config/litellm_callback.py:_forward_to_control_plane()`
- **Sends**: START message with request data, then CHUNK messages for each backend chunk
- **Tool Built**: `WebSocketMessageLogger` in `src/luthien_proxy/proxy/websocket_logger.py`
- **Status**: ✅ Integrated into `StreamConnection._sender_loop()` and `_receiver_loop()`
- **Tests**: E2E tests in `tests/e2e_tests/test_websocket_logging.py`
- **Implementation**:
  - Logs all outgoing messages (litellm → control plane) with type and keys
  - Logs all incoming messages (control plane → litellm) with type and keys
  - Detailed logging for START, CHUNK, END, ERROR messages
  - JSON parsing error logging
  - Tracks stream_id for correlation

### Step 3: Control Plane Endpoint Handling ✅ COMPLETE
**Location**: `src/luthien_proxy/control_plane/streaming_routes.py:policy_stream_endpoint()`
- **Receives**: WebSocket messages from litellm
- **Forwards to**: Policy's `generate_response_stream()`
- **Tool Built**: `StreamingEndpointLogger` in `src/luthien_proxy/control_plane/endpoint_logger.py`
- **Status**: ✅ Integrated into streaming routes
- **Tests**: E2E tests in `tests/e2e_tests/test_endpoint_logging.py`
- **Implementation**:
  - Logs START message with request data
  - Logs incoming CHUNK messages from litellm (backend output)
  - Logs POLICY invocation with policy class name
  - Logs outgoing CHUNK messages to litellm (policy output)
  - Logs END message when stream completes
  - Logs ERROR messages if failures occur
  - All logs include stream_id for correlation

### Step 4: Policy Processing ✅ COMPLETE
**Location**: Wrapper in `src/luthien_proxy/control_plane/streaming_routes.py:_forward_policy_output()`
- **Receives**: Async iterator of chunks from backend
- **Yields**: Modified/novel chunks (or blocks)
- **Tool Built**: `PolicyStreamLogger` in `src/luthien_proxy/policies/policy_instrumentation.py`
- **Status**: ✅ Instrumentation wrapped around all policy streams
- **Tests**: E2E tests in `tests/e2e_tests/test_policy_logging.py`
- **Implementation**:
  - Logs POLICY STREAM START when policy begins processing
  - Logs POLICY CHUNK IN for each chunk received from backend (via instrumented wrapper)
  - Logs POLICY CHUNK OUT for each chunk yielded by policy
  - Logs POLICY STREAM END with total chunks processed
  - All logs include stream_id and policy class name for correlation
  - Instrumentation applied via wrapper in `_forward_policy_output`, so works for ALL policies

### Step 5: Control Plane → LiteLLM WebSocket Response ✅ COMPLETE
**Location**: `src/luthien_proxy/control_plane/streaming_routes.py:_forward_policy_output()`
- **Sends**: CHUNK messages back over WebSocket, then END message
- **Tool Built**: Same `WebSocketMessageLogger` (logs incoming messages in `_receiver_loop()`)
- **Status**: ✅ Already covered by Step 2's WebSocket logger
- **Tests**: E2E tests in `tests/e2e_tests/test_websocket_logging.py`
- **Implementation**:
  - Logs all incoming WebSocket messages (control plane → litellm)
  - Logs CHUNK/END/ERROR messages
  - Same stream_id correlation as outgoing messages

### Step 6: LiteLLM Callback Chunk Processing ✅ COMPLETE
**Location**: `config/litellm_callback.py:poll_control()` and `_normalize_stream_chunk()`
- **Receives**: WebSocket messages from control plane
- **Processes**: Validates and converts to `ModelResponseStream`
- **Yields**: Chunks to client
- **Tool Built**: `CallbackChunkLogger` in `src/luthien_proxy/proxy/callback_chunk_logger.py`
- **Status**: ✅ Instrumentation integrated
- **Tests**: E2E tests in `tests/e2e_tests/test_callback_chunk_processing.py`
- **Implementation**:
  - Logs CALLBACK CONTROL IN for messages received from control plane in `poll_control()`
  - Logs CALLBACK NORMALIZED for chunk normalization results (success/failure)
  - Logs CALLBACK TO CLIENT for each chunk yielded to the client
  - All logs include stream_id and chunk index for correlation

## E2E Validation ⏸️ PENDING
**Goal**: Create a test that traces a single chunk through all 6 steps
- **Tool Needed**: End-to-end pipeline tracer with correlation IDs
- **Status**: ⏸️ Pending after all steps instrumented

## Progress Log

### 2025-10-03
- ✅ Completed Step 1: Built callback instrumentation infrastructure
- ✅ Created E2E tests for callback invocation
- ✅ Verified callbacks work with test callback
- ✅ Completed Step 2: Built WebSocket message logger
  - Created `WebSocketMessageLogger` class with log_outgoing/log_incoming methods
  - Integrated into `StreamConnection._sender_loop()` and `_receiver_loop()`
  - Logs both directions of WebSocket communication (steps 2 and 5)
- ✅ Created E2E tests for WebSocket logging
  - test_websocket_outgoing_messages_logged: verifies OUT messages logged
  - test_websocket_incoming_messages_logged: verifies IN messages logged
  - test_websocket_logs_include_stream_id: verifies stream ID correlation
  - Fixed ANSI escape code handling in log parsing
  - Fixed timing issues with docker logs --since

### 2025-10-04
- ✅ Completed Step 3: Control plane endpoint logging
  - `StreamingEndpointLogger` was already implemented and integrated
  - Created comprehensive E2E tests in `test_endpoint_logging.py`
  - All 5 tests passing (START, POLICY, CHUNKS, END, correlation)
- ✅ Completed Step 4: Policy stream instrumentation
  - Created `PolicyStreamLogger` in `policy_instrumentation.py`
  - Integrated via wrapper functions in `streaming_routes.py:_forward_policy_output()`
  - Instrumentation works for ALL policies (applied at call site, not in each policy)
  - Created E2E tests in `test_policy_logging.py`
  - All 4 tests passing (STREAM START, CHUNKS, STREAM END, correlation)
- ✅ Completed Step 6: Callback chunk processing logging
  - Created `CallbackChunkLogger` in `callback_chunk_logger.py`
  - Integrated into `config/litellm_callback.py:poll_control()` and yield points
  - Logs CONTROL IN (messages from control plane), NORMALIZED (validation results), TO CLIENT (chunks yielded)
  - Created E2E tests in `test_callback_chunk_processing.py` (4 tests)
  - All 6 pipeline steps now instrumented with correlation IDs
