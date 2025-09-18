"""CLI entry to start the LiteLLM proxy with Luthien Control.

The previous single `main()` mixed environment wiring, migrations, command
assembly, and process execution. Split into tiny helpers to keep the happy
path readable and reduce branching.
"""

import os
import sys
from pathlib import Path
from typing import List

# Add the src directory to Python path for imports
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root / "src"))


def _run_prisma_migrations() -> None:
    """Run `prisma db push`; continue if it fails (tables may exist)."""
    import subprocess

    print("🔧 Running Prisma migrations...")
    try:
        subprocess.run(["uv", "run", "prisma", "db", "push"], check=True, capture_output=True)
        print("✅ Prisma migrations completed")
    except Exception as e:  # CalledProcessError or other env issues
        msg = getattr(e, "stderr", b"").decode() if hasattr(e, "stderr") else str(e)
        print(f"⚠️  Prisma migration failed: {msg}")
        print("📝 Continuing anyway - tables may already exist")


def _prepend_pythonpath(extra: List[str]) -> None:
    existing = os.environ.get("PYTHONPATH", "")
    new = ":".join(extra)
    os.environ["PYTHONPATH"] = f"{new}:{existing}" if existing else new


def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "y"}


def _apply_env(config_path: str) -> None:
    os.environ.setdefault("LITELLM_CONFIG_PATH", config_path)
    # Embedded proxy_server reads CONFIG_FILE_PATH
    os.environ.setdefault("CONFIG_FILE_PATH", config_path)
    os.environ.setdefault("LITELLM_PORT", "4000")
    os.environ.setdefault("LITELLM_HOST", "0.0.0.0")
    db_url = os.getenv("LITELLM_DATABASE_URL") or os.getenv("DATABASE_URL")
    if db_url:
        os.environ["DATABASE_URL"] = db_url


def _build_litellm_cmd(config_path: str) -> List[str]:
    cmd: List[str] = [
        "uv",
        "run",
        "litellm",
        "--config",
        config_path,
        "--port",
        os.getenv("LITELLM_PORT", "4000"),
        "--host",
        os.getenv("LITELLM_HOST", "0.0.0.0"),
    ]
    if _bool_env("LITELLM_DETAILED_DEBUG"):
        cmd.append("--detailed_debug")
    return cmd


def main() -> None:
    """Start LiteLLM proxy server with Luthien Control integration."""
    _run_prisma_migrations()
    _prepend_pythonpath(["/app/src", "/app/config"])

    config_path = os.getenv("LITELLM_CONFIG_PATH", "/app/config/litellm_config.yaml")
    _apply_env(config_path)

    print("Starting LiteLLM proxy with Luthien Control integration...")
    print(f"Config path: {config_path}")
    print(f"Control plane URL: {os.getenv('CONTROL_PLANE_URL', 'http://localhost:8081')}")

    import subprocess

    cmd = _build_litellm_cmd(config_path)
    print(f"Starting LiteLLM with command: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\nShutting down LiteLLM proxy...")
    except Exception as e:
        print(f"Error starting LiteLLM proxy: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
