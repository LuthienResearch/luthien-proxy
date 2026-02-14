"""Policy discovery module for auto-discovering available policies.

Scans the luthien_proxy.policies package to find policy classes and extract
their metadata including config schemas from constructor signatures.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
import types
from typing import Annotated, Any, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel, TypeAdapter

import luthien_proxy.policies as policies_package
from luthien_proxy.policy_core.base_policy import BasePolicy

logger = logging.getLogger(__name__)

# Modules to skip when discovering policies
SKIP_MODULES = frozenset(
    {
        "__init__",
        "base_policy",
        "simple_policy",
    }
)

# Suffixes to skip
SKIP_SUFFIXES = ("_config", "_utils")


def python_type_to_json_schema(python_type: Any) -> dict[str, Any]:
    """Convert a Python type hint to a JSON Schema type definition.

    Args:
        python_type: A Python type annotation (e.g., str, int, list[str], dict[str, Any])

    Returns:
        A JSON Schema type definition dict
    """
    # Handle Pydantic models - extract full schema
    if isinstance(python_type, type):
        try:
            if issubclass(python_type, BaseModel):
                return python_type.model_json_schema()
        except TypeError:
            pass

    # Handle Annotated types (may contain discriminated unions)
    origin = get_origin(python_type)
    if origin is Annotated:
        args = get_args(python_type)
        if args:
            base_type = args[0]
            base_origin = get_origin(base_type)
            # Check if it's a Union with Pydantic models (discriminated union)
            if base_origin is Union or base_origin is types.UnionType:
                union_args = get_args(base_type)
                if all(isinstance(a, type) and issubclass(a, BaseModel) for a in union_args):
                    # Use TypeAdapter to generate proper discriminated union schema
                    adapter = TypeAdapter(python_type)
                    return adapter.json_schema()
            # Not a discriminated union, handle base type
            return python_type_to_json_schema(base_type)

    if python_type is inspect.Parameter.empty:
        return {"type": "string"}

    # Re-compute origin/args in case we didn't go through the Annotated branch above
    if origin is None:
        origin = get_origin(python_type)
    args = get_args(python_type)

    # Handle Union types (e.g., str | None, Union[str, None])
    # Python 3.10+ uses types.UnionType for | syntax, older uses typing.Union
    if origin is Union or origin is types.UnionType:
        non_none_types = [a for a in args if a is not type(None)]
        if len(non_none_types) == 1:
            schema = python_type_to_json_schema(non_none_types[0])
            schema["nullable"] = True
            return schema
        # Multiple non-None types - fall back to any
        return {"type": "string", "description": f"Union type: {python_type}"}

    # Handle basic types
    type_map: dict[Any, dict[str, str]] = {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
    }

    if python_type in type_map:
        return type_map[python_type].copy()

    # Handle parameterized list
    if origin is list:
        if args:
            items_schema = python_type_to_json_schema(args[0])
            return {"type": "array", "items": items_schema}
        return {"type": "array"}

    # Handle parameterized dict
    if origin is dict:
        return {"type": "object", "additionalProperties": True}

    # Handle bare list and dict
    if python_type is list:
        return {"type": "array"}
    if python_type is dict:
        return {"type": "object", "additionalProperties": True}

    # Fallback
    return {"type": "string", "description": f"Python type: {python_type}"}


def extract_config_schema(policy_class: type) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract config schema and example config from a policy class constructor.

    Args:
        policy_class: The policy class to extract schema from

    Returns:
        Tuple of (config_schema, example_config)
    """
    config_schema: dict[str, Any] = {}
    example_config: dict[str, Any] = {}

    try:
        sig = inspect.signature(policy_class.__init__)
    except (ValueError, TypeError):
        return config_schema, example_config

    # Use get_type_hints to resolve string annotations (from __future__ annotations)
    try:
        type_hints = get_type_hints(policy_class.__init__)
    except Exception:
        # Fall back to empty hints if resolution fails
        type_hints = {}

    for param_name, param in sig.parameters.items():
        # Skip self and *args/**kwargs
        if param_name == "self":
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        # Get the resolved type hint, falling back to param.annotation
        annotation = type_hints.get(param_name, param.annotation)

        # Build schema for this parameter
        param_schema = python_type_to_json_schema(annotation)

        # Add default if present
        if param.default is not inspect.Parameter.empty:
            param_schema["default"] = param.default
            # Use default as example value
            example_config[param_name] = param.default
        else:
            # No default - mark as required (by not having default)
            # Provide a placeholder example based on type
            example_config[param_name] = _get_example_value(param_schema)

        config_schema[param_name] = param_schema

    return config_schema, example_config


def _get_example_value(schema: dict[str, Any]) -> Any:
    """Generate an example value based on a JSON schema type."""
    schema_type = schema.get("type", "string")

    if schema_type == "string":
        return ""
    elif schema_type == "integer":
        return 0
    elif schema_type == "number":
        return 0.0
    elif schema_type == "boolean":
        return False
    elif schema_type == "array":
        return []
    elif schema_type == "object":
        return {}
    return None


