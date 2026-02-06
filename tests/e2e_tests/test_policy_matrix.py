# ABOUTME: Parameterized e2e tests that verify each policy works through the gateway
# ABOUTME: Tests both OpenAI and Anthropic paths with standard configs for each policy

"""E2E tests for all policy types.

Runs a simple request through the gateway for each policy to verify:
1. Policy can be activated via admin API
2. Request/response flow works for both OpenAI and Anthropic clients
3. No crashes or unexpected errors
"""

import pytest
from tests.e2e_tests.conftest import API_KEY, GATEWAY_URL, policy_context

# Policy configurations: (policy_class_ref, config, description)
POLICY_CONFIGS = [
    pytest.param(
        "luthien_proxy.policies.noop_policy:NoOpPolicy",
        {},
        id="NoOpPolicy",
    ),
    pytest.param(
        "luthien_proxy.policies.all_caps_policy:AllCapsPolicy",
        {},
        id="AllCapsPolicy",
    ),
    pytest.param(
        "luthien_proxy.policies.debug_logging_policy:DebugLoggingPolicy",
        {},
        id="DebugLoggingPolicy",
    ),
    pytest.param(
        "luthien_proxy.policies.simple_policy:SimplePolicy",
        {},
        id="SimplePolicy",
    ),
    pytest.param(
        "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy",
        {"replacements": {"hello": "hi"}},
        id="StringReplacementPolicy",
    ),
    pytest.param(
        "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy",
        {"probability_threshold": 0.5},
        id="ToolCallJudgePolicy",
    ),
]


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("policy_class_ref,config", POLICY_CONFIGS)
async def test_policy_openai_non_streaming(policy_class_ref: str, config: dict, http_client):
    """Test each policy works with OpenAI client, non-streaming."""

    async with policy_context(policy_class_ref, config):
        response = await http_client.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Say hello"}],
                "max_tokens": 20,
                "stream": False,
            },
            headers={"Authorization": f"Bearer {API_KEY}"},
        )

        assert response.status_code == 200, f"Policy {policy_class_ref} failed: {response.text}"
        data = response.json()
        assert "choices" in data
        assert len(data["choices"]) > 0
        assert "message" in data["choices"][0]


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("policy_class_ref,config", POLICY_CONFIGS)
async def test_policy_openai_streaming(policy_class_ref: str, config: dict, http_client):
    """Test each policy works with OpenAI client, streaming."""
    async with policy_context(policy_class_ref, config):
        async with http_client.stream(
            "POST",
            f"{GATEWAY_URL}/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Say hello"}],
                "max_tokens": 20,
                "stream": True,
            },
            headers={"Authorization": f"Bearer {API_KEY}"},
        ) as response:
            assert response.status_code == 200, f"Policy {policy_class_ref} failed: {response.status_code}"

            chunks = []
            async for line in response.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    chunks.append(line)

            assert len(chunks) > 0, f"No chunks received for {policy_class_ref}"


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("policy_class_ref,config", POLICY_CONFIGS)
async def test_policy_anthropic_non_streaming(policy_class_ref: str, config: dict, http_client):
    """Test each policy works with Anthropic client, non-streaming."""
    async with policy_context(policy_class_ref, config):
        response = await http_client.post(
            f"{GATEWAY_URL}/v1/messages",
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "Say hello"}],
                "max_tokens": 20,
                "stream": False,
            },
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "anthropic-version": "2023-06-01",
            },
        )

        assert response.status_code == 200, f"Policy {policy_class_ref} failed: {response.text}"
        data = response.json()
        assert "content" in data
        assert len(data["content"]) > 0
        assert data["content"][0]["type"] == "text"


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("policy_class_ref,config", POLICY_CONFIGS)
async def test_policy_anthropic_streaming(policy_class_ref: str, config: dict, http_client):
    """Test each policy works with Anthropic client, streaming."""
    async with policy_context(policy_class_ref, config):
        async with http_client.stream(
            "POST",
            f"{GATEWAY_URL}/v1/messages",
            json={
                "model": "claude-haiku-4-5",
                "messages": [{"role": "user", "content": "Say hello"}],
                "max_tokens": 20,
                "stream": True,
            },
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "anthropic-version": "2023-06-01",
            },
        ) as response:
            assert response.status_code == 200, f"Policy {policy_class_ref} failed: {response.status_code}"

            events = []
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    events.append(line)

            # Should have message_start, content_block_start, content_block_delta, etc.
            assert len(events) > 0, f"No events received for {policy_class_ref}"
