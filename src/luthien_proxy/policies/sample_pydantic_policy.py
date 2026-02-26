"""Sample policy demonstrating Pydantic config models for dynamic form generation.

This is a working no-op policy that passes through all requests unchanged.
It serves as an example for the dynamic form generation system, showing:
- Basic types with constraints (threshold with min/max)
- Password fields (api_key)
- Discriminated unions (rules with type selector)
- Nested objects and arrays
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from luthien_proxy.policies.noop_policy import NoOpPolicy


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


class SamplePydanticPolicy(NoOpPolicy):
    """Sample policy demonstrating Pydantic-based configuration.

    Inherits all passthrough behavior from NoOpPolicy. The interesting part
    is the SampleConfig model above, which demonstrates dynamic form generation.
    """

    @property
    def short_policy_name(self) -> str:
        """Use class name (BasePolicy default), not NoOpPolicy's 'NoOp'."""
        return type(self).__name__

    def __init__(self, config: SampleConfig | None = None):
        """Initialize the policy with optional config.

        Args:
            config: A SampleConfig instance or None for defaults.
                   Also accepts a dict at runtime which will be parsed into SampleConfig.
        """
        self.config = self._init_config(config, SampleConfig)

    # get_config() is inherited from BasePolicy - automatically serializes
    # the self.config Pydantic model


__all__ = [
    "SamplePydanticPolicy",
    "SampleConfig",
    "RuleConfig",
    "RegexRuleConfig",
    "KeywordRuleConfig",
]
