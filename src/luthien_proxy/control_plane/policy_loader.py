"""Policy loading helpers for the control plane."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional, cast

import yaml

from luthien_proxy.policies.base import LuthienPolicy
from luthien_proxy.policies.noop import NoOpPolicy
from luthien_proxy.utils.project_config import ProjectConfig

logger = logging.getLogger(__name__)


def load_policy_from_config(
    config: ProjectConfig,
    config_path: Optional[str] = None,
) -> LuthienPolicy:
    """Load the active policy from YAML config or return `NoOpPolicy`."""
    resolved_path = config_path or config.luthien_policy_config
    if not resolved_path:
        raise RuntimeError("LUTHIEN_POLICY_CONFIG must be set to load a policy")

    def _read(path: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
        if not os.path.exists(path):
            logger.warning("Policy config not found at %s; using NoOpPolicy", path)
            return None, None
        try:
            with open(path, "r", encoding="utf-8") as file:
                cfg = yaml.safe_load(file) or {}
            return cfg.get("policy"), (cfg.get("policy_options") or None)
        except Exception as exc:
            logger.error("Failed to read policy config %s: %s", path, exc)
            return None, None

    def _import(ref: str):
        try:
            module_path, class_name = ref.split(":", 1)
            module = __import__(module_path, fromlist=[class_name])
            cls = getattr(module, class_name)
            return cls, module_path, class_name
        except Exception as exc:
            logger.error("Failed to import policy '%s': %s", ref, exc)
            return None, None, None

    def _instantiate(cls, options: Optional[dict[str, Any]]) -> LuthienPolicy:
        if options is not None:
            try:
                return cast(Any, cls)(options=options)
            except TypeError:
                pass
        return cls()

    policy_ref, policy_options = _read(resolved_path)
    if not policy_ref:
        logger.info("No policy specified in config; using NoOpPolicy")
        return NoOpPolicy()

    cls, module_path, class_name = _import(policy_ref)
    if not cls or not module_path or not class_name:
        return NoOpPolicy()
    if not issubclass(cls, LuthienPolicy):
        logger.warning(
            "Configured policy %s does not subclass LuthienPolicy; using NoOpPolicy",
            class_name,
        )
        return NoOpPolicy()

    logger.info("Loaded policy from config: %s (%s)", class_name, module_path)
    return _instantiate(cls, policy_options)


__all__ = ["load_policy_from_config"]
