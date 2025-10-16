"""Typed configuration object for the Luthien proxy + control plane."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cached_property
from typing import Callable, Generic, Mapping, Optional, TypeVar, cast

__all__ = [
    "ProjectConfig",
    "ProxyConfig",
    "ControlPlaneConfig",
    "ConversationStreamConfig",
]

T = TypeVar("T")


@dataclass(frozen=True)
class ConfigValue(Generic[T]):
    env_var: str
    default: Optional[T] = None
    parser: Optional[Callable[[str], T]] = None


def get_config_value(env: Mapping[str, str], config: ConfigValue[T]) -> T:
    raw = env.get(config.env_var)
    if raw is None:
        if config.default is None:
            raise RuntimeError(f"{config.env_var} must be set")
        return config.default
    try:
        if config.parser is not None:
            return config.parser(raw)
        return cast(T, raw)
    except ValueError as exc:
        raise RuntimeError(f"{config.env_var} has invalid value {raw!r}: {exc}") from exc


def _parse_bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise ValueError("expected 'true' or 'false'")


DATABASE_URL = ConfigValue[Optional[str]]("DATABASE_URL")
REDIS_URL = ConfigValue[Optional[str]]("REDIS_URL")
LUTHIEN_POLICY_CONFIG = ConfigValue[Optional[str]]("LUTHIEN_POLICY_CONFIG")
LITELLM_CONFIG_PATH = ConfigValue[str]("LITELLM_CONFIG_PATH")
LITELLM_HOST = ConfigValue[str]("LITELLM_HOST", "0.0.0.0")
LITELLM_PORT = ConfigValue[int]("LITELLM_PORT", 4000, parser=int)
LITELLM_DETAILED_DEBUG = ConfigValue[bool]("LITELLM_DETAILED_DEBUG", False, parser=_parse_bool)
LITELLM_LOG = ConfigValue[str]("LITELLM_LOG", "INFO")
CONTROL_PLANE_URL = ConfigValue[str]("CONTROL_PLANE_URL", "http://localhost:8081")
CONTROL_PLANE_HOST = ConfigValue[str]("CONTROL_PLANE_HOST", "0.0.0.0")
CONTROL_PLANE_PORT = ConfigValue[int]("CONTROL_PLANE_PORT", 8081, parser=int)
CONTROL_PLANE_LOG_LEVEL = ConfigValue[str]("CONTROL_PLANE_LOG_LEVEL", "INFO")
STREAM_CONTEXT_TTL = ConfigValue[int]("STREAM_CONTEXT_TTL", 3600, parser=int)
CONVERSATION_STREAM_HEARTBEAT_SECONDS = ConfigValue[float]("CONVERSATION_STREAM_HEARTBEAT_SECONDS", 15.0, parser=float)
CONVERSATION_STREAM_REDIS_TIMEOUT_SECONDS = ConfigValue[float](
    "CONVERSATION_STREAM_REDIS_TIMEOUT_SECONDS", 1.0, parser=float
)
CONVERSATION_STREAM_RATE_LIMIT_MAX_REQUESTS = ConfigValue[int](
    "CONVERSATION_STREAM_RATE_LIMIT_MAX_REQUESTS", 60, parser=int
)
CONVERSATION_STREAM_RATE_LIMIT_WINDOW_SECONDS = ConfigValue[float](
    "CONVERSATION_STREAM_RATE_LIMIT_WINDOW_SECONDS", 60.0, parser=float
)
ENABLE_DEMO_MODE = ConfigValue[bool]("ENABLE_DEMO_MODE", False, parser=_parse_bool)


@dataclass(frozen=True)
class ProxyConfig:
    """Configuration for the LiteLLM proxy."""

    config_path: str
    host: str
    port: int
    detailed_debug: bool
    log_level: str
    control_plane_url: str


@dataclass(frozen=True)
class ControlPlaneConfig:
    """Configuration for the control plane."""

    host: str
    port: int
    log_level: str
    redis_url: str
    stream_context_ttl: int
    policy_config_path: str
    database_url: str
    conversation_stream_config: "ConversationStreamConfig"
    enable_demo_mode: bool


@dataclass(frozen=True)
class ConversationStreamConfig:
    """Configuration for SSE conversation streaming."""

    heartbeat_seconds: float
    redis_poll_timeout_seconds: float
    rate_limit_max_requests: int = 60
    rate_limit_window_seconds: float = 60.0


class ProjectConfig:
    """Typed configuration for the Luthien proxy and control plane."""

    _env_map: Mapping[str, str] = os.environ

    def __init__(self, env_map: Mapping[str, str] = os.environ):
        """Create a new ProjectConfig from the given environment mapping (default to os.environ)."""
        self._env_map = env_map

    @cached_property
    def database_url(self) -> str:
        """Database connection URL, if any."""
        db_url: Optional[str] = get_config_value(self._env_map, DATABASE_URL)
        if db_url is None:
            raise RuntimeError(f"{DATABASE_URL.env_var} must be set to run the control plane")
        return db_url

    @cached_property
    def redis_url(self) -> Optional[str]:
        """Redis connection URL, if any."""
        return get_config_value(self._env_map, REDIS_URL)

    @cached_property
    def luthien_policy_config(self) -> Optional[str]:
        """Luthien policy configuration, if any."""
        return get_config_value(self._env_map, LUTHIEN_POLICY_CONFIG)

    @cached_property
    def litellm_config_path(self) -> str:
        """Path to the LiteLLM configuration file."""
        return get_config_value(self._env_map, LITELLM_CONFIG_PATH)

    @cached_property
    def litellm_host(self) -> str:
        """Host for the LiteLLM server."""
        return get_config_value(self._env_map, LITELLM_HOST)

    @cached_property
    def litellm_port(self) -> int:
        """Port for the LiteLLM server."""
        return get_config_value(self._env_map, LITELLM_PORT)

    @cached_property
    def litellm_detailed_debug(self) -> bool:
        """Whether detailed debug is enabled for LiteLLM."""
        return get_config_value(self._env_map, LITELLM_DETAILED_DEBUG)

    @cached_property
    def litellm_log_level(self) -> str:
        """Log level for the LiteLLM server."""
        return get_config_value(self._env_map, LITELLM_LOG)

    @cached_property
    def control_plane_url(self) -> str:
        """URL for the control plane."""
        return get_config_value(self._env_map, CONTROL_PLANE_URL)

    @cached_property
    def control_plane_host(self) -> str:
        """Host for the control plane."""
        return get_config_value(self._env_map, CONTROL_PLANE_HOST)

    @cached_property
    def control_plane_port(self) -> int:
        """Port for the control plane."""
        return get_config_value(self._env_map, CONTROL_PLANE_PORT)

    @cached_property
    def control_plane_log_level(self) -> str:
        """Log level for the control plane."""
        return get_config_value(self._env_map, CONTROL_PLANE_LOG_LEVEL)

    @cached_property
    def stream_context_ttl(self) -> int:
        """TTL for stream contexts in seconds."""
        return get_config_value(self._env_map, STREAM_CONTEXT_TTL)

    @cached_property
    def conversation_stream_config(self) -> ConversationStreamConfig:
        """Settings for SSE-based conversation streaming."""
        return ConversationStreamConfig(
            heartbeat_seconds=get_config_value(self._env_map, CONVERSATION_STREAM_HEARTBEAT_SECONDS),
            redis_poll_timeout_seconds=get_config_value(self._env_map, CONVERSATION_STREAM_REDIS_TIMEOUT_SECONDS),
            rate_limit_max_requests=max(
                1, get_config_value(self._env_map, CONVERSATION_STREAM_RATE_LIMIT_MAX_REQUESTS)
            ),
            rate_limit_window_seconds=max(
                0.1, get_config_value(self._env_map, CONVERSATION_STREAM_RATE_LIMIT_WINDOW_SECONDS)
            ),
        )

    @cached_property
    def proxy_config(self) -> ProxyConfig:
        """Get the proxy configuration."""
        return ProxyConfig(
            config_path=self.litellm_config_path,
            host=self.litellm_host,
            port=self.litellm_port,
            detailed_debug=self.litellm_detailed_debug,
            log_level=self.litellm_log_level,
            control_plane_url=self.control_plane_url,
        )

    @cached_property
    def enable_demo_mode(self) -> bool:
        """Whether demo mode is enabled (default False for security)."""
        return get_config_value(self._env_map, ENABLE_DEMO_MODE)

    @cached_property
    def control_plane_config(self) -> ControlPlaneConfig:
        """Get the control plane configuration."""
        if self.redis_url is None:
            raise RuntimeError(f"{REDIS_URL.env_var} must be set to run the control plane")
        if self.luthien_policy_config is None:
            raise RuntimeError(f"{LUTHIEN_POLICY_CONFIG.env_var} must point to a policy configuration file")
        return ControlPlaneConfig(
            host=self.control_plane_host,
            port=self.control_plane_port,
            log_level=self.control_plane_log_level,
            redis_url=self.redis_url,
            stream_context_ttl=self.stream_context_ttl,
            policy_config_path=self.luthien_policy_config,
            database_url=self.database_url,
            conversation_stream_config=self.conversation_stream_config,
            enable_demo_mode=self.enable_demo_mode,
        )

    def with_overrides(self, **changes) -> "ProjectConfig":
        """Create a new ProjectConfig with the given overrides."""
        new_env = dict(self._env_map)
        changes = {k: str(v) for k, v in changes.items() if v is not None}
        new_env.update(changes)
        return ProjectConfig(env_map=new_env)
