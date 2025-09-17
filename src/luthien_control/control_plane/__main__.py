# ABOUTME: Main entry point for the Luthien Control plane service
# ABOUTME: Starts the FastAPI server with uvicorn for AI control policy orchestration

import os

import uvicorn

from luthien_control.utils.logging_config import configure_logging


def main():
    """Start the control plane service."""

    host = os.getenv("CONTROL_PLANE_HOST", "0.0.0.0")
    port = int(os.getenv("CONTROL_PLANE_PORT", "8081"))
    log_level = os.getenv("LOG_LEVEL", "info").lower()

    # Ensure Python logging goes to stdout at desired level
    configure_logging(log_level)

    print(f"Starting Luthien Control Plane on {host}:{port}")
    print(f"Database URL: {os.getenv('DATABASE_URL', 'Not configured')}")
    print(f"Redis URL: {os.getenv('REDIS_URL', 'Not configured')}")

    uvicorn.run(
        "luthien_control.control_plane.app:app",
        host=host,
        port=port,
        reload=False,  # Disable reload in container
        log_level=log_level,
        access_log=True,
    )


if __name__ == "__main__":
    main()
