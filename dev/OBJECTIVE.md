# Objective

Add two new policies: ParallelRulesPolicy (apply multiple rewriting rules in parallel with LLM-based refinement) and ClaudeMdRulesPolicy (extract rules from CLAUDE.md at session start, persist to DB, apply on subsequent turns).

## Description

These policies enable automatic enforcement of user-defined writing rules. ParallelRulesPolicy is a general-purpose engine that runs multiple text-rewriting rules in parallel via LLM calls, then merges conflicting rewrites through a refinement round. ClaudeMdRulesPolicy builds on top of it by automatically extracting rules from CLAUDE.md content in the conversation at session start, storing them in the database, and applying them on future turns.

## Approach

1. **DB migration**: Add `session_rules` table (id, session_id, rule_name, rule_instruction, created_at) for persisting extracted rules. Update both PostgreSQL migration and SQLite schema.

2. **Session rules storage module**: CRUD operations for session_rules table — save rules, load rules by session_id, check if rules exist for a session.

3. **ParallelRulesPolicy**: A SimplePolicy subclass that:
   - Accepts a list of rules (name + instruction) in config or via request state
   - For each text content block, runs all rules in parallel via LLM calls (litellm.acompletion)
   - If 0 rules modify content → passthrough
   - If 1 rule modifies → use that version
   - If 2+ rules modify → refinement LLM call that merges all versions
   - Buffers streaming content (inherits SimplePolicy behavior)

4. **ClaudeMdRulesPolicy**: An AnthropicExecutionInterface implementation that:
   - On each request, checks if session_id exists and if rules are already stored
   - If no rules: extracts CLAUDE.md content from system prompt, calls LLM to identify rules, stores in DB
   - If rules exist: loads from DB, sets them as request state, delegates to ParallelRulesPolicy
   - Handles missing session_id gracefully (passthrough)

5. **DB pool access for policies**: Add optional `db_pool` to PolicyContext so policies can access the database. The pipeline already creates PolicyContext with the emitter; adding db_pool follows the same pattern.

## Test Strategy

- Unit tests: Rule application logic, refinement merging, CLAUDE.md extraction, DB storage CRUD
- Integration tests: Full policy pipeline with mock LLM responses

## Acceptance Criteria

- [ ] ParallelRulesPolicy applies rules in parallel and merges when needed
- [ ] ClaudeMdRulesPolicy extracts rules from first turn and persists
- [ ] ClaudeMdRulesPolicy loads persisted rules on subsequent turns
- [ ] DB migration works for both PostgreSQL and SQLite
- [ ] Missing session_id degrades gracefully
- [ ] dev_checks passes

## Tracking

- Trello: none
- Branch: main (worktree)
- PR: TBD
