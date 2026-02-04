# User Story 3: AI Safety Researcher - Multi-Reviewer Evaluation Pipeline

## Persona

**Sam** - A PhD student researching AI control mechanisms. Sam's thesis focuses on ensemble approaches to AI safety—using multiple specialized reviewers rather than a single judge.

## Context

Sam hypothesizes that specialized reviewers (one for code safety, one for data privacy, one for resource usage) outperform a single general-purpose judge. Sam needs to extract evaluation rules from requests and fan them out to parallel reviewers.

## Story

> As Sam, I want to configure multiple specialized LLM reviewers that evaluate requests in parallel against different rule sets, so that I can study ensemble approaches to AI safety evaluation.

## Scenario

1. Sam configures a **Multi-Reviewer Policy** with three specialized judges:
   - **CodeSafetyReviewer**: Evaluates shell commands, file operations, git actions
   - **DataPrivacyReviewer**: Scans for PII, credentials, sensitive data patterns
   - **ResourceReviewer**: Checks for unbounded loops, large file operations, network calls
2. Sam defines **rule extraction**: The policy automatically extracts relevant rules from the request context:
   - Request mentions "delete" → activates deletion rules in CodeSafetyReviewer
   - Request contains file paths → activates file access rules
   - Request mentions "database" → activates data rules in DataPrivacyReviewer
3. Engineer makes a request: "Write a script to batch process all customer records and upload to S3"
4. The proxy:
   - Extracts rules: file_operations, database_access, network_upload, batch_processing
   - Fans out to all three reviewers **in parallel**
   - Each reviewer evaluates against their specialized ruleset
   - Results aggregated: CodeSafety=ALLOW, DataPrivacy=BLOCK(PII risk), Resource=WARN(unbounded)
5. Aggregation policy: ANY_BLOCK → block the request
6. Response includes **LLM-rephrased explanation**:
   - Raw: `{"reviewer": "DataPrivacyReviewer", "decision": "BLOCK", "reason": "batch processing customer records without explicit PII handling"}`
   - Rephrased: "I've paused this request. Processing customer records in batch could expose personal information. Consider: (1) filtering to non-PII fields, (2) adding anonymization, or (3) processing in a secure enclave."
7. Sam collects data across 1000 requests:
   - Per-reviewer accuracy (vs. human labels)
   - Latency impact of parallel vs. sequential evaluation
   - Agreement/disagreement patterns between reviewers
   - Rule extraction precision/recall
8. Sam's analysis shows the ensemble catches 40% more issues than single-judge with only 20% latency increase

## Acceptance Criteria

- [ ] Multiple reviewer policies can be configured and run in parallel
- [ ] Rule extraction identifies relevant rules from request content
- [ ] Parallel evaluation with configurable aggregation (ANY_BLOCK, MAJORITY, UNANIMOUS)
- [ ] Per-reviewer decision logging with reasoning
- [ ] LLM rephrasing of technical decisions into user-friendly messages
- [ ] Latency metrics per reviewer for performance analysis
- [ ] Reviewer disagreement logging for research analysis
- [ ] Configurable reviewer specialization (different prompts, models, rules)

## Required Features

### Multi-Reviewer Framework

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-kjr` | Multi-reviewer parallel policy evaluation | open | P2 |
| `luthien-proxy-7ib` | Rule extraction from request context | open | P2 |

### User Communication

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-8gv` | LLM rephrasing of policy decisions | open | P2 |
| `luthien-proxy-fsb` | Message injection into response stream | open | P1 |

