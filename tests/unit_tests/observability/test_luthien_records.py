"""Tests for LuthienRecord."""

from luthien_proxy.observability.context import (
    LuthienPayloadRecord,
    NoOpObservabilityContext,
)


def test_luthien_payload_record_to_dict():
    """Test LuthienPayloadRecord serialization."""
    rec = LuthienPayloadRecord(
        stage="client.request",
        data={
            "payload": {"model": "gpt-4", "messages": []},
            "format": "openai",
        },
    )

    result = rec.to_dict()

    assert result["stage"] == "client.request"
    assert result["payload"] == {"model": "gpt-4", "messages": []}
    assert result["format"] == "openai"


def test_luthien_payload_record_type():
    """Test LuthienPayloadRecord has correct record_type."""
    assert LuthienPayloadRecord.record_type == "payload"


def test_observability_context_record_nonblocking():
    """Test ObservabilityContext.record() method."""
    ctx = NoOpObservabilityContext()

    # Should not raise (NoOp context does nothing)
    ctx.record(
        LuthienPayloadRecord(
            stage="test.stage",
            data={"test_data": "value"},
        )
    )


async def test_observability_context_record_blocking():
    """Test ObservabilityContext.record_blocking() method."""
    ctx = NoOpObservabilityContext()

    # Should not raise (NoOp context does nothing)
    await ctx.record_blocking(
        LuthienPayloadRecord(
            stage="test.stage",
            data={"test_data": "value"},
        )
    )