def validate_policy_config(policy_class: type, config: dict[str, Any]) -> dict[str, Any]:
    """Validate config against a policy class constructor and return validated config.

    For Pydantic model parameters, performs full Pydantic validation.
    For other types, performs basic type checking.

    Args:
        policy_class: The policy class to validate against
        config: The config dict to validate

    Returns:
        Validated config dict (with Pydantic models converted to dicts)

    Raises:
        ValueError: If a required parameter is missing
        ValidationError: If Pydantic model validation fails
    """
    try:
        sig = inspect.signature(policy_class.__init__)
    except (ValueError, TypeError):
        return config

    try:
        type_hints = get_type_hints(policy_class.__init__)
    except Exception:
        type_hints = {}

    validated_config: dict[str, Any] = {}

    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        # Get value from config, or use default
        if param_name in config:
            value = config[param_name]
        elif param.default is not inspect.Parameter.empty:
            value = param.default
            validated_config[param_name] = value
            continue
        else:
            raise ValueError(f"Required parameter '{param_name}' is missing from config")

        annotation = type_hints.get(param_name, param.annotation)

        # Handle Pydantic models
        if _is_pydantic_model_type(annotation):
            model_class = _extract_pydantic_model(annotation)
            if model_class and value is not None:
                if isinstance(value, dict):
                    # Validate dict against Pydantic model
                    validated = model_class.model_validate(value)
                    validated_config[param_name] = validated.model_dump()
                elif isinstance(value, BaseModel):
                    validated_config[param_name] = value.model_dump()
                else:
                    validated_config[param_name] = value
            else:
                validated_config[param_name] = value
        else:
            validated_config[param_name] = value

    return validated_config


def _is_pydantic_model_type(annotation: Any) -> bool:
    """Check if an annotation is or contains a Pydantic model."""
    if annotation is inspect.Parameter.empty:
        return False

    # Direct Pydantic model
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return True

    # Check Union types (e.g., SampleConfig | None)
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        args = get_args(annotation)
        return any(_is_pydantic_model_type(arg) for arg in args if arg is not type(None))

    return False


def _extract_pydantic_model(annotation: Any) -> type[BaseModel] | None:
    """Extract the Pydantic model class from an annotation."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation

    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        args = get_args(annotation)
        for arg in args:
            if arg is not type(None) and isinstance(arg, type) and issubclass(arg, BaseModel):
                return arg

    return None


def extract_description(policy_class: type) -> str:
    """Extract description from a policy class docstring.

    Args:
        policy_class: The policy class to extract description from

    Returns:
        Description string, or empty string if no docstring
    """
    if policy_class.__doc__:
        # Take the first paragraph (up to double newline or end)
        doc = policy_class.__doc__.strip()
        first_para = doc.split("\n\n")[0]
        # Clean up whitespace
        lines = [line.strip() for line in first_para.split("\n")]
        return " ".join(lines)
    return ""


_discovered_policies_cache: list[dict[str, Any]] | None = None


def discover_policies() -> list[dict[str, Any]]:
    """Discover all policy classes in the luthien_proxy.policies package.

    Results are cached since the policy set is static at runtime.

    Returns:
        List of policy info dicts with keys: name, class_ref, description,
        config_schema, example_config
    """
    global _discovered_policies_cache
    if _discovered_policies_cache is not None:
        return _discovered_policies_cache

    policies: list[dict[str, Any]] = []

    try:
        package_path = policies_package.__path__
    except AttributeError as e:
        logger.error(f"Failed to get policies package path: {e}")
        return policies

    for module_info in pkgutil.iter_modules(package_path):
        module_name = module_info.name

        # Skip non-policy modules
        if module_name in SKIP_MODULES:
            continue
        if any(module_name.endswith(suffix) for suffix in SKIP_SUFFIXES):
            continue

        try:
            module = importlib.import_module(f"luthien_proxy.policies.{module_name}")
        except ImportError as e:
            logger.warning(f"Failed to import module {module_name}: {e}")
            continue

        # Find policy classes in this module
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue

            attr = getattr(module, attr_name)

            # Check if it's a class defined in this module
            if not isinstance(attr, type):
                continue
            if attr.__module__ != f"luthien_proxy.policies.{module_name}":
                continue

            # Check if it's a subclass of BasePolicy (but not BasePolicy itself)
            if not (issubclass(attr, BasePolicy) and attr is not BasePolicy):
                continue

            # Skip base classes meant to be subclassed
            if attr_name == "SimplePolicy":
                continue

            # Extract metadata
            class_ref = f"luthien_proxy.policies.{module_name}:{attr_name}"
            description = extract_description(attr)
            config_schema, example_config = extract_config_schema(attr)

            policies.append(
                {
                    "name": attr_name,
                    "class_ref": class_ref,
                    "description": description,
                    "config_schema": config_schema,
                    "example_config": example_config,
                }
            )

    # Sort by name for consistent ordering
    policies.sort(key=lambda p: p["name"])

    _discovered_policies_cache = policies
    return policies
