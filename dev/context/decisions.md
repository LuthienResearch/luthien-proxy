# Technical Decisions

Why certain approaches were chosen over alternatives.

**Format**: Each entry is a subsection with a title, timestamp (YYYY-MM-DD), and content (decision + rationale).
If updating existing content significantly, note it: `## Topic (2025-10-08, updated 2025-11-15)`

---

## Configuration (2025-10-08)

**Decision**: Use separate YAML files for LiteLLM config (`litellm_config.yaml`) and Luthien policy config (`luthien_config.yaml`)

**Rationale**: Separates concerns - LiteLLM manages model routing, Luthien manages policy decisions. This allows independent evolution of each configuration.

## Policy Loading (2025-10-08)

**Decision**: Load policy class dynamically via `LUTHIEN_POLICY_CONFIG` environment variable

**Rationale**: Allows swapping policies without code changes, supports different policies for different environments.

## Platform Vision and Scope (2025-10-08)

**Decision**: Build general-purpose infrastructure for LLM policy enforcement that can support both simple and adversarially robust policies.

**Rationale**: The platform should enable developers to easily write and enforce policies on LLM usage, ranging from prosaic policies (rate limiting, content filtering, PII detection) to complex adversarially robust policies like Redwood Research's AI Control methodology.

The architecture (centralized control plane, thin proxy, pluggable policies) supports this range:
- Control plane can implement trusted monitoring/editing logic for adversarial control
- Policies can be simple or complex depending on use case
- Callback hooks allow interception and modification at multiple points
- Reference implementations of complex policies (like Redwood-style control) will be provided alongside the infrastructure

This is infrastructure-first: Redwood AI Control is an important use case the platform should support, not the defining architecture.

---

(Add new decisions as they're made with timestamps: YYYY-MM-DD)
