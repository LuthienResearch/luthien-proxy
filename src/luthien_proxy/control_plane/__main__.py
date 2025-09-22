"""CLI entry point for the Luthien Control Plane (FastAPI)."""

import uvicorn

from luthien_proxy.control_plane.app import create_control_plane_app
from luthien_proxy.utils.logging_config import configure_logging
from luthien_proxy.utils.project_config import ProjectConfig


def main() -> None:
    """Start the Luthien Control Plane server."""
    config = ProjectConfig()
    app = create_control_plane_app(config)
    control = config.control_plane_config

    configure_logging(control.log_level)

    print(f"Starting Luthien Control Plane on {control.host}:{control.port}")
    db_url = control.database_url or "Not configured"
    print(f"Database URL: {db_url}")
    print(f"Redis URL: {control.redis_url}")

    uvicorn.run(
        app,
        host=control.host,
        port=control.port,
        reload=False,
        log_level=control.log_level.lower(),
        access_log=True,
    )


if __name__ == "__main__":  # pragma: no cover - manual invocation
    main()
