# Current Objective

**Conversation History Viewer** - Build a slick UI for browsing and exporting conversation histories.

## Features

1. **Session list view** (`/history`) - Recent sessions with metadata (turn count, policy interventions, timestamp)
2. **Session detail view** (`/history/{session_id}`) - Full conversation as styled chat transcript
3. **Message type styling** - Visual distinction for:
   - User messages
   - Assistant responses
   - Tool calls
   - Tool call responses
   - Other/system
4. **Policy annotations** - Inline highlights where policies modified or blocked content
5. **Markdown export** (`/history/{session_id}/export`) - Download conversation as `.md` file

## Acceptance Criteria

- [ ] Can browse list of recent sessions at `/history`
- [ ] Can view full conversation flow for a session with styled message types
- [ ] Tool calls and responses are visually distinct and readable
- [ ] Policy interventions are annotated inline
- [ ] Can export any session to markdown
- [ ] UI is clean and pleasant to use
