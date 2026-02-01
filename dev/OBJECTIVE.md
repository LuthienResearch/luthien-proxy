Objective: Implement a minimal `/v1/responses` shim for Codex so we can switch `wire_api=responses` and avoid chat deprecation issues.

Background
- Codex now warns that `wire_api=chat` is deprecated and will be removed soon.
- Our proxy only supports `/v1/chat/completions` and `/v1/messages`, so Codex is forced into chat mode.
- This has caused rendering issues (token-per-line/bullet output) and leaves us exposed to upcoming deprecations.

Acceptance
- `/v1/responses` endpoint exists and routes through the existing policy pipeline.
- Minimal request support: `input` (string or list), `model`, optional `stream`.
- Streaming responses emit valid Responses API SSE events.
- Non-streaming responses return a valid Responses API payload.
- Add unit tests for streaming and non-streaming.
- Update `scripts/launch_codex.sh` to set `wire_api=responses` once endpoint is in place.

Plan (TDD)
1. Add failing tests for `/v1/responses` (streaming + non-streaming) using a minimal payload.
2. Implement request adapter: map Responses `input` → OpenAI chat messages.
3. Implement response adapter: map OpenAI `ModelResponse` → Responses format.
4. Implement SSE adapter: emit `response.output_text.delta` + `response.completed` events.
5. Update Codex launcher to `wire_api=responses`.
