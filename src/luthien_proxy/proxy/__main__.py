"""Entry point for running the LiteLLM proxy under Luthien control."""

import subprocess
from typing import Callable

from luthien_proxy.utils.project_config import ProjectConfig, ProxyConfig


def _run_prisma_migrations(runner: Callable) -> None:
    run = runner or subprocess.run
    run(
        [
            "uv",
            "run",
            "prisma",
            "db",
            "push",
            "--schema",
            "prisma/litellm/schema.prisma",
        ],
        check=True,
    )


def _litellm_command(
    *,
    config_path: str,
    host: str,
    port: str,
    detailed_debug: bool,
) -> list[str]:
    cmd = [
        "uv",
        "run",
        "litellm",
        "--config",
        config_path,
        "--port",
        port,
        "--host",
        host,
    ]
    if detailed_debug:
        cmd.append("--detailed_debug")
    return cmd


def main(
    *,
    prisma_runner: Callable = subprocess.run,
    command_runner: Callable = subprocess.run,
    config: ProjectConfig | None = None,
) -> None:
    """Start the LiteLLM proxy with Luthien Control integration."""
    project_config = config or ProjectConfig()
    proxy_settings: ProxyConfig = project_config.proxy_config

    _run_prisma_migrations(prisma_runner)
    cmd = _litellm_command(
        config_path=proxy_settings.config_path,
        host=proxy_settings.host,
        port=str(proxy_settings.port),
        detailed_debug=proxy_settings.detailed_debug,
    )

    runner = command_runner or subprocess.run
    runner(cmd, check=True)


if __name__ == "__main__":
    main()
