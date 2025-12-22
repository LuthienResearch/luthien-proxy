"""Configuration dataclasses for ParallelRulesPolicy.

This module defines the configuration schema for rules-based response evaluation.
Each rule specifies conditions to check, response types to apply to, and how to
handle violations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from luthien_proxy.utils.constants import DEFAULT_JUDGE_MAX_TOKENS


class ResponseType(Enum):
    """Types of LLM responses that rules can apply to."""

    TEXT = "text"
    TOOL_CALL = "tool_call"
    OTHER = "other"

    @classmethod
    def from_string(cls, value: str) -> ResponseType:
        """Convert string to ResponseType, raising ValueError if invalid."""
        try:
            return cls(value.lower())
        except ValueError as e:
            valid = [t.value for t in cls]
            raise ValueError(f"Invalid response type '{value}'. Valid types: {valid}") from e


@dataclass(frozen=True)
class ViolationResponseConfig:
    """Configuration for how to respond when a rule is violated.

    Attributes:
        include_original: Whether to include the original response in violation message
        static_message: Static message to include (may be None)
        include_llm_explanation: Whether to include the LLM judge's explanation
        llm_explanation_template: Template for formatting LLM explanation.
            Available variables: {rule_name}, {explanation}, {probability}
    """

    include_original: bool = False
    static_message: str | None = None
    include_llm_explanation: bool = True
    llm_explanation_template: str = "Rule '{rule_name}' violated: {explanation}"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ViolationResponseConfig:
        """Create ViolationResponseConfig from a dictionary."""
        if data is None:
            return cls()
        return cls(
            include_original=data.get("include_original", False),
            static_message=data.get("static_message"),
            include_llm_explanation=data.get("include_llm_explanation", True),
            llm_explanation_template=data.get(
                "llm_explanation_template",
                "Rule '{rule_name}' violated: {explanation}",
            ),
        )


@dataclass(frozen=True)
class RuleConfig:
    """Configuration for a single rule to evaluate.

    Attributes:
        name: Unique identifier for the rule
        ruletext: The rule description passed to the LLM judge
        response_types: Set of response types this rule applies to
        probability_threshold: Threshold for considering rule violated (overrides default)
        judge_prompt_template: Custom prompt template for this rule (overrides default).
            Available variables: {ruletext}, {content}
        violation_response: Configuration for violation response
    """

    name: str
    ruletext: str
    response_types: frozenset[ResponseType]
    probability_threshold: float | None = None
    judge_prompt_template: str | None = None
    violation_response: ViolationResponseConfig = field(default_factory=ViolationResponseConfig)

    def get_threshold(self, default: float) -> float:
        """Get the probability threshold, falling back to default if not set."""
        return self.probability_threshold if self.probability_threshold is not None else default

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuleConfig:
        """Create RuleConfig from a dictionary.

        Args:
            data: Dictionary with rule configuration

        Returns:
            RuleConfig instance

        Raises:
            ValueError: If required fields are missing or invalid
        """
        if "name" not in data:
            raise ValueError("Rule must have a 'name' field")
        if "ruletext" not in data:
            raise ValueError(f"Rule '{data['name']}' must have a 'ruletext' field")

        # Parse response types
        response_types_raw = data.get("response_types", ["text"])
        if not isinstance(response_types_raw, list):
            raise ValueError(f"Rule '{data['name']}': response_types must be a list")

        response_types = frozenset(ResponseType.from_string(rt) for rt in response_types_raw)

        # Parse violation response
        violation_response = ViolationResponseConfig.from_dict(data.get("violation_response"))

        return cls(
            name=data["name"],
            ruletext=data["ruletext"],
            response_types=response_types,
            probability_threshold=data.get("probability_threshold"),
            judge_prompt_template=data.get("judge_prompt_template"),
            violation_response=violation_response,
        )


@dataclass(frozen=True)
class ParallelRulesJudgeConfig:
    """Configuration for the LLM judge used to evaluate rules.

    Attributes:
        model: LLM model identifier (e.g., "anthropic/claude-3-haiku-20240307")
        api_base: Optional API base URL
        api_key: Optional API key (falls back to env vars)
        temperature: Sampling temperature for judge
        max_tokens: Maximum output tokens for judge response
        probability_threshold: Default threshold for rule violation (0-1)
    """

    model: str
    api_base: str | None = None
    api_key: str | None = None
    temperature: float = 0.0
    max_tokens: int = DEFAULT_JUDGE_MAX_TOKENS
    probability_threshold: float = 0.5

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if not 0.0 <= self.probability_threshold <= 1.0:
            raise ValueError(f"probability_threshold must be between 0 and 1, got {self.probability_threshold}")
        if self.temperature < 0.0:
            raise ValueError(f"temperature must be non-negative, got {self.temperature}")
        if self.max_tokens < 1:
            raise ValueError(f"max_tokens must be positive, got {self.max_tokens}")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParallelRulesJudgeConfig:
        """Create ParallelRulesJudgeConfig from a dictionary.

        Args:
            data: Dictionary with judge configuration

        Returns:
            ParallelRulesJudgeConfig instance

        Raises:
            ValueError: If required fields are missing or invalid
        """
        if "model" not in data:
            raise ValueError("Judge configuration must have a 'model' field")

        return cls(
            model=data["model"],
            api_base=data.get("api_base"),
            api_key=data.get("api_key"),
            temperature=data.get("temperature", 0.0),
            max_tokens=data.get("max_tokens", DEFAULT_JUDGE_MAX_TOKENS),
            probability_threshold=data.get("probability_threshold", 0.5),
        )


@dataclass
class RuleResult:
    """Result from evaluating a single rule."""

    probability: float
    explanation: str
    prompt: list[dict[str, str]]
    response_text: str


@dataclass
class RuleViolation:
    """Represents a rule that was violated.

    Attributes:
        rule: The rule configuration that was violated
        result: The judge result (None if evaluation failed)
        error: The exception if evaluation failed (None if successful)
    """

    rule: RuleConfig
    result: RuleResult | None
    error: Exception | None = None

    @property
    def is_error(self) -> bool:
        """Whether this violation is due to an evaluation error (fail-secure)."""
        return self.error is not None


__all__ = [
    "ResponseType",
    "ViolationResponseConfig",
    "RuleConfig",
    "ParallelRulesJudgeConfig",
    "RuleResult",
    "RuleViolation",
]
