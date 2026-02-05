"""Sample policy demonstrating Pydantic config models for dynamic form generation.

This policy does nothing but serves as an example for the dynamic form generation
system. It shows:
- Basic types with constraints (threshold with min/max)
- Password fields (api_key)
- Discriminated unions (rules with type selector)
- Nested objects and arrays
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from luthien_proxy.policy_core.base_policy import BasePolicy


class RegexRuleConfig(BaseModel):
    """Rule that matches content against a regex pattern."""

    type: Literal["regex"] = "regex"
    pattern: str = Field(description="Regular expression pattern to match")
    case_sensitive: bool = Field(default=False, description="Whether matching is case-sensitive")


class KeywordRuleConfig(BaseModel):
    """Rule that matches content against a list of keywords."""

    type: Literal["keyword"] = "keyword"
    keywords: list[str] = Field(description="Keywords to detect in content")


RuleConfig = Annotated[RegexRuleConfig | KeywordRuleConfig, Field(discriminator="type")]


class SampleConfig(BaseModel):
    """Configuration for the sample policy."""

    name: str = Field(default="default", description="Name for this policy instance")
    enabled: bool = Field(default=True, description="Whether the policy is active")
    threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="Detection threshold (0-1)")
    api_key: str | None = Field(default=None, json_schema_extra={"format": "password"})
    rules: list[RuleConfig] = Field(default_factory=list, description="List of detection rules")


class SamplePydanticPolicy(BasePolicy):
    """Sample policy demonstrating Pydantic-based configuration.

    This policy does nothing but serves as an example for the dynamic
    form generation system. It shows:
    - Basic types with constraints (threshold with min/max)
    - Password fields (api_key)
    - Discriminated unions (rules with type selector)
    - Nested objects and arrays
    """

    def __init__(self, config: SampleConfig | None = None):
        """Initialize the policy with optional config.

        Args:
            config: A SampleConfig instance or None for defaults.
                   Also accepts a dict at runtime which will be parsed into SampleConfig.
        """
        if config is None:
            self.config = SampleConfig()
        elif isinstance(config, dict):
            # Handle dict passed from policy manager at runtime
            self.config = SampleConfig.model_validate(config)
        else:
            self.config = config

    def get_config(self) -> dict:
        """Return the configuration for this policy instance."""
        return {"config": self.config.model_dump()}


__all__ = [
    "SamplePydanticPolicy",
    "SampleConfig",
    "RuleConfig",
    "RegexRuleConfig",
    "KeywordRuleConfig",
]
