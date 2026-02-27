"""Unit tests for policy composition."""

from __future__ import annotations

from luthien_proxy.policies.multi_serial_policy import MultiSerialPolicy
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_composition import compose_policy


class TestComposeTwoSinglePolicies:
    """Composing two single (non-Multi) policies creates a MultiSerialPolicy."""

    def test_creates_multi_serial(self):
        a, b = NoOpPolicy(), NoOpPolicy()
        result = compose_policy(a, b)

        assert isinstance(result, MultiSerialPolicy)
        assert list(result._sub_policies) == [a, b]

    def test_default_position_appends(self):
        a, b = NoOpPolicy(), NoOpPolicy()
        result = compose_policy(a, b)

        assert result._sub_policies[-1] is b

    def test_position_zero_prepends(self):
        a, b = NoOpPolicy(), NoOpPolicy()
        result = compose_policy(a, b, position=0)

        assert result._sub_policies[0] is b
        assert result._sub_policies[1] is a


class TestComposeIntoMultiSerial:
    """Composing into an existing MultiSerialPolicy inserts into its chain."""

    def test_inserts_into_existing_chain(self):
        p1, p2 = NoOpPolicy(), NoOpPolicy()
        multi = MultiSerialPolicy.from_instances([p1, p2])
        additional = NoOpPolicy()

        result = compose_policy(multi, additional)

        assert isinstance(result, MultiSerialPolicy)
        assert len(result._sub_policies) == 3
        assert result._sub_policies[2] is additional

    def test_prepend_into_chain(self):
        p1, p2 = NoOpPolicy(), NoOpPolicy()
        multi = MultiSerialPolicy.from_instances([p1, p2])
        additional = NoOpPolicy()

        result = compose_policy(multi, additional, position=0)

        assert result._sub_policies[0] is additional
        assert result._sub_policies[1] is p1
        assert result._sub_policies[2] is p2

    def test_insert_middle(self):
        p1, p2 = NoOpPolicy(), NoOpPolicy()
        multi = MultiSerialPolicy.from_instances([p1, p2])
        additional = NoOpPolicy()

        result = compose_policy(multi, additional, position=1)

        assert result._sub_policies[0] is p1
        assert result._sub_policies[1] is additional
        assert result._sub_policies[2] is p2

    def test_does_not_mutate_original(self):
        p1, p2 = NoOpPolicy(), NoOpPolicy()
        multi = MultiSerialPolicy.from_instances([p1, p2])

        compose_policy(multi, NoOpPolicy())

        assert len(multi._sub_policies) == 2


class TestComposePositionEdgeCases:
    """Position values beyond chain length use list.insert() semantics."""

    def test_large_position_appends(self):
        a, b = NoOpPolicy(), NoOpPolicy()
        result = compose_policy(a, b, position=999)

        assert result._sub_policies[-1] is b

    def test_negative_position(self):
        p1, p2 = NoOpPolicy(), NoOpPolicy()
        multi = MultiSerialPolicy.from_instances([p1, p2])
        additional = NoOpPolicy()

        result = compose_policy(multi, additional, position=-1)

        # list.insert(-1, x) inserts before the last element
        assert result._sub_policies[1] is additional
        assert result._sub_policies[2] is p2
