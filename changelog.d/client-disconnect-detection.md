---
category: Features
pr: 465
---

**Cancel upstream LLM calls on client disconnect**: Streaming responses now check `request.is_disconnected()` before forwarding each chunk. When a client drops mid-stream, the generator returns early, closing the upstream Anthropic connection and stopping further token generation.
