# ABOUTME: Logging policy that records all requests and responses to the database
# ABOUTME: MVP implementation for tracking API calls and building audit trail

from __future__ import annotations

import json
import os
import uuid
from typing import Any, AsyncGenerator, Dict, Optional, List

import asyncpg
import yaml
from beartype import beartype

from luthien_control.policies.base import LuthienPolicy


class LoggingPolicy(LuthienPolicy):
    """Policy that logs all requests and responses to the database."""

    def __init__(self, options: Optional[Dict[str, Any]] = None):
        self.db_url = os.getenv(
            "DATABASE_URL", "postgresql://luthien:luthien@postgres:5432/luthien"
        )
        self.pool = None
        self.config = self._load_config(options)

    async def _ensure_pool(self):
        """Create connection pool if it doesn't exist."""
        if self.pool is None:
            self.pool = await asyncpg.create_pool(self.db_url, min_size=1, max_size=10)

    async def _debug_log(self, debug_type: str, payload: Dict[str, Any]):
        """Write an arbitrary JSON payload to debug_logs."""
        await self._ensure_pool()
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO debug_logs (debug_type_identifier, jsonblob)
                    VALUES ($1, $2::jsonb)
                    """,
                    debug_type,
                    json.dumps(self._redact(payload)),
                )
            except Exception as e:
                print(f"Error writing debug_log({debug_type}): {e}")

    async def _log_request(
        self,
        stage: str,
        data: Dict[str, Any],
        response: Optional[Dict[str, Any]] = None,
        policy_action: str = "allow",
        policy_metadata: Optional[Dict[str, Any]] = None,
        call_type: Optional[str] = None,
    ):
        """Log a request/response to the database."""
        await self._ensure_pool()

        episode_id = data.get("metadata", {}).get("episode_id")
        step_id = data.get("metadata", {}).get("step_id")
        call_type = call_type or data.get("call_type")
        user_metadata = data.get("metadata", {})

        # Convert UUID strings to UUID objects if present
        if episode_id and isinstance(episode_id, str):
            episode_id = uuid.UUID(episode_id)
        if step_id and isinstance(step_id, str):
            step_id = uuid.UUID(step_id)

        async with self.pool.acquire() as conn:
            # Redact sensitive fields before storing
            redacted_request = self._redact(data)
            redacted_response = self._redact(response) if response else None

            await conn.execute(
                """
                INSERT INTO request_logs (
                    episode_id, step_id, call_type, stage,
                    request, response, user_metadata,
                    policy_action, policy_metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                episode_id,
                step_id,
                call_type,
                stage,
                json.dumps(redacted_request),
                json.dumps(redacted_response) if redacted_response else None,
                json.dumps(user_metadata),
                policy_action,
                json.dumps(policy_metadata or {}),
            )

    @beartype
    async def async_pre_call_hook(
        self,
        user_api_key_dict: Optional[Dict[str, Any]],
        cache: Optional[Dict[str, Any]],
        data: Dict[str, Any],
        call_type: Optional[str],
    ) -> Optional[object]:
        """Log pre-call request and pass through."""
        try:
            await self._log_request(
                "pre", data, policy_action="allow", call_type=call_type
            )
            # Also record raw kwargs in debug logs for investigation
            # attach litellm_call_id if present on common paths
            call_id = self._extract_call_id_from_kwargs(data)
            dbg = {"call_type": call_type, "kwargs": data}
            if call_id:
                dbg["litellm_call_id"] = call_id
            await self._debug_log("kwargs_pre", dbg)
        except Exception as e:
            print(f"Error logging pre-call request: {e}")

        # Pass through - no modification
        return None

    @beartype
    async def async_post_call_success_hook(
        self,
        data: Dict[str, Any],
        user_api_key_dict: Optional[Dict[str, Any]],
        response: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Log post-call response and pass through."""
        try:
            await self._log_request(
                "post",
                data,
                response,
                policy_action="allow",
                call_type=data.get("call_type"),
            )
            # Also record kwargs + response in debug logs
            call_id = self._extract_call_id_from_kwargs(data)
            dbg = {
                "call_type": data.get("call_type"),
                "kwargs": data,
                "response": response,
            }
            if call_id:
                dbg["litellm_call_id"] = call_id
            await self._debug_log("kwargs_post", dbg)
        except Exception as e:
            print(f"Error logging post-call response: {e}")

        # Pass through - no modification
        return None

    @beartype
    async def streaming_on_chunk(
        self,
        user_api_key_dict: Optional[Dict[str, Any]],
        request_data: Dict[str, Any],
        chunk: Dict[str, Any],
        chunk_index: int,
        accumulated_text: str,
    ) -> Dict[str, Any]:
        """Log streaming chunks periodically to avoid DB spam."""
        log_every_n = int(self.config.get("stream", {}).get("log_every_n", 10))
        if log_every_n > 0 and (chunk_index % log_every_n == 0):
            try:
                chunk_data = {
                    "chunk_index": chunk_index,
                    "accumulated_length": len(accumulated_text),
                    "chunk": chunk,
                }
                await self._log_request(
                    "streaming_chunk",
                    request_data,
                    response=chunk_data,
                    policy_action="allow",
                    policy_metadata={"chunk_index": chunk_index},
                    call_type=request_data.get("call_type"),
                )
                call_id = self._extract_call_id_from_kwargs(request_data)
                dbg = {
                    "call_type": request_data.get("call_type"),
                    "kwargs": request_data,
                    "chunk_index": chunk_index,
                    "accumulated_length": len(accumulated_text),
                    "chunk": chunk,
                }
                if call_id:
                    dbg["litellm_call_id"] = call_id
                await self._debug_log("stream_chunk", dbg)
            except Exception as e:
                print(f"Error logging streaming chunk: {e}")

        return {"action": "pass"}

    async def streaming_replacement(
        self,
        request_data: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Not implemented for logging policy - just yield empty."""
        if False:
            yield {}
        return

    # ---------------- Internal helpers ----------------
    def _extract_call_id_from_kwargs(self, data: Dict[str, Any]) -> Optional[str]:
        try:
            for p in (
                ["litellm_call_id"],
                ["litellm_params", "litellm_call_id"],
                ["litellm_metadata", "litellm_call_id"],
                ["litellm_metadata", "hidden_params", "litellm_call_id"],
            ):
                v = data
                ok = True
                for k in p:
                    if not isinstance(v, dict):
                        ok = False
                        break
                    v = v.get(k)
                if ok and isinstance(v, str) and v:
                    return v
        except Exception:
            return None
        return None

    def _load_config(
        self, inline_options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Load policy options.

        Priority:
        1) Options passed into constructor (from consolidated luthien_config.yaml)
        2) Env var `LUTHIEN_POLICY_OPTIONS_JSON` (JSON string)
        3) Env var `LUTHIEN_POLICY_OPTIONS` (path to YAML file; legacy)
        4) Built-in defaults
        """
        defaults: Dict[str, Any] = {
            "redact_keys": [
                "api_key",
                "Authorization",
                "authorization",
                "auth",
                "password",
                "bearer_token",
            ],
            "stream": {"log_every_n": 10},
        }
        # 1) Inline
        if inline_options is not None:
            try:
                merged = defaults.copy()
                for k, v in (inline_options or {}).items():
                    merged[k] = v
                return merged
            except Exception:
                pass
        # 2) JSON env
        json_env = os.getenv("LUTHIEN_POLICY_OPTIONS_JSON")
        if json_env:
            try:
                loaded = json.loads(json_env)
                merged = defaults.copy()
                for k, v in (loaded or {}).items():
                    merged[k] = v
                return merged
            except Exception as e:
                print(
                    f"LoggingPolicy: failed to parse LUTHIEN_POLICY_OPTIONS_JSON: {e}"
                )
        # 3) Legacy YAML path env
        path = os.getenv("LUTHIEN_POLICY_OPTIONS")
        try:
            if path and os.path.exists(path):
                with open(path, "r") as f:
                    loaded = yaml.safe_load(f) or {}
                    merged = defaults.copy()
                    for k, v in loaded.items():
                        merged[k] = v
                    return merged
        except Exception as e:
            print(f"LoggingPolicy: failed to load config from {path}: {e}")
        return defaults

    def _redact(self, obj: Optional[Any]) -> Optional[Any]:
        """Redact configured keys in nested structures."""
        if obj is None:
            return None
        keys: List[str] = [str(k) for k in self.config.get("redact_keys", [])]

        def _walk(v: Any) -> Any:
            if isinstance(v, dict):
                out: Dict[str, Any] = {}
                for k, vv in v.items():
                    if str(k) in keys:
                        out[k] = "<redacted>"
                    else:
                        out[k] = _walk(vv)
                return out
            if isinstance(v, list):
                return [_walk(i) for i in v]
            if isinstance(v, tuple):
                return tuple(_walk(i) for i in v)
            if isinstance(v, set):
                return {_walk(i) for i in v}
            return v

        try:
            return _walk(obj)
        except Exception:
            return obj
