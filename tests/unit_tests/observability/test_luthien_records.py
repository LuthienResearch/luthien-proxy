"""Tests for LuthienRecord."""

import json

from luthien_proxy.observability.context import (
    NoOpObservabilityContext,
    PipelineRecord,
)


def test_pipeline_record_basic():
    """Test PipelineRecord initialization and attributes."""
    rec = PipelineRecord(
        transaction_id="test-123",
        pipeline_stage="client_request",
        payload=json.dumps({"model": "gpt-4", "messages": []}),
    )

    assert rec.transaction_id == "test-123"
    assert rec.pipeline_stage == "client_request"
    assert json.loads(rec.payload) == {"model": "gpt-4", "messages": []}


def test_pipeline_record_type():
    """Test PipelineRecord has correct record_type."""
    assert PipelineRecord.record_type == "pipeline"


def test_pipeline_record_vars():
    """Test PipelineRecord serialization via vars()."""
    rec = PipelineRecord(
        transaction_id="test-456",
        pipeline_stage="backend_response",
        payload="test payload string",
    )

    result = vars(rec)

    assert result["transaction_id"] == "test-456"
    assert result["pipeline_stage"] == "backend_response"
    assert result["payload"] == "test payload string"


def test_observability_context_record_nonblocking():
    """Test ObservabilityContext.record() method."""
    ctx = NoOpObservabilityContext()

    # Should not raise (NoOp context does nothing)
    ctx.record(
        PipelineRecord(
            transaction_id="test-789",
            pipeline_stage="test_payload",
            payload="test data",
        )
    )
