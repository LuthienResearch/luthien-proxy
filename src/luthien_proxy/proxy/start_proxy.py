"""Start the LiteLLM proxy with Luthien Control integration.

Prepares PYTHONPATH, ensures the YAML config is visible to the embedded
proxy_server, and launches Uvicorn for the app.
"""

import os
import sys
from typing import Any


def setup_environment() -> Any:
    """Set up the environment for LiteLLM with our custom logger."""
    # Ensure our src + config directories are in Python path
    for p in ("/app/src", "/app/config"):
        if p not in sys.path:
            sys.path.insert(0, p)

    # Ensure LiteLLM proxy reads our YAML config
    # LiteLLM's embedded proxy_server loads CONFIG_FILE_PATH (or WORKER_CONFIG),
    # not LITELLM_CONFIG_PATH. Set it explicitly before importing the app.
    config_path = os.getenv("LITELLM_CONFIG_PATH", "/app/config/litellm_config.yaml")
    os.environ.setdefault("CONFIG_FILE_PATH", config_path)

    # Import LiteLLM and configure it
    import litellm
    from litellm.proxy.proxy_server import app

    # Do not set callbacks programmatically; rely on YAML single-hook config

    print("🎯 Luthien Control Logger configured successfully")
    print(f"📋 Active callbacks: {[cb.__class__.__name__ for cb in litellm.callbacks]}")

    return app


def main() -> None:
    """Start the LiteLLM proxy with Luthien Control integration."""
    print("🚀 Starting LiteLLM proxy with Luthien Control...")

    # Set up configuration
    config_path = os.getenv("LITELLM_CONFIG_PATH", "/app/config/litellm_config.yaml")
    host = os.getenv("LITELLM_HOST", "0.0.0.0")
    port = int(os.getenv("LITELLM_PORT", "4000"))

    print(f"📂 Config: {config_path}")
    print(f"🌐 Host: {host}:{port}")
    print(f"🎛️  Control Plane: {os.getenv('CONTROL_PLANE_URL', 'http://control-plane:8081')}")

    # Set up environment and get the app
    app = setup_environment()

    # Start the server using uvicorn
    import uvicorn

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=os.getenv("LITELLM_LOG_LEVEL", "info").lower(),
        reload=False,  # Don't use reload in Docker
    )


if __name__ == "__main__":
    main()
