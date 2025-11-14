"""Example usage of LuthienPayloadRecord for observability.

This shows how to use the structured record system to log payloads
at different stages of the pipeline.
"""

from luthien_proxy.observability.context import (
    DefaultObservabilityContext,
    LuthienPayloadRecord,
)


async def example_request_flow(obs_ctx: DefaultObservabilityContext):
    """Example showing how to log payloads through the request lifecycle."""
    # 1. Log raw client request
    client_request = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello!"}],
        "max_tokens": 100,
    }

    obs_ctx.record(
        LuthienPayloadRecord(
            stage="client.request",
            data={
                "payload": client_request,
                "format": "anthropic",
                "endpoint": "/v1/messages",
            },
        )
    )

    # 2. Log after format conversion
    openai_request = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello!"}],
        "max_tokens": 100,
    }

    obs_ctx.record(
        LuthienPayloadRecord(
            stage="format.converted",
            data={
                "from_format": "anthropic",
                "to_format": "openai",
                "payload": openai_request,
            },
        )
    )

    # 3. Log before policy
    obs_ctx.record(
        LuthienPayloadRecord(
            stage="policy.request.before",
            data={
                "policy": "SimpleJudgePolicy",
                "payload": openai_request,
            },
        )
    )

    # 4. Log after policy modification
    modified_request = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ],
        "max_tokens": 100,
    }

    obs_ctx.record(
        LuthienPayloadRecord(
            stage="policy.request.after",
            data={
                "policy": "SimpleJudgePolicy",
                "payload": modified_request,
                "modifications": {
                    "added_system_message": True,
                },
            },
        )
    )

    # 5. Log backend request
    obs_ctx.record(
        LuthienPayloadRecord(
            stage="backend.request",
            data={
                "payload": modified_request,
                "backend": "anthropic",
            },
        )
    )

    # 6. Log backend response
    backend_response = {
        "id": "msg_123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Hi there!"}],
    }

    obs_ctx.record(
        LuthienPayloadRecord(
            stage="backend.response",
            data={
                "payload": backend_response,
                "backend": "anthropic",
            },
        )
    )

    # 7. Log final client response
    obs_ctx.record(
        LuthienPayloadRecord(
            stage="client.response",
            data={
                "payload": backend_response,
                "format": "anthropic",
            },
        )
    )


# In production, use this pattern in your gateway routes:
"""
@router.post("/v1/chat/completions")
async def chat_completions(request: Request, ...):
    obs_ctx = DefaultObservabilityContext(...)

    # Log incoming request
    obs_ctx.record(LuthienPayloadRecord(
        stage="client.request",
        data={"payload": body, "format": "openai"}
    ))

    # ... process through pipeline ...

    # Log at each transformation stage using obs_ctx.record()
"""

# Query in Loki:
"""
# All records for a transaction:
{service_name="luthien-proxy"} | json | transaction_id="abc-123"

# Just policy modifications:
{service_name="luthien-proxy"} | json | stage=~"policy.*"

# Compare before/after:
{service_name="luthien-proxy"} | json
  | transaction_id="abc-123"
  | stage=~"policy.request.(before|after)"
"""
