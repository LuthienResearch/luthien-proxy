"""CLI entry to start the LiteLLM proxy with Luthien Control.

The previous single `main()` mixed environment wiring, migrations, command
assembly, and process execution. Split into tiny helpers to keep the happy
path readable and reduce branching.
"""

import os
import sys
from pathlib import Path
from typing import Any, Callable, List, MutableMapping

# Add the src directory to Python path for imports
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root / "src"))


Runner = Callable[..., Any]
EnvMap = MutableMapping[str, str]


def _run_prisma_migrations(runner: Runner | None = None) -> None:
    """Run `prisma db push`; continue if it fails (tables may exist)."""
    import subprocess

    print("🔧 Running Prisma migrations...")
    try:
        run = runner or subprocess.run
        run(["uv", "run", "prisma", "db", "push"], check=True, capture_output=True)
        print("✅ Prisma migrations completed")
    except Exception as e:  # CalledProcessError or other env issues
        msg = getattr(e, "stderr", b"").decode() if hasattr(e, "stderr") else str(e)
        print(f"⚠️  Prisma migration failed: {msg}")
        print("📝 Continuing anyway - tables may already exist")


def _prepend_pythonpath(extra: List[str], env: EnvMap | None = None) -> None:
    env_map = env if env is not None else os.environ
    existing = env_map.get("PYTHONPATH", "")
    new = ":".join(extra)
    env_map["PYTHONPATH"] = f"{new}:{existing}" if existing else new


def _bool_env(name: str, default: str = "false", env: EnvMap | None = None) -> bool:
    env_map = env if env is not None else os.environ
    return env_map.get(name, default).lower() in {"1", "true", "yes", "y"}


def _apply_env(config_path: str, env: EnvMap | None = None) -> None:
    env_map = env if env is not None else os.environ
    env_map.setdefault("LITELLM_CONFIG_PATH", config_path)
    env_map.setdefault("LITELLM_PORT", "4000")
    env_map.setdefault("LITELLM_HOST", "0.0.0.0")
    db_url = env_map.get("LITELLM_DATABASE_URL") or env_map.get("DATABASE_URL")
    if db_url:
        env_map["DATABASE_URL"] = db_url


def _build_litellm_cmd(config_path: str, env: EnvMap | None = None) -> List[str]:
    env_map = env if env is not None else os.environ
    cmd: List[str] = [
        "uv",
        "run",
        "litellm",
        "--config",
        config_path,
        "--port",
        env_map.get("LITELLM_PORT", "4000"),
        "--host",
        env_map.get("LITELLM_HOST", "0.0.0.0"),
    ]
    if _bool_env("LITELLM_DETAILED_DEBUG", env=env_map):
        cmd.append("--detailed_debug")
    return cmd


def main(
    *,
    prisma_runner: Runner | None = None,
    command_runner: Runner | None = None,
    env: EnvMap | None = None,
) -> None:
    """Start LiteLLM proxy server with Luthien Control integration."""
    env_map = env if env is not None else os.environ

    _run_prisma_migrations(prisma_runner)
    _prepend_pythonpath(["/app/src", "/app/config"], env=env_map)

    config_path = env_map.get("LITELLM_CONFIG_PATH", "/app/config/litellm_config.yaml")
    _apply_env(config_path, env=env_map)

    print("Starting LiteLLM proxy with Luthien Control integration...")
    print(f"Config path: {config_path}")
    print(f"Control plane URL: {env_map.get('CONTROL_PLANE_URL', 'http://localhost:8081')}")

    import subprocess

    cmd = _build_litellm_cmd(config_path, env=env_map)
    print(f"Starting LiteLLM with command: {' '.join(cmd)}")
    runner = command_runner or subprocess.run
    try:
        runner(cmd, check=True)
    except KeyboardInterrupt:
        print("\nShutting down LiteLLM proxy...")
    except Exception as e:
        print(f"Error starting LiteLLM proxy: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
