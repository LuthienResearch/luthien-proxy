"""Policy configuration loading.

Loads policy configuration from YAML files with the format:

```yaml
policy:
  class: "luthien_proxy.policies.simple_policy:SimplePolicy"
  config: {}
```

Or with config parameters:

```yaml
policy:
  class: "luthien_proxy.policies.all_caps:AllCapsPolicy"
  config:
    enabled: true
```
"""

from __future__ import annotations

import logging
from typing import Any, cast

import yaml

from luthien_proxy.policy_core.base_policy import BasePolicy
from luthien_proxy.policy_core.policy_protocol import PolicyProtocol
from luthien_proxy.settings import get_settings

logger = logging.getLogger(__name__)


def load_policy_from_yaml(config_path: str | None = None) -> PolicyProtocol:
    """Load a policy from YAML configuration file.

    Args:
        config_path: Path to YAML config file. If None, uses settings.policy_config.

    Returns:
        Instantiated policy object

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config is missing required fields or has invalid structure
        ImportError: If policy module cannot be imported
        yaml.YAMLError: If YAML syntax is invalid
    """
    # Determine config path
    if config_path is None:
        config_path = get_settings().policy_config

    # Read YAML file
    try:
        with open(config_path, "r", encoding="utf-8") as file:
            cfg = yaml.safe_load(file) or {}

        # Validate policy section exists
        policy_section = cfg.get("policy")
        if not isinstance(policy_section, dict):
            raise ValueError(
                f"Config at {config_path} must contain a 'policy' section as a dictionary. "
                f"Found: {type(policy_section).__name__ if policy_section is not None else 'None'}"
            )

        # Validate class reference exists and is a string
        policy_class_ref = policy_section.get("class")
        if not isinstance(policy_class_ref, str):
            raise ValueError(
                f"Policy section in {config_path} must contain a 'class' field as a string. "
                f"Found: {type(policy_class_ref).__name__ if policy_class_ref is not None else 'None'}"
            )

        policy_config = policy_section.get("config", {})

        # Import policy class (note: we can't use issubclass() with Protocol that has properties)
        policy_class = _import_policy_class(policy_class_ref)

        # Instantiate and return policy
        policy = _instantiate_policy(policy_class, policy_config)
        return policy
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Policy config not found at {config_path} (path set with POLICY_CONFIG env var or passed explicitly)"
        )
    except Exception as exc:
        logger.error(f"Failed to load policy config {config_path}: {exc}")
        raise exc


def _import_policy_class(class_ref: str) -> type[PolicyProtocol]:
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

    if not issubclass(cls, BasePolicy):
        raise TypeError(f"{class_name} does not inherit from BasePolicy")

    return cast(type[PolicyProtocol], cls)


def _instantiate_policy(policy_class: type[PolicyProtocol], config: dict[str, Any]) -> PolicyProtocol:
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
