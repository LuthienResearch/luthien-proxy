"""ABOUTME: E2E test helpers for asserting callback invocation and data flow
ABOUTME: Provides utilities to inspect callback inputs, outputs, and verify they reach the client
"""

from typing import Any

from luthien_proxy.proxy.callback_instrumentation import CallbackInvocation, get_tracer


def get_callback_invocations(callback_name: str) -> list[CallbackInvocation]:
    """Get all recorded invocations for a specific callback."""
    return get_tracer().get_invocations(callback_name)


def assert_callback_was_called(callback_name: str, times: int = 1) -> None:
    """Assert that a callback was invoked the expected number of times."""
    invocations = get_callback_invocations(callback_name)
    actual_count = len(invocations)
    assert actual_count == times, (
        f"Expected {callback_name} to be called {times} time(s), but it was called {actual_count} time(s)"
    )


def assert_callback_order(expected_order: list[str]) -> None:
    """Assert that callbacks were invoked in a specific order."""
    all_invocations = get_tracer().get_invocations()
    actual_order = [inv.callback_name for inv in all_invocations]

    # Filter to only the callbacks we care about
    filtered_order = [name for name in actual_order if name in expected_order]

    assert filtered_order == expected_order, f"Expected callback order {expected_order}, but got {filtered_order}"


def assert_callback_received_arg(
    callback_name: str,
    arg_name: str,
    invocation_index: int = 0,
) -> Any:
    """Assert that a callback received a specific keyword argument and return its value."""
    invocations = get_callback_invocations(callback_name)
    assert invocations, f"No invocations found for {callback_name}"
    assert invocation_index < len(invocations), (
        f"Invocation index {invocation_index} out of range (only {len(invocations)} invocations)"
    )

    invocation = invocations[invocation_index]
    assert arg_name in invocation.kwargs, (
        f"Callback {callback_name} did not receive argument '{arg_name}'. "
        f"Available kwargs: {list(invocation.kwargs.keys())}"
    )

    return invocation.kwargs[arg_name]


def assert_streaming_callback_yielded_chunks(
    callback_name: str,
    min_chunks: int | None = None,
    max_chunks: int | None = None,
    invocation_index: int = 0,
) -> list[Any]:
    """Assert that a streaming callback yielded the expected number of chunks."""
    invocations = get_callback_invocations(callback_name)
    assert invocations, f"No invocations found for {callback_name}"
    assert invocation_index < len(invocations), (
        f"Invocation index {invocation_index} out of range (only {len(invocations)} invocations)"
    )

    invocation = invocations[invocation_index]
    actual_count = len(invocation.yielded_chunks)

    if min_chunks is not None:
        assert actual_count >= min_chunks, (
            f"Expected {callback_name} to yield at least {min_chunks} chunks, but it yielded {actual_count}"
        )

    if max_chunks is not None:
        assert actual_count <= max_chunks, (
            f"Expected {callback_name} to yield at most {max_chunks} chunks, but it yielded {actual_count}"
        )

    return invocation.yielded_chunks


def assert_callback_returned_value(
    callback_name: str,
    invocation_index: int = 0,
) -> Any:
    """Get the return value from a callback invocation."""
    invocations = get_callback_invocations(callback_name)
    assert invocations, f"No invocations found for {callback_name}"
    assert invocation_index < len(invocations), (
        f"Invocation index {invocation_index} out of range (only {len(invocations)} invocations)"
    )

    return invocations[invocation_index].return_value


def assert_callback_completed_successfully(
    callback_name: str,
    invocation_index: int = 0,
) -> None:
    """Assert that a callback completed without raising an exception."""
    invocations = get_callback_invocations(callback_name)
    assert invocations, f"No invocations found for {callback_name}"
    assert invocation_index < len(invocations), (
        f"Invocation index {invocation_index} out of range (only {len(invocations)} invocations)"
    )

    invocation = invocations[invocation_index]
    assert invocation.exception is None, f"Callback {callback_name} raised an exception: {invocation.exception}"
    assert invocation.end_time is not None, f"Callback {callback_name} did not complete"


def clear_callback_trace() -> None:
    """Clear all recorded callback invocations."""
    get_tracer().clear()


def get_callback_summary() -> dict[str, int]:
    """Get a summary of all callback invocations."""
    return get_tracer().get_invocation_summary()


__all__ = [
    "get_callback_invocations",
    "assert_callback_was_called",
    "assert_callback_order",
    "assert_callback_received_arg",
    "assert_streaming_callback_yielded_chunks",
    "assert_callback_returned_value",
    "assert_callback_completed_successfully",
    "clear_callback_trace",
    "get_callback_summary",
]
