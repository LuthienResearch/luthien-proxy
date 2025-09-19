"""Start the LiteLLM proxy with Luthien Control integration.

Prepares PYTHONPATH, ensures the YAML config is visible to the embedded
proxy_server, and launches Uvicorn for the app.
"""

import os
from dataclasses import dataclass
from types import ModuleType
from typing import Callable, MutableMapping, Optional

from starlette.types import ASGIApp

EnvMap = MutableMapping[str, str]


@dataclass
class ProxyRuntime:
    """Runtime configuration for the LiteLLM proxy server (to support dependency injection)."""

    env: EnvMap
    uvicorn_runner: Callable[..., object]
    litellm: Optional[ModuleType] = None
    app: Optional[ASGIApp] = None


def _get_real_runtime() -> ProxyRuntime:
    import litellm
    import uvicorn
    from litellm.proxy.proxy_server import app

    return ProxyRuntime(env=os.environ, uvicorn_runner=uvicorn.run, litellm=litellm, app=app)


def runtime_for_tests(
    *,
    env: EnvMap,
    uvicorn_runner: Callable[..., object],
    litellm: ModuleType,
    app: ASGIApp,
) -> ProxyRuntime:
    """Helper for tests to build a deterministic runtime."""
    return ProxyRuntime(env=env, uvicorn_runner=uvicorn_runner, litellm=litellm, app=app)


def main(runtime: ProxyRuntime | None = None) -> None:
    """Start the LiteLLM proxy with Luthien Control integration."""
    print("🚀 Starting LiteLLM proxy with Luthien Control...")

    if runtime is None:
        runtime = _get_real_runtime()
    env_map = runtime.env

    # Set up configuration
    config_path = env_map.get("LITELLM_CONFIG_PATH", "/app/config/litellm_config.yaml")
    host = env_map.get("LITELLM_HOST", "0.0.0.0")
    port = int(env_map.get("LITELLM_PORT", "4000"))

    print(f"📂 Config: {config_path}")
    print(f"🌐 Host: {host}:{port}")
    print(f"🎛️  Control Plane: {env_map.get('CONTROL_PLANE_URL', 'http://control-plane:8081')}")

    # Start the server using uvicorn
    runtime.uvicorn_runner(
        runtime.app,
        host=host,
        port=port,
        log_level=env_map.get("LITELLM_LOG_LEVEL", "info").lower(),
        reload=False,  # Don't use reload in Docker
    )


if __name__ == "__main__":
    main()
