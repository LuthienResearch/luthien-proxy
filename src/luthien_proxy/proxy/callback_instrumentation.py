"""ABOUTME: Instrumentation layer for LiteLLM callback invocations.

ABOUTME: Provides logging and inspection of callback inputs, outputs, and data flow.
"""

import functools
import json
import time
from typing import Any, Callable, TypeVar

# Use LiteLLM's proxy logger so our logs actually appear in proxy context
from litellm._logging import verbose_proxy_logger as logger

T = TypeVar("T")


class CallbackInvocation:
    """Record of a single callback invocation with inputs and outputs."""

    def __init__(self, callback_name: str, args: tuple, kwargs: dict):  # noqa: D107
        self.callback_name = callback_name
        self.args = args
        self.kwargs = kwargs
        self.start_time = time.time()
        self.end_time: float | None = None
        self.return_value: Any = None
        self.yielded_chunks: list[Any] = []
        self.exception: Exception | None = None

    def finish(self, return_value: Any = None, exception: Exception | None = None) -> None:
        """Mark the invocation as complete."""
        self.end_time = time.time()
        self.return_value = return_value
        self.exception = exception

    def add_yielded_chunk(self, chunk: Any) -> None:
        """Record a chunk yielded by an async generator callback."""
        self.yielded_chunks.append(chunk)

    @property
    def duration(self) -> float | None:
        """Duration in seconds, or None if not finished."""
        if self.end_time is None:
            return None
        return self.end_time - self.start_time

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dict."""
        return {
            "callback_name": self.callback_name,
            "args_types": [type(arg).__name__ for arg in self.args],
            "kwargs_keys": list(self.kwargs.keys()),
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "return_value_type": type(self.return_value).__name__ if self.return_value is not None else None,
            "yielded_chunk_count": len(self.yielded_chunks),
            "exception": str(self.exception) if self.exception else None,
        }


class CallbackTracer:
    """Global singleton for tracking callback invocations."""

    def __init__(self):  # noqa: D107
        self._invocations: list[CallbackInvocation] = []
        self._enabled = True

    def enable(self) -> None:
        """Enable callback tracing."""
        self._enabled = True

    def disable(self) -> None:
        """Disable callback tracing."""
        self._enabled = False

    def clear(self) -> None:
        """Clear all recorded invocations."""
        self._invocations.clear()

    def record(self, invocation: CallbackInvocation) -> None:
        """Record a callback invocation."""
        if self._enabled:
            self._invocations.append(invocation)

    def get_invocations(self, callback_name: str | None = None) -> list[CallbackInvocation]:
        """Get all recorded invocations, optionally filtered by callback name."""
        if callback_name is None:
            return list(self._invocations)
        return [inv for inv in self._invocations if inv.callback_name == callback_name]

    def get_invocation_summary(self) -> dict[str, int]:
        """Get a summary of invocation counts by callback name."""
        summary: dict[str, int] = {}
        for inv in self._invocations:
            summary[inv.callback_name] = summary.get(inv.callback_name, 0) + 1
        return summary


# Global tracer instance
_tracer = CallbackTracer()


def get_tracer() -> CallbackTracer:
    """Get the global callback tracer."""
    return _tracer


def _safe_preview(obj: Any, max_length: int = 200) -> str:
    """Generate a safe string preview of an object."""
    try:
        if obj is None:
            return "None"
        if isinstance(obj, (str, int, float, bool)):
            s = str(obj)
            return s if len(s) <= max_length else f"{s[:max_length]}..."
        if isinstance(obj, dict):
            s = json.dumps(obj, default=str, ensure_ascii=False)
            return s if len(s) <= max_length else f"{s[:max_length]}..."
        return f"{type(obj).__name__}(...)"
    except Exception:
        return f"{type(obj).__name__}(unprintable)"


def instrument_callback(func: Callable[..., T]) -> Callable[..., T]:
    """Decorator to instrument callback functions with logging and tracing.

    For async generators (streaming callbacks), this will log each yielded chunk.
    For regular async functions, this will log input and output.
    """
    callback_name = func.__name__

    @functools.wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        # Log invocation
        logger.info(
            "Callback invoked: %s, args_types=[%s], kwargs_keys=%s",
            callback_name,
            ", ".join(type(arg).__name__ for arg in args),
            list(kwargs.keys()),
        )

        # Preview key arguments
        if kwargs.get("data"):
            logger.debug("  data preview: %s", _safe_preview(kwargs["data"]))
        if kwargs.get("response"):
            logger.debug("  response type: %s", type(kwargs["response"]).__name__)

        invocation = CallbackInvocation(callback_name, args, kwargs)
        get_tracer().record(invocation)

        try:
            result = func(*args, **kwargs)

            # Handle async generators (streaming callbacks)
            if hasattr(result, "__aiter__"):

                async def instrumented_generator():
                    chunk_count = 0
                    try:
                        async for chunk in result:  # type: ignore[misc]
                            chunk_count += 1
                            invocation.add_yielded_chunk(chunk)

                            if chunk_count <= 3 or chunk_count % 10 == 0:
                                logger.debug(
                                    "  %s yielded chunk #%d: %s",
                                    callback_name,
                                    chunk_count,
                                    _safe_preview(chunk, max_length=100),
                                )
                            yield chunk

                        invocation.finish()
                        logger.info(
                            "Callback completed: %s, yielded %d chunks in %.3fs",
                            callback_name,
                            chunk_count,
                            invocation.duration or 0,
                        )
                    except Exception as exc:
                        invocation.finish(exception=exc)
                        logger.error("Callback failed: %s, error: %s", callback_name, exc)
                        raise

                return instrumented_generator()

            # Handle regular async functions
            else:
                result = await result  # type: ignore[misc]
                invocation.finish(return_value=result)

                logger.info(
                    "Callback completed: %s, return_type=%s, duration=%.3fs",
                    callback_name,
                    type(result).__name__,
                    invocation.duration or 0,
                )

                if result is not None:
                    logger.debug("  return value preview: %s", _safe_preview(result))

                return result

        except Exception as exc:
            invocation.finish(exception=exc)
            logger.error("Callback failed: %s, error: %s", callback_name, exc)
            raise

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        logger.info(
            "Callback invoked (sync): %s, args_types=[%s], kwargs_keys=%s",
            callback_name,
            ", ".join(type(arg).__name__ for arg in args),
            list(kwargs.keys()),
        )

        invocation = CallbackInvocation(callback_name, args, kwargs)
        get_tracer().record(invocation)

        try:
            result = func(*args, **kwargs)
            invocation.finish(return_value=result)

            logger.info(
                "Callback completed (sync): %s, return_type=%s, duration=%.3fs",
                callback_name,
                type(result).__name__,
                invocation.duration or 0,
            )

            return result

        except Exception as exc:
            invocation.finish(exception=exc)
            logger.error("Callback failed (sync): %s, error: %s", callback_name, exc)
            raise

    # Return appropriate wrapper based on function type
    import inspect

    if inspect.iscoroutinefunction(func):
        return async_wrapper  # type: ignore
    else:
        return sync_wrapper  # type: ignore


__all__ = ["instrument_callback", "get_tracer", "CallbackInvocation", "CallbackTracer"]
