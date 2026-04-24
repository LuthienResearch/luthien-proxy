"""Tests for the `InferenceProvider` abstract base + error hierarchy."""

from __future__ import annotations

import pytest

from luthien_proxy.inference.base import (
    InferenceCredentialOverrideUnsupported,
    InferenceError,
    InferenceInvalidCredentialError,
    InferenceProvider,
    InferenceProviderError,
    InferenceTimeoutError,
)


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

        class _StubProvider(InferenceProvider):
            backend_type = "stub"

            async def complete(self, messages, **kwargs):  # type: ignore[override]
                return ""

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
        ],
    )
    def test_subclass_of_inference_error(self, cls):
        """Each concrete error is catchable by the base class."""
        exc = cls("msg")
        assert isinstance(exc, InferenceError)

    def test_close_is_noop_by_default(self):
        """Default `close()` returns None without side effects."""
        import asyncio

        class _StubProvider(InferenceProvider):
            backend_type = "stub"

            async def complete(self, messages, **kwargs):  # type: ignore[override]
                return ""

        provider = _StubProvider(name="judge")
        assert asyncio.run(provider.close()) is None
