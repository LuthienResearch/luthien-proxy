# `llm-as-judge` demo (stub — open for implementation discussion)

Stub PR. The goal is captured below; implementation is up to Jai.

## Goal

The LLM-as-judge should evaluate responses against an arbitrary document provided by the user.

The user's document is whatever they treat as the spec a Claude response should conform to: a requirements doc, a CLAUDE.md, a style guide, a checklist. The judge reads the document, reads the completed response, and returns pass/fail plus reasoning.

The pitch framing (per the May 19 Ivan call) is *"how can I use LLM-as-judge to make sure Claude follows my company's / org's rules?"* The personal-workflow framing (per the May 18 pre-Ivan call with Jai) is the same mechanism applied to Scott's own response requirements.

## Source context

- [PR #755](https://github.com/LuthienResearch/luthien-proxy/pull/755) — merged demo infrastructure (fabricator + protector + setup / toggle / reset). The pattern this PR extends.
- [`dev/demo/README.md`](../README.md) — meta-pattern for adding new demos.
- [`dev/demo/rm-rf/README.md`](../rm-rf/README.md) — reference per-demo runbook.
- May 18 pre-Ivan Jai-Scott Plaud transcript — verbatim implementation discussion (Stop hook vs proxy policy, verification / follow-up agents, the "rules sensitive to entire context but not tool-specific" framing).
- May 19 post-Ivan Jai-Scott Plaud transcript — Scott names LLM-as-judge as the priority demo; Jai offers to build it given a rules list + failing-response transcripts.
- Scott's current 12-rule + cross-surface requirements docs (the artifact the judge would evaluate against): shared with Jai separately.

## Open implementation questions for Jai

- **Mechanism.** Stop hook in Claude Code (what Jai recommended on May 18 for the personal-workflow case) vs Luthien proxy policy that judges outbound responses vs both. The proxy version slots into the `dev/demo/<name>/` pattern; the Stop-hook version is outside Luthien's process but inside the demo story.
- **Input contract.** What does the user actually hand the judge? A file path to a markdown / text doc? A URL? An inline string? Multiple docs concatenated? Does the demo template ship with an example user-doc the way `rm-rf` ships with a planted prompt-injection memo?
- **Failure verifiability.** Some rule violations are step-local and visible in a single response. Others depend on full conversation state ("plan fully carried out to completion") and are not visible in a single response. Per Jai on May 19: *"depending on this doable in theory... if it depends on the whole state rather than any given step, it's gonna be harder."* What scope is in-bounds for the first version of this demo?
- **Output contract.** Pass / fail / re-prompt? Pass / fail with reasoning surfaced to the user? Pass / fail with retry-instruction routed back into the next turn?
- **Fabricator side.** PR #755's pattern uses a fabricator policy to force a deterministic bad action. For LLM-as-judge, what's the analogue? A fabricator that emits a response known to violate a planted rule in the template user-doc? Or is the determinism handled differently because the judge's verdict is itself probabilistic?
- **Anything else you'd want spelled out before starting.**

Suggestions welcome on this PR; happy to revise the stub or wait until you've had time to think about the shape.
