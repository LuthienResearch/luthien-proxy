"""Example usage of PipelineRecord for observability.

This shows how to use the structured record system to log payloads
at different stages of the pipeline.
"""

import json

from luthien_proxy.observability.context import (
    DefaultObservabilityContext,
    PipelineRecord,
)


async def example_request_flow(obs_ctx: DefaultObservabilityContext, transaction_id: str):
    """Example showing how to log payloads through the request lifecycle."""
    # 1. Log raw client request
    client_request = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello!"}],
        "max_tokens": 100,
    }

    obs_ctx.record(
        PipelineRecord(
            transaction_id=transaction_id,
            pipeline_stage="client_request",
            payload=json.dumps(client_request),
        )
    )

    # 2. Log after format conversion
    openai_request = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello!"}],
        "max_tokens": 100,
    }

    obs_ctx.record(
        PipelineRecord(
            transaction_id=transaction_id,
            pipeline_stage="format_conversion",
            payload=json.dumps(openai_request),
        )
    )

    # 3. Log backend request (after policy modification)
    modified_request = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ],
        "max_tokens": 100,
    }

    obs_ctx.record(
        PipelineRecord(
            transaction_id=transaction_id,
            pipeline_stage="backend_request",
            payload=json.dumps(modified_request),
        )
    )

    # 4. Log backend response
    backend_response = {
        "id": "msg_123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hi there!"}],
    }

    obs_ctx.record(
        PipelineRecord(
            transaction_id=transaction_id,
            pipeline_stage="backend_response",
            payload=json.dumps(backend_response),
        )
    )

    # 5. Log final client response
    obs_ctx.record(
        PipelineRecord(
            transaction_id=transaction_id,
            pipeline_stage="client_response",
            payload=json.dumps(backend_response),
        )
    )


# In production, use this pattern in your gateway routes:
"""
@router.post("/v1/chat/completions")
async def chat_completions(request: Request, ...):
    call_id = str(uuid.uuid4())
    obs_ctx = DefaultObservabilityContext(transaction_id=call_id, ...)

    # Log incoming request
    obs_ctx.record(PipelineRecord(
        transaction_id=call_id,
        pipeline_stage="client_request",
        payload=json.dumps(body)
    ))

    # ... process through pipeline ...

    # Log at each transformation stage using obs_ctx.record()
"""

# Query in Grafana/Loki:
"""
# All pipeline records:
{app="luthien-gateway", record_type="pipeline"}

# Just client requests:
{app="luthien-gateway", record_type="pipeline", pipeline_stage="client_request"}

# Just backend responses:
{app="luthien-gateway", record_type="pipeline", pipeline_stage="backend_response"}

# All records for a specific transaction (use line filter for transaction_id):
{app="luthien-gateway", record_type="pipeline"} | json | transaction_id="abc-123"

# Compare before/after for a transaction:
{app="luthien-gateway", record_type="pipeline", pipeline_stage=~"client_request|backend_request"}
  | json | transaction_id="abc-123"
"""
