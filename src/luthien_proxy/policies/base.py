"""Abstract base for Luthien Control Policies with streaming support."""

from __future__ import annotations

import logging
import time
from abc import ABC
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AsyncIterator, Awaitable, Callable, Mapping, Optional

from litellm.integrations.custom_logger import CustomLogger

from luthien_proxy.types import JSONObject, JSONValue

if TYPE_CHECKING:
    from luthien_proxy.utils import db
    from luthien_proxy.utils.redis_client import RedisClient

logger = logging.getLogger(__name__)


@dataclass
class StreamPolicyContext:
    """Base state object for streaming policies.

    Attributes:
        stream_id: Identifier provided by LiteLLM for this stream.
        original_request: Request payload associated with the stream.
        chunk_count: Number of chunks processed so far.
        start_time: Timestamp captured when the stream context is created.
    """

    stream_id: str
    original_request: dict[str, object]
    chunk_count: int = 0
    start_time: float = field(default_factory=time.time)


DebugLogWriter = Callable[[str, JSONObject], Awaitable[None]]


class LuthienPolicy(ABC, CustomLogger):
    """Mirror of LiteLLM hook API, executed server-side in the control plane."""

    def __init__(self, options: Mapping[str, JSONValue] | None = None) -> None:
        """Initialise policy base class, storing raw configuration options."""
        super().__init__()
        self._options: Optional[JSONObject] = dict(options) if options is not None else None
        self._debug_log_writer: Optional[DebugLogWriter] = None
        self._database_pool: "db.DatabasePool | None" = None
        self._redis_client: "RedisClient | None" = None

    # ------------------------------------------------------------------
    # Streaming API
    # ------------------------------------------------------------------
    def create_stream_context(self, stream_id: str, request_data: dict) -> StreamPolicyContext:
        """Create per-stream state when a stream starts."""
        return StreamPolicyContext(stream_id=stream_id, original_request=request_data)

    async def generate_response_stream(
        self,
        context: StreamPolicyContext,
        incoming_stream: AsyncIterator[dict],
    ) -> AsyncIterator[dict]:
        """Default implementation that forwards chunks unchanged."""
        async for chunk in incoming_stream:
            context.chunk_count += 1
            yield chunk

    # ------------------------------------------------------------------
    # Shared resource injection
    # ------------------------------------------------------------------
    def set_debug_log_writer(self, writer: Optional[DebugLogWriter]) -> None:
        """Configure the async writer used for persisting debug records."""
        self._debug_log_writer = writer

    def set_database_pool(self, pool: "db.DatabasePool | None") -> None:
        """Inject the database pool used for policy events."""
        self._database_pool = pool

    def set_redis_client(self, client: "RedisClient | None") -> None:
        """Inject the Redis client used for activity publishing."""
        self._redis_client = client

    # ------------------------------------------------------------------
    # Debug logging helpers
    # ------------------------------------------------------------------
    async def _record_debug_event(self, debug_type: str, payload: JSONObject) -> None:
        """Persist *payload* best-effort via the configured debug writer."""
        if self._debug_log_writer is None:
            return
        try:
            await self._debug_log_writer(debug_type, payload)
        except Exception:
            logger.warning("debug log writer failed for %s", debug_type)


__all__ = [
    "LuthienPolicy",
    "StreamPolicyContext",
    "DebugLogWriter",
]
