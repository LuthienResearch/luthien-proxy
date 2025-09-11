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

    # Ensure Python can import callback module from config/
    try:
        existing_pp = os.environ.get("PYTHONPATH", "")
        extra_paths = ["/app/src", "/app/config"]
        new_pp = ":".join([p for p in extra_paths if p])
        if existing_pp:
            new_pp = f"{new_pp}:{existing_pp}"
        os.environ["PYTHONPATH"] = new_pp
    except Exception:
        pass

    # Set up environment variables for LiteLLM
    config_path = os.getenv("LITELLM_CONFIG_PATH", "/app/config/litellm_config.yaml")

    # Set environment variables for LiteLLM proxy
    os.environ.setdefault("LITELLM_CONFIG_PATH", config_path)
    # IMPORTANT: the embedded proxy_server loads CONFIG_FILE_PATH, not LITELLM_CONFIG_PATH
    os.environ.setdefault("CONFIG_FILE_PATH", config_path)
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

    # Start LiteLLM via CLI with a single config path
    try:
        import subprocess

        cmd = [
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

        detailed = os.getenv("LITELLM_DETAILED_DEBUG", "false").lower() in {
            "1",
            "true",
            "yes",
        }
        if detailed:
            cmd.append("--detailed_debug")

        print(f"Starting LiteLLM with command: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\nShutting down LiteLLM proxy...")
    except subprocess.CalledProcessError as e:
        print(f"Error starting LiteLLM proxy: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error starting LiteLLM proxy: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
