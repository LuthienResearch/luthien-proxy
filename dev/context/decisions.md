# Technical Decisions

Why certain approaches were chosen over alternatives.

---

## Configuration (2025-10-08)

**Decision**: Use separate YAML files for LiteLLM config (`litellm_config.yaml`) and Luthien policy config (`luthien_config.yaml`)

**Rationale**: Separates concerns - LiteLLM manages model routing, Luthien manages policy decisions. This allows independent evolution of each configuration.

## Policy Loading (2025-10-08)

**Decision**: Load policy class dynamically via `LUTHIEN_POLICY_CONFIG` environment variable

**Rationale**: Allows swapping policies without code changes, supports different policies for different environments.

---

(Add new decisions as they're made with timestamps: YYYY-MM-DD)
