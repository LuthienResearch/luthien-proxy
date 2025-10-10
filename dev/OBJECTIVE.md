# Objective: COMPLETED

ConversationLoggingPolicy has been killed and its functionality moved to core infra and utilities.

## What Was Done

1. **Deleted ConversationLoggingPolicy** - The policy class that was mixing logging concerns with policy logic
2. **Created utils/conversation_parsing.py** - Shared parsing utilities for call IDs, trace IDs, tool calls, messages
3. **Created utils/streaming_aggregation.py** - StreamChunkAggregator for policies that need to aggregate chunks
4. **Updated all dependent policies**:
   - ToolCallBufferPolicy now inherits from LuthienPolicy and uses StreamChunkAggregator
   - SQLProtectionPolicy uses conversation_parsing utilities
   - LLMJudgeToolPolicy uses conversation_parsing utilities
5. **Deleted tests** - Removed test_conversation_logging_policy.py as it tested deleted functionality
6. **Fixed remaining tests** - Updated test_sql_protection.py to use context.aggregator.tool_calls

## Core Infra Now Handles

- Logging original hook payloads to debug_logs
- Building conversation events with original vs final payloads
- Storing events in database
- Publishing events to Redis for live streaming
- All of this happens in hooks_routes.py automatically

## Policies No Longer Handle

- Conversation event logging
- Emitting structured JSON logs (this was redundant)
- They still can aggregate chunks if needed (via StreamChunkAggregator utility)
- They still transform payloads (that's their job)

All tests pass, dev checks pass.
