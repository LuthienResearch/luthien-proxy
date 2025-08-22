#!/usr/bin/env python3

"""
ABOUTME: Validation script to ensure the Luthien Control development environment is properly configured
ABOUTME: Checks all files, imports, configurations, and dependencies for a working setup
"""

import os
import sys
from pathlib import Path
import yaml
import subprocess


class SetupValidator:
    """Validates that the development environment is properly configured."""

    def __init__(self):
        self.project_root = Path(__file__).parent.parent
        self.errors = []
        self.warnings = []

    def validate_project_structure(self):
        """Check that all required files and directories exist."""
        print("📁 Validating project structure...")

        required_files = [
            "pyproject.toml",
            "docker-compose.yaml",
            ".env.example",
            "config/litellm_config.yaml",
            "config/policy_default.yaml",
            "migrations/001_initial_schema.sql",
            "src/luthien_control/proxy/__init__.py",
            "src/luthien_control/proxy/custom_logger.py",
            "src/luthien_control/proxy/__main__.py",
            "src/luthien_control/control_plane/__init__.py",
            "src/luthien_control/control_plane/app.py",
            "src/luthien_control/control_plane/__main__.py",
            "src/luthien_control/policies/engine.py",
            "src/luthien_control/monitors/trusted.py",
            "src/luthien_control/monitors/untrusted.py",
            "scripts/dev_setup.sh",
            "scripts/test_proxy.py",
            "scripts/monitor_services.sh",
        ]

        required_dirs = [
            "src/luthien_control",
            "config",
            "migrations",
            "scripts",
            "docker",
        ]

        for file_path in required_files:
            full_path = self.project_root / file_path
            if not full_path.exists():
                self.errors.append(f"Missing required file: {file_path}")
            else:
                print(f"✅ {file_path}")

        for dir_path in required_dirs:
            full_path = self.project_root / dir_path
            if not full_path.exists():
                self.errors.append(f"Missing required directory: {dir_path}")
            else:
                print(f"✅ {dir_path}/")

    def validate_configurations(self):
        """Validate configuration files are valid YAML/JSON."""
        print("\n⚙️  Validating configuration files...")

        # Validate YAML files
        yaml_files = ["config/litellm_config.yaml", "config/policy_default.yaml"]

        for yaml_file in yaml_files:
            try:
                with open(self.project_root / yaml_file, "r") as f:
                    yaml.safe_load(f)
                print(f"✅ {yaml_file}")
            except Exception as e:
                self.errors.append(f"Invalid YAML in {yaml_file}: {e}")

        # Validate docker-compose.yaml
        try:
            with open(self.project_root / "docker-compose.yaml", "r") as f:
                yaml.safe_load(f)
            print("✅ docker-compose.yaml")
        except Exception as e:
            self.errors.append(f"Invalid docker-compose.yaml: {e}")

    def validate_python_imports(self):
        """Check that Python modules can be imported."""
        print("\n🐍 Validating Python imports...")

        sys.path.insert(0, str(self.project_root / "src"))

        modules_to_test = [
            "luthien_control",
            "luthien_control.proxy",
            # Skip custom_logger - has complex LiteLLM dependencies
            "luthien_control.control_plane",
            "luthien_control.control_plane.app",
            "luthien_control.policies.engine",
            "luthien_control.monitors.trusted",
            "luthien_control.monitors.untrusted",
        ]

        for module in modules_to_test:
            try:
                __import__(module)
                print(f"✅ {module}")
            except Exception as e:
                self.errors.append(f"Cannot import {module}: {e}")

    def validate_dependencies(self):
        """Check that required dependencies are available."""
        print("\n📦 Validating dependencies...")

        required_packages = [
            "asyncpg",
            "beartype",
            "fastapi",
            "httpx",
            "litellm",
            "psycopg",
            "pydantic",
            "yaml",  # pyyaml imports as 'yaml'
            "redis",
            "uvicorn",
        ]

        for package in required_packages:
            try:
                __import__(package)
                print(f"✅ {package}")
            except ImportError:
                self.errors.append(f"Missing dependency: {package}")

    def validate_docker_setup(self):
        """Check Docker and docker-compose availability."""
        print("\n🐳 Validating Docker setup...")

        try:
            result = subprocess.run(
                ["docker", "--version"], capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"✅ Docker: {result.stdout.strip()}")
            else:
                self.errors.append("Docker not available")
        except FileNotFoundError:
            self.errors.append("Docker not installed")

        try:
            # Try Docker Compose V2 first (preferred)
            result = subprocess.run(
                ["docker", "compose", "version"], capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"✅ Docker Compose: {result.stdout.strip()}")
            else:
                # Try legacy docker-compose
                result = subprocess.run(
                    ["docker-compose", "--version"], capture_output=True, text=True
                )
                if result.returncode == 0:
                    print(f"✅ Docker Compose (legacy): {result.stdout.strip()}")
                else:
                    self.errors.append("Docker Compose not available")
        except FileNotFoundError:
            self.errors.append("Docker Compose not installed")

    def validate_environment(self):
        """Check environment configuration."""
        print("\n🌍 Validating environment...")

        env_file = self.project_root / ".env"
        if env_file.exists():
            print("✅ .env file exists")
        else:
            self.warnings.append(".env file not found - copy from .env.example")

        # Check if .env.example exists
        env_example = self.project_root / ".env.example"
        if env_example.exists():
            print("✅ .env.example exists")
        else:
            self.errors.append(".env.example file missing")

    def validate_scripts_executable(self):
        """Check that scripts are executable."""
        print("\n🔨 Validating script permissions...")

        scripts = [
            "scripts/dev_setup.sh",
            "scripts/test_proxy.py",
            "scripts/monitor_services.sh",
        ]

        for script in scripts:
            script_path = self.project_root / script
            if script_path.exists():
                if os.access(script_path, os.X_OK):
                    print(f"✅ {script} (executable)")
                else:
                    self.warnings.append(
                        f"{script} not executable - run: chmod +x {script}"
                    )
            else:
                self.errors.append(f"Script missing: {script}")

    def run_validation(self):
        """Run all validation checks."""
        print("🔍 Validating Luthien Control setup...\n")

        self.validate_project_structure()
        self.validate_configurations()
        self.validate_python_imports()
        self.validate_dependencies()
        self.validate_docker_setup()
        self.validate_environment()
        self.validate_scripts_executable()

        print("\n" + "=" * 50)
        print("📋 Validation Summary")
        print("=" * 50)

        if self.errors:
            print(f"\n❌ Errors ({len(self.errors)}):")
            for error in self.errors:
                print(f"  • {error}")

        if self.warnings:
            print(f"\n⚠️  Warnings ({len(self.warnings)}):")
            for warning in self.warnings:
                print(f"  • {warning}")

        if not self.errors and not self.warnings:
            print("\n🎉 All validations passed! Your setup is ready.")
            print("\nNext steps:")
            print("  1. Copy .env.example to .env and add your API keys")
            print("  2. Run: ./scripts/dev_setup.sh")
            print("  3. Test: uv run python scripts/test_proxy.py")
        elif not self.errors:
            print("\n✅ Setup is valid with minor warnings.")
            print("Fix the warnings above, then run:")
            print("  ./scripts/dev_setup.sh")
        else:
            print("\n❌ Setup validation failed.")
            print("Fix the errors above before proceeding.")
            return False

        return True


def main():
    """Main validation function."""
    validator = SetupValidator()
    success = validator.run_validation()

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
