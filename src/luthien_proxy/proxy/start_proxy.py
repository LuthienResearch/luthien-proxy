"""Start the LiteLLM proxy with Luthien Control integration."""

from dataclasses import dataclass
from types import ModuleType
from typing import Callable, Optional

from starlette.types import ASGIApp

from luthien_proxy.utils.project_config import ProjectConfig, ProxyConfig


@dataclass
class ProxyRuntime:
    """Runtime configuration for the LiteLLM proxy server (to support dependency injection)."""

    config: ProjectConfig
    uvicorn_runner: Callable[..., object]
    litellm: Optional[ModuleType] = None
    app: Optional[ASGIApp] = None


def _get_real_runtime() -> ProxyRuntime:
    import litellm
    import uvicorn
    from litellm.proxy.proxy_server import app

    config = ProjectConfig()
    return ProxyRuntime(config=config, uvicorn_runner=uvicorn.run, litellm=litellm, app=app)


def runtime_for_tests(
    *,
    config: ProjectConfig,
    uvicorn_runner: Callable[..., object],
    litellm: ModuleType,
    app: ASGIApp,
) -> ProxyRuntime:
    """Helper for tests to build a deterministic runtime."""
    return ProxyRuntime(config=config, uvicorn_runner=uvicorn_runner, litellm=litellm, app=app)


def main(runtime: ProxyRuntime | None = None) -> None:
    """Start the LiteLLM proxy with Luthien Control integration."""
    print("🚀 Starting LiteLLM proxy with Luthien Control...")

    if runtime is None:
        runtime = _get_real_runtime()

    proxy_settings: ProxyConfig = runtime.config.proxy_config

    print(f"📂 Config: {proxy_settings.config_path}")
    print(f"🌐 Host: {proxy_settings.host}:{proxy_settings.port}")
    print(f"🎛️  Control Plane: {proxy_settings.control_plane_url}")

    # Start the server using uvicorn
    runtime.uvicorn_runner(
        runtime.app,
        host=proxy_settings.host,
        port=proxy_settings.port,
        log_level=proxy_settings.log_level.lower(),
        reload=False,  # Don't use reload in Docker
    )


if __name__ == "__main__":
    main()
