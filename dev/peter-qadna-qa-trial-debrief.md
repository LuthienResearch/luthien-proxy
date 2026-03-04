# Peter / QADNA — QA Trial Debrief

**Date:** 2026-02-25

**Source:** [QA Trial Report (Google Doc)](https://docs.google.com/document/d/1xugPuJjtfxXw3ale54rdhqAlsH5wcvo31RtPVPJLgz4/edit), [Loom video](https://www.loom.com/share/c4e1b1ef83224dcca9420de8a448d846), video transcript (inline in doc)

---

## Who is Peter?

- **CTO & co-founder of QADNA** — QA collaboration company, Bucharest, Romania
- **Career:** QA at Vodafone → test automation → Google → software development
- **Company:** Plugs into dev teams, sets up test automation, owns QA process. Building own QA SaaS platform (Next.js frontend, Go backend, TypeScript workspace containers, Docker, Redis, Playwright)
- **AI usage:** Claude Code daily, prefers VS Code over Cursor, **spawns up to 14 Claude Code instances simultaneously**. Also uses Codex, MCPs (Playwright, AWS, etc.)
- **Pushing towards:** AI-driven testing AND testing of AI agents themselves. Has multi-agentic flows for autonomous test generation using Playwright + Gherkin

## Key Quote

> "I haven't seen something like this before, to be honest. And I think this is something that should exist, and people should have it."

## Bugs Found in Luthien (9 total)

| # | Bug | Severity | Existing? |
|---|-----|----------|-----------|
| 1 | **Activity Monitor broken** — `emitter.py` imports under `TYPE_CHECKING` but uses at runtime → `NameError` silently swallowed → Redis pub/sub never gets events | High | **New** |
| 2 | Policy Diff Viewer 404 on `/debug/calls/{call_id}` | Medium | **New** |
| 3 | Conversation history shows Turn entries but no contents — Anthropic processor not wired to `DefaultTransactionRecorder` | Medium | **New** |
| 4 | `DebugLoggingPolicy` logs unclear where to find them | Low | **New** |
| 5 | `SamplePydanticPolicy` unimplemented — causes all requests to fail when selected | Medium | **New** |
| 6 | DeSlop policy activation error ([screenshot](https://imgur.com/a/GHagJn7)) | Medium | **New** |
| 7 | Policy examples in README don't match actual app policy names | Low | Known (PR #179 in progress) |
| 8 | No way to configure custom policy from `/policy-config` page | Medium | Known (TODO exists) |
| 9 | Can't test Active Policy without API key (single users) | Low | **New** |

### Onboarding Issues (4 total)

| # | Issue | Existing? |
|---|-------|-----------|
| 1 | Python 3.13+ requirement not communicated upfront — `.env.example` and `quick_start.sh` don't check/warn | **New** |
| 2 | No guidance for OAuth/Claude Max users — README implies API key is the only option | **New** |
| 3 | `AUTH_MODE` in DB silently overrides `.env` | **Known** — PR #222 |
| 4 | Stale/expired API key gives nested JSON error, not clear message | **New** |

## Frustrations (from transcript) — Added to Frustrations DB #55-59

| Frustration | Severity | DB ID |
|-------------|----------|-------|
| Unsupervised agents become overly conservative — self-optimize for token savings, do minimal work | 8-9/10 | #55 |
| Destructive actions fear — API keys, prod database access, integrations | ~10/10 | #56 |
| Token costs prohibitive for multi-agentic testing | High | #57 |
| Autonomous processes can't stay running >10 hours | High | #58 |
| After compacting, Claude forgets repeatedly-stated instructions | High | #59 |

**Note:** Frustrations DB append failed (403 permission error) — need to add #55-59 manually.

## Action Items

- [ ] **File bugs** for the 6 new findings (emitter.py, diff viewer 404, conversation history, SamplePydanticPolicy, DeSlop activation, API key test mode)
- [ ] **Review Peter's proposed fix** for emitter.py (`cast()` calls under `TYPE_CHECKING` — just remove the casts)
- [ ] **Add Frustrations #55-59** to Frustrations DB manually (append failed with 403)
- [ ] **Follow up with Peter** — he explicitly offered to continue working with us toward April demo day
- [ ] **Evaluate QADNA as QA partner** — they found real bugs, understand our stack, and want to help
- [ ] **Add Python version check** to `quick_start.sh` and `.env.example`
- [ ] **Add OAuth/Claude Max guidance** to README

## Requirements Implications

| Finding | Uber Req | Implication |
|---------|----------|-------------|
| Activity Monitor completely broken (silent failure) | #1 Stability | Core observability feature non-functional — users can't see what's happening |
| Policy config UI can't configure custom policies | #3 Policies | Users can't actually set up policies through the UI |
| Onboarding requires Python 3.13+ without warning | #2 Time-to-value | First-time setup fails silently on common Python versions |
| AUTH_MODE DB override (confirmed by external user) | #1 Stability | Already known (#222) but now validated by external user |
| No OAuth/Max guidance | #2 Time-to-value | Users think API key is the only option |

## Peter's Landing Page Feedback

> "I like this. This is very simple. I love how this looks... I don't find this confusing at all. It's very well simplified."

- Dark mode appreciated
- Clear to engineers who use Claude Code
- Acknowledged it might not be clear to non-technical users (acceptable — our target is devs)
