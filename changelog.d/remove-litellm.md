---
category: Refactors
pr: 441
---

**Remove litellm dependency**: Replaced litellm with a thin `llm/completion.py` wrapper backed by the Anthropic SDK. Eliminates ~62 transitive packages. Judge policies now use Anthropic models directly; multi-provider support planned behind the same interface. Breaking: non-Anthropic judge models (GPT-4o, Ollama) no longer supported.
