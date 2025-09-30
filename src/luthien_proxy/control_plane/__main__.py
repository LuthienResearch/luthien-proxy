"""CLI entry point for the Luthien Control Plane (FastAPI)."""

import logging

import uvicorn

from luthien_proxy.control_plane.app import create_control_plane_app
from luthien_proxy.utils.logging_config import configure_logging
from luthien_proxy.utils.project_config import ProjectConfig

logger = logging.getLogger(__name__)


def main() -> None:
    """Start the Luthien Control Plane server."""
    config = ProjectConfig()
    app = create_control_plane_app(config)
    control = config.control_plane_config

    configure_logging(control.log_level)

    db_url = control.database_url

    logger.info("Starting control plane on %s:%s", control.host, control.port)
    logger.info("Database URL: %s", db_url)
    logger.info("Redis URL: %s", control.redis_url)
    logger.info("Changes are being picked up.")

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
