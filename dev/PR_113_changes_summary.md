# PR #113 Changes Summary

## Items Removed from TODO.md (now in CHANGELOG)

| Removed TODO Item | CHANGELOG Entry | PR Link |
|-------------------|-----------------|---------|
| `[x] create_app dependency injection` | Dependency injection for `create_app()` | [#105](https://github.com/LuthienResearch/luthien-proxy/pull/105) |
| `[x] Break up llm/types.py into submodules` | Reorganize LLM types into separate OpenAI and Anthropic modules | [#117](https://github.com/LuthienResearch/luthien-proxy/pull/117) |
| `[x] Complete strict typing for LLM types` | (part of LLM types reorganization) | [#117](https://github.com/LuthienResearch/luthien-proxy/pull/117) |
| `[x] Move Request class to llm/types/openai.py` | (part of LLM types reorganization) | [#117](https://github.com/LuthienResearch/luthien-proxy/pull/117) |
| `[x] LiteLLM multimodal routing issue (#108)` | Fix validation error when images in Anthropic requests | [#108](https://github.com/LuthienResearch/luthien-proxy/issues/108), [#103](https://github.com/LuthienResearch/luthien-proxy/pull/103), [#104](https://github.com/LuthienResearch/luthien-proxy/pull/104) |
| `[x] Update README post v2-migration` | README and documentation updates | [#68](https://github.com/LuthienResearch/luthien-proxy/pull/68) |
| `[x] Verify all environment variables documented` | (part of README updates) | [#96](https://github.com/LuthienResearch/luthien-proxy/pull/96) |
| `[x] Review LiteLLMClient instantiation pattern` | LiteLLMClient singleton pattern | [#69](https://github.com/LuthienResearch/luthien-proxy/pull/69) |
| `[x] Implement proper task tracking for event publisher` | Event publisher task tracking | [#83](https://github.com/LuthienResearch/luthien-proxy/pull/83) |

## Items Added to TODO.md (new from dogfooding)

| New TODO Item | Section | Reference |
|---------------|---------|-----------|
| `/compact` fails with "Tool names must be unique" error | Bugs | [Google Drive](https://drive.google.com/file/d/1Gn2QBZ2WqG6qY0kDK4KsgxJbmmuKRi1S/view), PR #112 |
| Review user-stories for priority adjustments | Pending Review | PR #114 |
| `[Future] Conversation history browser & export` (new use case) | Policy UI & Admin | Dogfooding 2025-12-15 |
| Factor out common gateway route logic | Code Quality | - |
| Retrospective on dogfooding sessions | Dogfooding & UX | [Google Drive](https://drive.google.com/file/d/1YMd0CEgEF2vtvyAy70_SZQFFzp1ZG7C-/view) |
| "Logged by Luthien" indicator policy | Dogfooding & UX | Dogfooding 2025-12-16 |
| Include tool calls in conversation_transcript | Dogfooding & UX | Dogfooding 2025-12-16 |
| DB Migration: call_id -> transaction_id | Infrastructure | - |
| Add Prometheus metrics endpoint | Infrastructure | - |
| Convert Loki validation scripts to e2e tests | Testing | - |
| Create visual database schema documentation | Documentation | Dogfooding 2025-12-16 |

## Items Moved (not deleted)

| Item | Old Section | New Section |
|------|-------------|-------------|
| `[Future] Smart dev key hint` | Policy UI & Admin | Policy UI & Admin (same) |
| `Activity Monitor missing auth indicator` | Policy UI & Admin | Policy UI & Admin (same) |

## Other Changes

- Coverage stat updated: `~90%` → `~78%` (reflects actual current state)
- Renamed section: "Scott Review" → "Pending Review" (more generic)
- Removed empty sections: "Architecture Improvements", "Type System Improvements", "Multimodal / Images"
