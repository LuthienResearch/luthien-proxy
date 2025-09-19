"""Entry point for running the LiteLLM proxy under Luthien control."""

import os
import subprocess
from typing import Any, Callable, MutableMapping

Runner = Callable[..., Any]
EnvMap = MutableMapping[str, str]


def _run_prisma_migrations(runner: Runner | None = None) -> None:
    run = runner or subprocess.run
    run(["uv", "run", "prisma", "db", "push"], check=True)


def _config_path(env: EnvMap) -> str:
    config_path = env.get("LITELLM_CONFIG_PATH")
    if not config_path:
        raise RuntimeError("LITELLM_CONFIG_PATH must be set")
    return config_path


def _sync_database_url(env: EnvMap) -> None:
    db_url = env.get("LITELLM_DATABASE_URL") or env.get("DATABASE_URL")
    if db_url:
        env["DATABASE_URL"] = db_url


def _litellm_command(config_path: str, env: EnvMap) -> list[str]:
    host = env.get("LITELLM_HOST", "0.0.0.0")
    port = env.get("LITELLM_PORT", "4000")

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

    detailed_debug = env.get("LITELLM_DETAILED_DEBUG", "").lower()
    if detailed_debug in {"1", "true", "yes", "y"}:
        cmd.append("--detailed_debug")

    return cmd


def main(
    *,
    prisma_runner: Runner | None = None,
    command_runner: Runner | None = None,
    env: EnvMap | None = None,
) -> None:
    """Start the LiteLLM proxy with Luthien Control integration."""
    env_map = env if env is not None else os.environ

    _run_prisma_migrations(prisma_runner)
    config_path = _config_path(env_map)
    _sync_database_url(env_map)
    cmd = _litellm_command(config_path, env_map)

    runner = command_runner or subprocess.run
    runner(cmd, check=True)


if __name__ == "__main__":
    main()
