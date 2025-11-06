# ABOUTME: Unit tests for TransactionRecorder.wrap() method
# ABOUTME: Tests wrapping of pipeline components for chunk recording

"""Unit tests for TransactionRecorder.wrap() functionality."""

import asyncio
from unittest.mock import Mock

import pytest
from litellm.types.utils import ModelResponse

from luthien_proxy.v2.observability.context import NoOpObservabilityContext
from luthien_proxy.v2.observability.transaction_recorder import (
    DefaultTransactionRecorder,
)
from luthien_proxy.v2.policies import PolicyContext


class MockComponent:
    """Mock pipeline component with process() method."""

    def __init__(self, output_chunks):
        """Initialize with chunks to output."""
        self.output_chunks = output_chunks
        self.process_called = False
        self.received_input = None

    async def process(self, input_stream, output_queue, policy, policy_ctx, obs_ctx):
        """Mock process that reads input and writes output."""
        self.process_called = True

        # Consume input stream
        consumed = []
        async for chunk in input_stream:
            consumed.append(chunk)
        self.received_input = consumed

        # Write output chunks
        for chunk in self.output_chunks:
            await output_queue.put(chunk)

        # Signal completion
        await output_queue.put(None)


async def async_stream(items):
    """Helper to create async stream from list."""
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_wrap_returns_wrapper_with_same_interface():
    """Test that wrap() returns an object with the same process() method."""
    recorder = DefaultTransactionRecorder(observability=NoOpObservabilityContext(transaction_id="test", span=Mock()))

    component = MockComponent([])
    wrapped = recorder.wrap(component)

    # Wrapped object should have process method
    assert hasattr(wrapped, "process")
    assert callable(wrapped.process)


@pytest.mark.asyncio
async def test_wrap_records_ingress_chunks():
    """Test that wrapped component records ingress (input) chunks."""
    recorder = DefaultTransactionRecorder(observability=NoOpObservabilityContext(transaction_id="test", span=Mock()))

    # Create mock chunks
    chunk1 = Mock(spec=ModelResponse)
    chunk2 = Mock(spec=ModelResponse)

    component = MockComponent(output_chunks=[])
    wrapped = recorder.wrap(component)

    # Create queues
    input_stream = async_stream([chunk1, chunk2])
    output_queue = asyncio.Queue()

    policy_ctx = PolicyContext(transaction_id="test", request=Mock())
    obs_ctx = NoOpObservabilityContext(transaction_id="test", span=Mock())

    # Process
    await wrapped.process(input_stream, output_queue, policy=Mock(), policy_ctx=policy_ctx, obs_ctx=obs_ctx)

    # Verify ingress chunks were recorded
    assert len(recorder._ingress_chunks) == 2
    assert recorder._ingress_chunks[0] == chunk1
    assert recorder._ingress_chunks[1] == chunk2


@pytest.mark.asyncio
async def test_wrap_records_egress_chunks():
    """Test that wrapped component records egress (output) chunks."""
    recorder = DefaultTransactionRecorder(observability=NoOpObservabilityContext(transaction_id="test", span=Mock()))

    # Create mock output chunks
    out_chunk1 = Mock(spec=ModelResponse)
    out_chunk2 = Mock(spec=ModelResponse)

    component = MockComponent(output_chunks=[out_chunk1, out_chunk2])
    wrapped = recorder.wrap(component)

    # Create queues
    input_stream = async_stream([])
    output_queue = asyncio.Queue()

    policy_ctx = PolicyContext(transaction_id="test", request=Mock())
    obs_ctx = NoOpObservabilityContext(transaction_id="test", span=Mock())

    # Process
    await wrapped.process(input_stream, output_queue, policy=Mock(), policy_ctx=policy_ctx, obs_ctx=obs_ctx)

    # Verify egress chunks were recorded
    assert len(recorder._egress_chunks) == 2
    assert recorder._egress_chunks[0] == out_chunk1
    assert recorder._egress_chunks[1] == out_chunk2


@pytest.mark.asyncio
async def test_wrap_passes_through_chunks():
    """Test that wrapped component passes chunks through correctly."""
    recorder = DefaultTransactionRecorder(observability=NoOpObservabilityContext(transaction_id="test", span=Mock()))

    # Create chunks
    in_chunk = Mock(spec=ModelResponse)
    out_chunk1 = Mock(spec=ModelResponse)
    out_chunk2 = Mock(spec=ModelResponse)

    component = MockComponent(output_chunks=[out_chunk1, out_chunk2])
    wrapped = recorder.wrap(component)

    # Create queues
    input_stream = async_stream([in_chunk])
    output_queue = asyncio.Queue()

    policy_ctx = PolicyContext(transaction_id="test", request=Mock())
    obs_ctx = NoOpObservabilityContext(transaction_id="test", span=Mock())

    # Process
    await wrapped.process(input_stream, output_queue, policy=Mock(), policy_ctx=policy_ctx, obs_ctx=obs_ctx)

    # Verify component received input
    assert component.received_input == [in_chunk]

    # Verify output queue has chunks
    received_output = []
    while not output_queue.empty():
        chunk = await output_queue.get()
        if chunk is not None:
            received_output.append(chunk)

    assert received_output == [out_chunk1, out_chunk2]


@pytest.mark.asyncio
async def test_wrap_handles_none_sentinel():
    """Test that wrap handles None sentinel in output correctly."""
    recorder = DefaultTransactionRecorder(observability=NoOpObservabilityContext(transaction_id="test", span=Mock()))

    out_chunk = Mock(spec=ModelResponse)
    component = MockComponent(output_chunks=[out_chunk])
    wrapped = recorder.wrap(component)

    input_stream = async_stream([])
    output_queue = asyncio.Queue()

    policy_ctx = PolicyContext(transaction_id="test", request=Mock())
    obs_ctx = NoOpObservabilityContext(transaction_id="test", span=Mock())

    await wrapped.process(input_stream, output_queue, policy=Mock(), policy_ctx=policy_ctx, obs_ctx=obs_ctx)

    # Verify egress chunks does NOT include None sentinel
    assert len(recorder._egress_chunks) == 1
    assert recorder._egress_chunks[0] == out_chunk
    assert None not in recorder._egress_chunks
