"""Policy loading helpers for the control plane."""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional, cast

import yaml

from luthien_proxy.policies.base import LuthienPolicy
from luthien_proxy.policies.noop import NoOpPolicy
from luthien_proxy.utils.project_config import ProjectConfig
from luthien_proxy.types import JSONObject
from luthien_proxy.control_plane.conversation.utils import json_safe

logger = logging.getLogger(__name__)


def load_policy_from_config(
    config: ProjectConfig,
    config_path: Optional[str] = None,
) -> LuthienPolicy:
    """Load the active policy from YAML config or return `NoOpPolicy`."""
    resolved_path = config_path or config.luthien_policy_config
    if not resolved_path:
        raise RuntimeError("LUTHIEN_POLICY_CONFIG must be set to load a policy")

    def _read(path: str) -> tuple[Optional[str], Optional[JSONObject]]:
        if not os.path.exists(path):
            logger.warning("Policy config not found at %s; using NoOpPolicy", path)
            return None, None
        try:
            with open(path, "r", encoding="utf-8") as file:
                cfg = yaml.safe_load(file) or {}
            policy_ref = cfg.get("policy")
            raw_options = cfg.get("policy_options")
            options: Optional[JSONObject] = None
            if isinstance(raw_options, dict):
                options = {str(k): json_safe(v) for k, v in raw_options.items()}
            return policy_ref, options
        except Exception as exc:
            logger.error("Failed to read policy config %s: %s", path, exc)
            return None, None

    def _import(ref: str) -> tuple[type[LuthienPolicy] | None, str | None, str | None]:
        try:
            module_path, class_name = ref.split(":", 1)
            module = __import__(module_path, fromlist=[class_name])
            cls = getattr(module, class_name)
            if not isinstance(cls, type):
                raise TypeError(f"{class_name} is not a class")
            return cast(type[LuthienPolicy], cls), module_path, class_name
        except Exception as exc:
            logger.error("Failed to import policy '%s': %s", ref, exc)
            return None, None, None

    def _instantiate(cls: type[LuthienPolicy], options: Optional[JSONObject]) -> LuthienPolicy:
        if options is not None:
            try:
                ctor = cast(Callable[..., LuthienPolicy], cls)
                return ctor(options=options)
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
