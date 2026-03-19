"""Config management for ~/.luthien/config.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w

DEFAULT_CONFIG_DIR = Path.home() / ".luthien"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.toml"

SECRET_KEYS = {"api_key", "admin_key"}


@dataclass
class LuthienConfig:
    gateway_url: str = "http://localhost:8000"
    api_key: str | None = None
    admin_key: str | None = None
    repo_path: str | None = None
    mode: str = "local"


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> LuthienConfig:
    """Load config from TOML file. Returns defaults if file doesn't exist."""
    if not path.exists():
        return LuthienConfig()

    with open(path, "rb") as f:
        data = tomllib.load(f)

    gateway = data.get("gateway", {})
    local = data.get("local", {})

    return LuthienConfig(
        gateway_url=gateway.get("url", "http://localhost:8000"),
        api_key=gateway.get("api_key"),
        admin_key=gateway.get("admin_key"),
        repo_path=local.get("repo_path"),
        mode=local.get("mode", "local"),
    )


def save_config(config: LuthienConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Save config to TOML file. Creates parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    data: dict = {
        "gateway": {
            "url": config.gateway_url,
        },
        "local": {},
    }
    if config.api_key:
        data["gateway"]["api_key"] = config.api_key
    if config.admin_key:
        data["gateway"]["admin_key"] = config.admin_key
    if config.repo_path:
        data["local"]["repo_path"] = config.repo_path
    if config.mode:
        data["local"]["mode"] = config.mode

    with open(path, "wb") as f:
        tomli_w.dump(data, f)
    os.chmod(path, 0o600)
