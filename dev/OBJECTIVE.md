# SimpleLLMPolicy

Add a new policy that applies plain-English instructions to LLM response blocks
using a configurable judge LLM. Supports pass-through, text/tool replacement,
cross-type replacement. Works with both OpenAI and Anthropic APIs in streaming
and non-streaming modes.

## Acceptance

- SimpleLLMPolicy class with Pydantic config, YAML-loadable
- Judge LLM via LiteLLM with JSON mode, structured pass/replace protocol
- Cross-type replacement (tool->text, text->tool)
- Streaming + non-streaming for both API formats
- Automatic stop_reason correction
- Configurable on_error (pass/block)
- Unit tests, dev_checks pass
