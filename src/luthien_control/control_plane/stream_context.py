from __future__ import annotations

from typing import Optional

import redis.asyncio as redis  # type: ignore

"""
Minimal per-call streaming context store.

Why: Policies evaluating streaming chunks often need the accumulated text so far
and a stable chunk index. We keep this in the control plane so it's centralized
and consistent across proxy processes.
"""


class StreamContextStore:
    def __init__(
        self,
        redis_client: "redis.Redis",
        ttl_seconds: int = 3600,
    ) -> None:
        if redis_client is None:
            # Fail fast: Redis is required for stream context
            raise RuntimeError("StreamContextStore requires a Redis client")
        self._redis = redis_client
        self._ttl = int(ttl_seconds)

    def _text_key(self, call_id: str) -> str:
        return f"stream:{call_id}:text"

    def _idx_key(self, call_id: str) -> str:
        return f"stream:{call_id}:index"

    async def get_accumulated(self, call_id: Optional[str]) -> str:
        if not call_id:
            return ""
        val = await self._redis.get(self._text_key(call_id))
        return val.decode() if isinstance(val, (bytes, bytearray)) else str(val or "")

    async def get_index(self, call_id: Optional[str]) -> int:
        if not call_id:
            return 0
        val = await self._redis.get(self._idx_key(call_id))
        try:
            return int(val) if val is not None else 0
        except Exception:
            return 0

    async def append_delta(self, call_id: Optional[str], text: str) -> None:
        if not call_id or not text:
            return
        # Redis: append text and incr index, maintain TTLs
        await self._redis.append(self._text_key(call_id), text)
        await self._redis.incr(self._idx_key(call_id))
        await self._redis.expire(self._text_key(call_id), self._ttl)
        await self._redis.expire(self._idx_key(call_id), self._ttl)

    async def clear(self, call_id: Optional[str]) -> None:
        if not call_id:
            return
        await self._redis.delete(self._text_key(call_id))
        await self._redis.delete(self._idx_key(call_id))
