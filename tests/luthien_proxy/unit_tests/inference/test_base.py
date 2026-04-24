"""Tests for the `InferenceProvider` abstract base, result type, and error hierarchy."""

from __future__ import annotations

import json

import pytest

from luthien_proxy.inference.base import (
    InferenceCredentialOverrideUnsupported,
    InferenceError,
    InferenceInvalidCredentialError,
    InferenceProvider,
    InferenceProviderError,
    InferenceResult,
    InferenceStructuredOutputError,
    InferenceTimeoutError,
    extract_schema,
)


class _StubProvider(InferenceProvider):
    backend_type = "stub"

    async def complete(self, messages, **kwargs):  # type: ignore[override]
        return InferenceResult.from_text("")


class TestAbstractContract:
    """The abstract class can't be instantiated and documents its subclass hooks."""

    def test_cannot_instantiate_abstract_base(self):
        """InferenceProvider is abstract — instantiating raises TypeError."""
        with pytest.raises(TypeError):
            InferenceProvider(name="x")  # type: ignore[abstract]

    def test_subclass_must_implement_complete(self):
        """A subclass that skips `complete()` is still abstract."""

        class IncompleteProvider(InferenceProvider):
            pass

        with pytest.raises(TypeError):
            IncompleteProvider(name="x")  # type: ignore[abstract]

    def test_repr_does_not_leak_state(self):
        """`repr(provider)` includes name + backend_type, no secrets."""
        provider = _StubProvider(name="judge")
        repr_str = repr(provider)
        assert "judge" in repr_str
        assert "stub" in repr_str


class TestErrorHierarchy:
    """All inference-specific errors inherit from `InferenceError`."""

    @pytest.mark.parametrize(
        "cls",
        [
            InferenceProviderError,
            InferenceInvalidCredentialError,
            InferenceTimeoutError,
            InferenceCredentialOverrideUnsupported,
            InferenceStructuredOutputError,
        ],
    )
    def test_subclass_of_inference_error(self, cls):
        """Each concrete error is catchable by the base class."""
        exc = cls("msg")
        assert isinstance(exc, InferenceError)

    @pytest.mark.asyncio
    async def test_close_is_noop_by_default(self):
        """Default `close()` returns None without side effects."""
        provider = _StubProvider(name="judge")
        assert await provider.close() is None


class TestInferenceResult:
    """Result dataclass constructors and invariants."""

    def test_from_text_leaves_structured_none(self):
        """`from_text` populates text only."""
        result = InferenceResult.from_text("hello")
        assert result.text == "hello"
        assert result.structured is None

    def test_from_structured_serializes_text(self):
        """`from_structured` stringifies the dict so `.text` is always usable."""
        payload = {"city": "Paris", "population": 2_161_000}
        result = InferenceResult.from_structured(payload)
        assert result.structured == payload
        # Non-aware callers reading .text get JSON they can still parse.
        assert json.loads(result.text) == payload

    def test_result_is_immutable(self):
        """Frozen dataclass — attributes can't be reassigned."""
        result = InferenceResult.from_text("x")
        with pytest.raises(Exception):
            result.text = "y"  # type: ignore[misc]


class TestExtractSchema:
    """`extract_schema` normalizes the various response_format shapes."""

    @pytest.mark.parametrize(
        "response_format,expected",
        [
            (None, None),
            ({"type": "json_object"}, None),
            ({"type": "json_schema", "schema": {"type": "object"}}, {"type": "object"}),
            ({"type": "json_schema"}, None),  # missing schema key
            ({"type": "json_schema", "schema": "not-a-dict"}, None),
            ({"type": "unknown"}, None),
        ],
    )
    def test_extract_schema(self, response_format, expected):
        """Pulls the schema out only for the `json_schema` shape with a dict schema."""
        assert extract_schema(response_format) == expected
