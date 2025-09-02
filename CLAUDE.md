# CLAUDE.md

## Project Overview

Luthien Control: Redwood Research-style AI Control as a production LLM proxy built on LiteLLM.

## Core Principles
 - KISS. You keep it simple.
 - Fail fast. You do not try to handle unexpected conditions or handle unexpected arguments. Rather than building complex logic to try to handle every case, you write code to fail fast when the unexpected happens.
 - YAGNI. You do not build more than what you need. You do not try to proactive design for problems we *might* have at some point. You solve the problem we have *right now*.

 You are an engineer who writes code for **human brains, not machines**. You favor code that is simple to undertand and maintain. Remember at all times that the code you will be processed by human brain. The brain has a very limited capacity. People can only hold ~4 chunks in their working memory at once. If there are more than four things to think about, it feels mentally taxing.

Here's an example that's hard for people to understand:
```
if val > someConstant // (one fact in human memory)
    && (condition2 || condition3) // (three facts in human memory), prev cond should be true, one of c2 or c3 has be true
    && (condition4 && !condition5) { // (human memory overload), we are messed up by this point
    ...
}
```

A good example, introducing intermediate variables with meaningful names:
```
isValid = val > someConstant
isAllowed = condition2 || condition3
isSecure = condition4 && !condition5
// (human working memory is clean), we don't need to remember the conditions, there are descriptive variables
if isValid && isAllowed && isSecure {
    ...
}
```

- No useless "WHAT" comments, don't write a comment if it duplicates the code. Only "WHY" comments, explaining the motivation behind the code, explaining an especially complex part of the code or giving a bird's eye overview of the code.
- Make conditionals readable, extract complex expressions into intermediate variables with meaningful names.
- Prefer early returns over nested ifs, free working memory by letting the reader focus only on the happy path only.
- Prefer composition over deep inheritance, don’t force readers to chase behavior across multiple classes.
- Don't write shallow methods/classes/modules (complex interface, simple functionality). An example of shallow class: `MetricsProviderFactoryFactory`. The names and interfaces of such classes tend to be more mentally taxing than their entire implementations. Having too many shallow modules can make it difficult to understand the project. Not only do we have to keep in mind each module responsibilities, but also all their interactions.
- Prefer deep method/classes/modules (simple interface, complex functionality) over many shallow ones.
- Don’t overuse language featuress, stick to the minimal subset. Readers shouldn't need an in-depth knowledge of the language to understand the code.
- Use self-descriptive values, avoid custom mappings that require memorization.
- Don’t abuse DRY, a little duplication is better than unnecessary dependencies.
- Avoid unnecessary layers of abstractions, jumping between layers of abstractions is mentally exhausting, linear thinking is more natural to humans.
- NEVER write comments about code changes ("added feature X", "replaced A with B", "moved from otherplace.py to here"). Comments are for understanding how and why the code works the way it is right now, not for annotating diffs.

## Essential Commands

```bash
# Setup & start
./scripts/quick_start.sh

# After code changes
docker compose restart control-plane    # or litellm-proxy

# Run tests & lint
uv run pytest
uv run ruff format
uv run ruff check --fix

# Debug
docker compose logs -f [service_name]
```

## Architecture

1. **LiteLLM Proxy** (`src/luthien_control/proxy/`): CustomLogger with async hooks
   - Pre-call: Modify requests, add episode IDs
   - Post-call: Score responses, trigger control protocols
   - Streaming: Monitor/modify chunks in real-time

2. **Control Plane** (`src/luthien_control/control_plane/`): FastAPI orchestration
   - `/policy/evaluate`: Main decision endpoint
   - `/resample`: K-resample protocol
   - Manages budgets and audit allocation

3. **Monitors** (`src/luthien_control/monitors/`):
   - Trusted: Local model scoring
   - Untrusted: Main model with honeypots
   - Paraphraser: Prevent collusion

4. **Policies** (`src/luthien_control/policies/`): YAML/JSON configuration

5. **Data**: PostgreSQL (persistent) + Redis (ephemeral)

## Code Standards

- Use beartype for type checking
- Async-first for all proxy operations
- Fail-safe: Fall back to trusted-only on honeypot failures
- Correlate via LiteLLM's `call_id`

## Testing

Run full test suite before commits:
```bash
uv run pytest
uv run pre-commit run --all-files
```

## References

- Implementation phases: `plan.md`
- Note: Use "docker compose" not "docker-compose"
