# ABOUTME: Main entry point for the LiteLLM proxy server with Luthien Control integration
# ABOUTME: Configures and starts LiteLLM proxy with our custom logger for AI control hooks

import os
import sys
from pathlib import Path

# Add the src directory to Python path for imports
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root / "src"))


def main():
    """Start LiteLLM proxy server with Luthien Control integration."""

    # Run Prisma migrations first
    print("🔧 Running Prisma migrations...")
    import subprocess

    try:
        subprocess.run(
            ["uv", "run", "prisma", "db", "push"], check=True, capture_output=True
        )
        print("✅ Prisma migrations completed")
    except subprocess.CalledProcessError as e:
        print(f"⚠️  Prisma migration failed: {e.stderr.decode()}")
        print("📝 Continuing anyway - tables may already exist")

    # Import our custom logger
    from luthien_control.proxy.custom_logger import luthien_logger

    # Set up environment variables for LiteLLM
    config_path = os.getenv("LITELLM_CONFIG_PATH", "/app/config/litellm_config.yaml")

    # Import LiteLLM after setting up the path
    import litellm

    # Configure LiteLLM with our custom logger
    litellm.callbacks = [luthien_logger]

    print(f"✅ Configured LiteLLM with Luthien Control logger: {luthien_logger}")
    print(f"✅ Callbacks configured: {litellm.callbacks}")

    # Set environment variables for LiteLLM proxy
    os.environ.setdefault("LITELLM_CONFIG_PATH", config_path)
    os.environ.setdefault("LITELLM_PORT", "4000")
    os.environ.setdefault("LITELLM_HOST", "0.0.0.0")

    # Database configuration - use LiteLLM-specific database
    database_url = os.getenv("LITELLM_DATABASE_URL") or os.getenv("DATABASE_URL")
    if database_url:
        os.environ["DATABASE_URL"] = database_url

    print("Starting LiteLLM proxy with Luthien Control integration...")
    print(f"Config path: {config_path}")
    print(
        f"Control plane URL: {os.getenv('CONTROL_PLANE_URL', 'http://localhost:8081')}"
    )

    # Initialize and start the proxy
    try:
        # Start the proxy server
        from litellm.proxy.proxy_server import startup_event
        import uvicorn

        # Run startup event to initialize proxy
        import asyncio

        asyncio.create_task(startup_event())

        # Start the server
        uvicorn.run(
            "litellm.proxy.proxy_server:app",
            host=os.getenv("LITELLM_HOST", "0.0.0.0"),
            port=int(os.getenv("LITELLM_PORT", "4000")),
            reload=False,  # Disable reload in container
            log_level=os.getenv("LITELLM_LOG_LEVEL", "info").lower(),
        )

    except KeyboardInterrupt:
        print("\nShutting down LiteLLM proxy...")
    except Exception as e:
        print(f"Error starting LiteLLM proxy: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