### Policy Framework (Existing)

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-mro` | Add policy composition (chaining multiple policies) | open | P3 |
| `luthien-proxy-0g0` | Policy API: Prevent common streaming mistakes | open | P2 |

### Observability

| Issue | Title | Status | Priority |
|-------|-------|--------|----------|
| `luthien-proxy-7tj` | Add Prometheus metrics endpoint | open | P2 |

## Technical Touchpoints

- `policies/multi_reviewer_policy.py`: Ensemble evaluation framework
- `policies/rule_extractor.py`: Dynamic rule extraction from requests
- `llm/parallel_client.py`: Parallel LLM calls with aggregation
- `llm/rephraser.py`: Convert technical decisions to user messages
- `storage/reviewer_decisions`: Per-reviewer decision logging
- `observability/`: Latency tracking per reviewer

## Implementation Status

**Overall Progress**: Partial (Updated 2026-01-16)

### Phase 1: Rule Extraction
- [ ] Design rule extraction interface
- [ ] Implement keyword/pattern-based rule activation
- [ ] Add configurable rule sets per reviewer type
- [ ] Test rule extraction accuracy

### Phase 2: Parallel Evaluation
- [x] Implement parallel LLM call orchestration — `ParallelRulesPolicy` uses `asyncio.gather()` to run multiple `call_rule_judge()` LLM calls concurrently (verified: 41 unit tests pass)
- [ ] Add timeout handling for slow reviewers
- [ ] Implement aggregation strategies (ANY_BLOCK, MAJORITY, UNANIMOUS)
- [x] Handle partial failures gracefully — `return_exceptions=True` in gather, exceptions converted to `RuleViolation` with fail-secure blocking

### Phase 3: Decision Logging
- [x] Design reviewer decision schema — `RuleResult` and `RuleViolation` dataclasses in `parallel_rules_config.py`; DB table `conversation_judge_decisions` exists but policy uses generic `conversation_events` table instead
- [x] Store per-reviewer decisions with reasoning — events recorded via `record_event()` with `rule_name`, `probability`, `explanation`; verified event path works (800+ events in DB from other policies)
- [ ] Add latency metrics per reviewer
- [ ] Implement disagreement detection and logging

### Phase 4: LLM Rephrasing
- [ ] Design rephrasing prompt templates
- [ ] Implement rephrasing service
- [ ] Cache common rephrasings for performance
- [ ] A/B test rephrased vs. raw messages

### Phase 5: Research Export
- [ ] Add bulk export for reviewer decisions
- [ ] Include confusion matrix support
- [ ] Export latency distributions
- [ ] Support custom analysis queries

## Dependencies

```
luthien-proxy-7ib (Rule extraction)
    └── luthien-proxy-kjr (Multi-reviewer evaluation)

luthien-proxy-fsb (Message injection)
    └── luthien-proxy-8gv (LLM rephrasing)
```

## Configuration Example

```yaml
policy:
  class: "luthien_proxy.policies.multi_reviewer:MultiReviewerPolicy"
  config:
    aggregation: "any_block"  # any_block | majority | unanimous
    timeout_seconds: 5
    reviewers:
      - name: "CodeSafetyReviewer"
        model: "your-reviewer-model"
        rules:
          - "Block rm -rf commands"
          - "Block git push --force to main"
          - "Warn on file deletions"
        triggers: ["delete", "rm", "git", "file"]

      - name: "DataPrivacyReviewer"
        model: "your-reviewer-model"
        rules:
          - "Block exposure of PII"
          - "Block credential logging"
          - "Warn on database bulk operations"
        triggers: ["customer", "user", "password", "database", "records"]

      - name: "ResourceReviewer"
        model: "your-reviewer-model"
        rules:
          - "Warn on unbounded loops"
          - "Block large file downloads"
          - "Warn on external network calls"
        triggers: ["while", "for", "download", "upload", "http", "api"]

    rephrasing:
      enabled: true
      model: "your-rephrasing-model"
      style: "helpful_assistant"  # helpful_assistant | formal | brief
```

## Notes

- Parallel evaluation should use asyncio.gather with return_exceptions=True
- Consider caching rule extraction results for repeated request patterns
- Rephrasing should be optional and skippable for latency-sensitive deployments
- Research mode should disable caching for accurate latency measurements
- Consider adding "explanation budget" to limit rephrasing tokens
