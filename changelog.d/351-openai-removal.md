### Breaking Changes
- **Removed OpenAI gateway endpoint** (`/v1/chat/completions`). The proxy now exclusively supports the Anthropic `/v1/messages` endpoint. Clients using the OpenAI-compatible endpoint must migrate to the Anthropic API format.
- **Removed Codex CLI support** (`scripts/launch_codex.sh`).
- **Removed LiteLLM client** — LiteLLM is retained only for policy-internal judge LLM calls, not for request routing.
