"""Tests for typed request state helpers on PolicyContext."""

from dataclasses import dataclass, field

import pytest

from luthien_proxy.policy_core import PolicyContext, StateSlot


@dataclass
class _State:
    values: dict[int, str] = field(default_factory=dict)


_SLOT: StateSlot[_State] = StateSlot(
    name="test.state_slot",
    expected_type=_State,
    factory=_State,
)


class TestPolicyContextStateSlot:
    def test_get_state_creates_and_reuses_typed_state(self):
        ctx = PolicyContext.for_testing()
        state_a = ctx.get_state(_SLOT)
        state_a.values[1] = "hello"

        state_b = ctx.get_state(_SLOT)
        assert state_b is state_a
        assert state_b.values[1] == "hello"

    def test_pop_state_removes_slot(self):
        ctx = PolicyContext.for_testing()
        ctx.get_state(_SLOT).values[1] = "hello"

        popped = ctx.pop_state(_SLOT)
        assert popped is not None
        assert popped.values[1] == "hello"
        assert ctx.pop_state(_SLOT) is None

    def test_get_state_raises_if_slot_type_mismatch(self):
        ctx = PolicyContext.for_testing()
        ctx.scratchpad[_SLOT.name] = {"unexpected": "dict"}

        with pytest.raises(TypeError, match="expected _State, got dict"):
            ctx.get_state(_SLOT)
