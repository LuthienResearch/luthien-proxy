# Policy Interface Refactor Plan

## Goal

Refactor policies to use:
1. A platform-agnostic `BasePolicy` class
2. Platform-specific interface classes (`OpenAIPolicyInterface`, `AnthropicPolicyInterface`)
3. Runtime `isinstance` checks in processors
4. Merged policy classes that inherit from base + interfaces they support

## Architecture

```python
# policy_core/base_policy.py
class BasePolicy:
    @property
    def short_policy_name(self) -> str:
        return self.__class__.__name__

# policy_core/openai_interface.py
class OpenAIPolicyInterface(ABC):
    @abstractmethod
    async def on_openai_request(self, request: OpenAIRequest, ctx: PolicyContext) -> OpenAIRequest: ...

    @abstractmethod
    async def on_openai_response(self, response: OpenAIResponse, ctx: PolicyContext) -> OpenAIResponse: ...

    @abstractmethod
    async def on_openai_chunk(self, chunk: dict, ctx: StreamingPolicyContext) -> ...: ...
    # ... other streaming hooks

# policy_core/anthropic_interface.py
class AnthropicPolicyInterface(ABC):
    @abstractmethod
    async def on_anthropic_request(self, request: AnthropicRequest, ctx: PolicyContext) -> AnthropicRequest: ...

    @abstractmethod
    async def on_anthropic_response(self, response: AnthropicResponse, ctx: PolicyContext) -> AnthropicResponse: ...

    @abstractmethod
    async def on_anthropic_stream_event(self, event: MessageStreamEvent, ctx: PolicyContext) -> MessageStreamEvent | None: ...

# Example merged policy
class NoOpPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicPolicyInterface):
    """Supports both platforms."""

    async def on_openai_request(self, request, ctx):
        return request

    async def on_anthropic_request(self, request, ctx):
        return request
    # ... all hooks implemented
```

## Workstreams (Parallel Execution)

### Workstream 1: Infrastructure (MUST COMPLETE FIRST - 1 agent)

Create the interface classes that all other work depends on.

**Tasks:**
1. Create `src/luthien_proxy/policy_core/base_policy.py`
   - Minimal base class with `short_policy_name` property
   - Any shared utilities

2. Create `src/luthien_proxy/policy_core/openai_interface.py`
   - `OpenAIPolicyInterface(ABC)` with all required OpenAI hooks
   - Look at current `PolicyProtocol` for the hook signatures
   - Hooks: `on_openai_request`, `on_openai_response`, streaming hooks

3. Create `src/luthien_proxy/policy_core/anthropic_interface.py`
   - `AnthropicPolicyInterface(ABC)` with all required Anthropic hooks
   - Look at current `AnthropicPolicyProtocol` for signatures
   - Hooks: `on_anthropic_request`, `on_anthropic_response`, `on_anthropic_stream_event`

4. Update `src/luthien_proxy/policy_core/__init__.py` exports

5. Add basic unit tests for the interfaces

**Output:** Interface classes exist and can be imported.

---

### Workstream 2: Processor Updates (1 agent, can start after Workstream 1)

Update processors to use `isinstance` checks.

**Tasks:**
1. Update `src/luthien_proxy/pipeline/anthropic_processor.py`
   - Import `AnthropicPolicyInterface`
   - Add check: `if not isinstance(policy, AnthropicPolicyInterface): raise TypeError(...)`
   - Update method calls to use new hook names (`on_anthropic_*`)

2. Update `src/luthien_proxy/pipeline/processor.py` (OpenAI path)
   - Import `OpenAIPolicyInterface`
   - Add check: `if not isinstance(policy, OpenAIPolicyInterface): raise TypeError(...)`
   - Update method calls to use new hook names (`on_openai_*`)

3. Update any streaming executors that call policy hooks

4. Update tests for processors

---

### Workstream 3-9: Policy Merges (7 agents, can start after Workstream 1)

Each policy gets merged from separate OpenAI/Anthropic versions into one class.

**For each policy:**
1. Merge the Anthropic version (from `policies/anthropic/`) into the main policy file
2. Update the class to inherit from `BasePolicy` + appropriate interfaces
3. Rename hooks to use `on_openai_*` / `on_anthropic_*` naming
4. Merge tests into one test file
5. Delete the old `policies/anthropic/` file

**Policies to merge:**

| # | Policy | Files to Merge |
|---|--------|----------------|
| 3 | NoOpPolicy | `noop_policy.py` + `anthropic/noop.py` |
| 4 | AllCapsPolicy | `all_caps_policy.py` + `anthropic/allcaps.py` |
| 5 | DebugLoggingPolicy | `debug_logging_policy.py` + `anthropic/debug_logging.py` |
| 6 | SimplePolicy | `simple_policy.py` + `anthropic/simple_policy.py` |
| 7 | StringReplacementPolicy | `string_replacement_policy.py` + `anthropic/string_replacement.py` |
| 8 | SimpleJudgePolicy | `simple_judge_policy.py` + `anthropic/simple_judge.py` |
| 9 | ToolCallJudgePolicy | `tool_call_judge_policy.py` + `anthropic/tool_call_judge.py` |

---

### Workstream 10: Cleanup (1 agent, after all merges complete)

1. Delete `src/luthien_proxy/policies/anthropic/` directory
2. Delete old protocol files if superseded
3. Update `policy_core/__init__.py` exports
4. Update `policies/__init__.py` exports
5. Run full `./scripts/dev_checks.sh`
6. Commit all changes

---

## Execution Order

```
Workstream 1 (Infrastructure)
         |
         v
    +----+----+----+----+----+----+----+----+
    |    |    |    |    |    |    |    |    |
    v    v    v    v    v    v    v    v    v
   W2   W3   W4   W5   W6   W7   W8   W9  (parallel)
    |    |    |    |    |    |    |    |
    +----+----+----+----+----+----+----+
                    |
                    v
              Workstream 10 (Cleanup)
```

## Agent Dispatch Commands

After Workstream 1 completes, dispatch these in parallel:

```
# Processor updates
Agent: Update processors to use isinstance checks for policy interfaces

# Policy merges (7 agents)
Agent: Merge NoOpPolicy - combine policies/noop_policy.py with policies/anthropic/noop.py
Agent: Merge AllCapsPolicy - combine policies/all_caps_policy.py with policies/anthropic/allcaps.py
Agent: Merge DebugLoggingPolicy - combine policies/debug_logging_policy.py with policies/anthropic/debug_logging.py
Agent: Merge SimplePolicy - combine policies/simple_policy.py with policies/anthropic/simple_policy.py
Agent: Merge StringReplacementPolicy - combine policies/string_replacement_policy.py with policies/anthropic/string_replacement.py
Agent: Merge SimpleJudgePolicy - combine policies/simple_judge_policy.py with policies/anthropic/simple_judge.py
Agent: Merge ToolCallJudgePolicy - combine policies/tool_call_judge_policy.py with policies/anthropic/tool_call_judge.py
```

## Notes

- Agents working on policy merges can assume interfaces exist (Workstream 1 output)
- If an agent needs interface signatures, they can check the interface files
- Keep existing test coverage - tests should be merged, not deleted
- `simple_noop_policy.py` and `parallel_rules_policy.py` are low priority and can be done later
- The `base_policy.py` that exists today has OpenAI-specific logic - it needs to be replaced with a minimal version
