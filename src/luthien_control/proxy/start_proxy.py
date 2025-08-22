# ABOUTME: Startup script for LiteLLM proxy with proper Luthien Control integration
# ABOUTME: Handles Python path setup and callback registration before starting LiteLLM

import os
import sys


def setup_environment():
    """Set up the environment for LiteLLM with our custom logger."""

    # Ensure our src directory is in Python path
    if "/app/src" not in sys.path:
        sys.path.insert(0, "/app/src")

    # Import and configure our custom logger
    from luthien_control.proxy.custom_logger import luthien_logger

    # Import LiteLLM and configure it
    import litellm
    from litellm.proxy.proxy_server import app

    # Set the callback directly on the litellm module
    litellm.callbacks = [luthien_logger]

    print("🎯 Luthien Control Logger configured successfully")
    print(f"📋 Active callbacks: {[cb.__class__.__name__ for cb in litellm.callbacks]}")

    return app


def main():
    """Start the LiteLLM proxy with Luthien Control integration."""

    print("🚀 Starting LiteLLM proxy with Luthien Control...")

    # Set up configuration
    config_path = os.getenv("LITELLM_CONFIG_PATH", "/app/config/litellm_config.yaml")
    host = os.getenv("LITELLM_HOST", "0.0.0.0")
    port = int(os.getenv("LITELLM_PORT", "4000"))

    print(f"📂 Config: {config_path}")
    print(f"🌐 Host: {host}:{port}")
    print(
        f"🎛️  Control Plane: {os.getenv('CONTROL_PLANE_URL', 'http://control-plane:8081')}"
    )

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
