# Objective

## Dynamic Policy Creation System

Allow users to create custom policies from natural language prompts via an LLM, store them in the database, and activate them at runtime.

### Acceptance Criteria

- [ ] Admin API endpoints for generating, saving, listing, activating, and deleting dynamic policies
- [ ] Dynamic policy loader that compiles Python source and instantiates policy classes at runtime
- [ ] LLM-powered policy generation using claude-sonnet-4-5 with comprehensive system prompt
- [ ] Policy code validation (syntax, safety, protocol compliance) before saving
- [ ] DB storage for policy source code, config, metadata, and generation prompt
- [ ] Web UI (Policy Workshop) for generating, editing, saving, browsing, and activating policies
- [ ] Unit tests for loader, generator, and API endpoints
