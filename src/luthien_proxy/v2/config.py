# ABOUTME: V2 configuration loading - policy instantiation from YAML
# ABOUTME: Loads policy class and config from YAML file specified by V2_POLICY_CONFIG

"""Policy configuration loading for V2 architecture.

Loads policy configuration from YAML files with the format:

```yaml
policy:
  class: "luthien_proxy.v2.policies.simple_policy:SimplePolicy"
  config: {}
```

Or with config parameters:

```yaml
policy:
  class: "luthien_proxy.v2.policies.all_caps:AllCapsPolicy"
  config:
    enabled: true
```
"""

from __future__ import annotations

import logging
import os
from typing import Any, cast

import yaml

from luthien_proxy.v2.policies.policy import Policy
from luthien_proxy.v2.policies.simple_policy import SimplePolicy

logger = logging.getLogger(__name__)


def load_policy_from_yaml(config_path: str | None = None) -> Policy:
    """Load a policy from YAML configuration file.

    Args:
        config_path: Path to YAML config file. If None, uses V2_POLICY_CONFIG env var.
                    Defaults to config/v2_config.yaml if env var not set.

    Returns:
        Instantiated policy object (defaults to SimplePolicy if config missing or invalid)
    """
    # Determine config path
    if config_path is None:
        config_path = os.getenv("V2_POLICY_CONFIG", "config/v2_config.yaml")

    # Read YAML file
    if not os.path.exists(config_path):
        logger.warning(f"Policy config not found at {config_path}; using SimplePolicy")
        return SimplePolicy()

    try:
        with open(config_path, "r", encoding="utf-8") as file:
            cfg = yaml.safe_load(file) or {}
    except Exception as exc:
        logger.error(f"Failed to read policy config {config_path}: {exc}")
        return SimplePolicy()

    # Extract policy section
    policy_section = cfg.get("policy")
    if not isinstance(policy_section, dict):
        logger.warning(f"No valid 'policy' section in {config_path}; using SimplePolicy")
        return SimplePolicy()

    policy_class_ref = policy_section.get("class")
    policy_config = policy_section.get("config", {})

    if not policy_class_ref:
        logger.warning(f"No 'class' specified in policy section of {config_path}; using SimplePolicy")
        return SimplePolicy()

    # Import policy class
    try:
        policy_class = _import_policy_class(policy_class_ref)
    except Exception as exc:
        logger.error(f"Failed to import policy '{policy_class_ref}': {exc}")
        return SimplePolicy()

    # Validate it's a Policy subclass
    if not issubclass(policy_class, Policy):
        logger.warning(f"Policy class {policy_class_ref} does not subclass Policy; using SimplePolicy")
        return SimplePolicy()

    # Instantiate policy
    try:
        policy = _instantiate_policy(policy_class, policy_config)
        logger.info(f"Loaded policy from {config_path}: {policy_class.__name__}")
        return policy
    except Exception as exc:
        logger.error(f"Failed to instantiate policy {policy_class_ref} with config {policy_config}: {exc}")
        return SimplePolicy()


def _import_policy_class(class_ref: str) -> type[Policy]:
    """Import a policy class from a module:class reference.

    Args:
        class_ref: String like "module.path:ClassName"

    Returns:
        Policy class

    Raises:
        ValueError: If class_ref format is invalid
        ImportError: If module cannot be imported
        AttributeError: If class doesn't exist in module
        TypeError: If the reference is not a class
    """
    if ":" not in class_ref:
        raise ValueError(f"Policy class reference must be in format 'module.path:ClassName', got: {class_ref}")

    module_path, class_name = class_ref.split(":", 1)

    # Import module
    module = __import__(module_path, fromlist=[class_name])

    # Get class from module
    cls = getattr(module, class_name)

    # Validate it's a class
    if not isinstance(cls, type):
        raise TypeError(f"{class_name} is not a class")

    return cast(type[Policy], cls)


def _instantiate_policy(policy_class: type[Policy], config: dict[str, Any]) -> Policy:
    """Instantiate a policy with the given config.

    Args:
        policy_class: Policy class to instantiate
        config: Configuration dictionary (will be passed as **kwargs)

    Returns:
        Instantiated policy

    Raises:
        TypeError: If config parameters don't match policy constructor
    """
    if config:
        return policy_class(**config)
    else:
        return policy_class()


__all__ = ["load_policy_from_yaml"]
