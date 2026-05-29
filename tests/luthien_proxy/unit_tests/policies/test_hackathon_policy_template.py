"""Unit tests for HackathonPolicy template — verify it imports and passes through cleanly."""

from __future__ import annotations

import pytest
from tests.luthien_proxy.fixtures.policy_context import make_policy_context

from luthien_proxy.policies.hackathon_policy_template import HackathonPolicy
from luthien_proxy.policies.simple_policy import SimplePolicy


@pytest.fixture
def policy():
    return HackathonPolicy()


@pytest.fixture
def context():
    return make_policy_context()


class TestTemplate:
    def test_inherits_simple_policy(self, policy):
        assert isinstance(policy, SimplePolicy)

    @pytest.mark.asyncio
    async def test_request_passthrough(self, policy, context):
        result = await policy.simple_on_request("hello world", context)
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_response_passthrough(self, policy, context):
        result = await policy.simple_on_response_content("response text", context)
        assert result == "response text"

    @pytest.mark.asyncio
    async def test_tool_call_passthrough(self, policy, context):
        tool_call = {
            "type": "tool_use",
            "id": "test-id",
            "name": "bash",
            "input": {"command": "ls"},
        }
        result = await policy.simple_on_anthropic_tool_call(tool_call, context)
        assert result == tool_call
