"""Tests for framework-owned typed request state helpers on PolicyContext."""

from dataclasses import dataclass, field
from typing import Any, cast

import pytest

from luthien_proxy.policy_core import PolicyContext


@dataclass
class _State:
    values: dict[int, str] = field(default_factory=dict)


class _Owner:
    pass


class TestPolicyContextPolicyState:
    def test_get_policy_state_creates_and_reuses_by_owner_and_type(self):
        ctx = PolicyContext.for_testing()
        owner = _Owner()

        state_a = ctx.get_policy_state(owner, _State, _State)
        state_a.values[1] = "hello"
        state_b = ctx.get_policy_state(owner, _State, _State)

        assert state_b is state_a
        assert state_b.values[1] == "hello"

    def test_get_policy_state_isolated_between_owners(self):
        ctx = PolicyContext.for_testing()
        owner_a = _Owner()
        owner_b = _Owner()

        state_a = ctx.get_policy_state(owner_a, _State, _State)
        state_b = ctx.get_policy_state(owner_b, _State, _State)
        state_a.values[1] = "hello"

        assert state_a is not state_b
        assert state_b.values == {}

    def test_pop_policy_state_removes_owned_state(self):
        ctx = PolicyContext.for_testing()
        owner = _Owner()
        ctx.get_policy_state(owner, _State, _State).values[1] = "hello"

        popped = ctx.pop_policy_state(owner, _State)
        assert popped is not None
        assert popped.values[1] == "hello"
        assert ctx.pop_policy_state(owner, _State) is None

    def test_get_policy_state_raises_if_stored_type_mismatch(self):
        ctx = PolicyContext.for_testing()
        owner = _Owner()
        cast(Any, ctx)._policy_state[(id(owner), _State)] = {"unexpected": "dict"}

        with pytest.raises(TypeError, match="expected _State, got dict"):
            ctx.get_policy_state(owner, _State, _State)

    def test_get_policy_state_raises_if_factory_returns_wrong_type(self):
        ctx = PolicyContext.for_testing()
        owner = _Owner()

        with pytest.raises(TypeError, match="returned dict, expected _State"):
            ctx.get_policy_state(owner, _State, lambda: cast(Any, {}))
