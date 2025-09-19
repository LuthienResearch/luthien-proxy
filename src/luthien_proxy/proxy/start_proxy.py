"""Start the LiteLLM proxy with Luthien Control integration.

Prepares PYTHONPATH, ensures the YAML config is visible to the embedded
proxy_server, and launches Uvicorn for the app.
"""

import os
import sys
from typing import Any, Callable, MutableMapping, Tuple

EnvMap = MutableMapping[str, str]
Importer = Callable[[], Tuple[Any, Any]]


def _import_litellm() -> Tuple[Any, Any]:
    import litellm
    from litellm.proxy.proxy_server import app

    return litellm, app


def setup_environment(
    importer: Importer | None = None,
    env: EnvMap | None = None,
) -> Any:
    """Set up the environment for LiteLLM with our custom logger."""
    # Ensure our src + config directories are in Python path
    for p in ("/app/src", "/app/config"):
        if p not in sys.path:
            sys.path.insert(0, p)

    env_map = env if env is not None else os.environ

    # Ensure LiteLLM proxy reads our YAML config
    # LiteLLM's embedded proxy_server loads CONFIG_FILE_PATH (or WORKER_CONFIG),
    # not LITELLM_CONFIG_PATH. Set it explicitly before importing the app.
    config_path = env_map.get("LITELLM_CONFIG_PATH", "/app/config/litellm_config.yaml")
    env_map.setdefault("CONFIG_FILE_PATH", config_path)

    importer = importer or _import_litellm
    litellm, app = importer()

    # Do not set callbacks programmatically; rely on YAML single-hook config

    print("🎯 Luthien Control Logger configured successfully")
    print(f"📋 Active callbacks: {[cb.__class__.__name__ for cb in litellm.callbacks]}")

    return app


def main(
    *,
    importer: Importer | None = None,
    runner: Callable[..., Any] | None = None,
    env: EnvMap | None = None,
) -> None:
    """Start the LiteLLM proxy with Luthien Control integration."""
    print("🚀 Starting LiteLLM proxy with Luthien Control...")

    env_map = env if env is not None else os.environ

    # Set up configuration
    config_path = env_map.get("LITELLM_CONFIG_PATH", "/app/config/litellm_config.yaml")
    host = env_map.get("LITELLM_HOST", "0.0.0.0")
    port = int(env_map.get("LITELLM_PORT", "4000"))

    print(f"📂 Config: {config_path}")
    print(f"🌐 Host: {host}:{port}")
    print(f"🎛️  Control Plane: {env_map.get('CONTROL_PLANE_URL', 'http://control-plane:8081')}")

    # Set up environment and get the app
    app = setup_environment(importer=importer, env=env_map)

    # Start the server using uvicorn
    uvicorn_runner = runner
    if uvicorn_runner is None:
        import uvicorn

        uvicorn_runner = uvicorn.run

    uvicorn_runner(
        app,
        host=host,
        port=port,
        log_level=env_map.get("LITELLM_LOG_LEVEL", "info").lower(),
        reload=False,  # Don't use reload in Docker
    )


if __name__ == "__main__":
    main()
