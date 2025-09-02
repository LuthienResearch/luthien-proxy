#!/usr/bin/env python3

"""
ABOUTME: Test script for Luthien Control proxy to verify end-to-end functionality
ABOUTME: Tests basic LLM calls through the proxy and verifies control plane integration
"""

import asyncio
import json
import os
import sys

import httpx


class LuthienTester:
    """Test the Luthien Control proxy setup."""

    def __init__(self):
        # Read URLs from environment variables, with fallback defaults
        self.proxy_url = os.getenv("LITELLM_URL", "http://localhost:4000")
        self.control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://localhost:8081")
        self.master_key = os.getenv("LITELLM_MASTER_KEY", "sk-luthien-dev-key")

    async def test_health_checks(self) -> bool:
        """Test that all services are responding to health checks."""
        print("🏥 Testing service health checks...")

        services = {
            "Control Plane": f"{self.control_plane_url}/health",
            "LiteLLM Proxy": f"{self.proxy_url}/test",
        }

        all_healthy = True
        async with httpx.AsyncClient(timeout=10.0) as client:
            for service, url in services.items():
                try:
                    response = await client.get(url)
                    if response.status_code == 200:
                        print(f"✅ {service} is healthy")
                    else:
                        print(f"❌ {service} returned status {response.status_code}")
                        all_healthy = False
                except Exception as e:
                    print(f"❌ {service} failed: {e}")
                    all_healthy = False

        return all_healthy

    async def test_simple_completion(self) -> bool:
        """Test a simple completion through the proxy."""
        print("\n💬 Testing simple completion...")

        headers = {
            "Authorization": f"Bearer {self.master_key}",
            "Content-Type": "application/json",
        }

        request_data = {
            "model": "gpt-4o-mini",  # Use a model from our config
            "messages": [
                {
                    "role": "user",
                    "content": "Hello! Can you say 'Testing successful' please?",
                }
            ],
            "max_tokens": 50,
            "temperature": 0.7,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.proxy_url}/chat/completions",
                    headers=headers,
                    json=request_data,
                )

                if response.status_code == 200:
                    result = response.json()
                    content = result["choices"][0]["message"]["content"]
                    print(f"✅ Completion successful: {content}")
                    return True
                else:
                    print(f"❌ Completion failed with status {response.status_code}")
                    print(f"Response: {response.text}")
                    return False

        except Exception as e:
            print(f"❌ Completion request failed: {e}")
            return False

    async def test_streaming_completion(self) -> bool:
        """Test streaming completion through the proxy."""
        print("\n📡 Testing streaming completion...")

        headers = {
            "Authorization": f"Bearer {self.master_key}",
            "Content-Type": "application/json",
        }

        request_data = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": "Count from 1 to 5, one number per line."}
            ],
            "max_tokens": 50,
            "temperature": 0.7,
            "stream": True,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.proxy_url}/chat/completions",
                    headers=headers,
                    json=request_data,
                ) as response:
                    if response.status_code != 200:
                        print(f"❌ Streaming failed with status {response.status_code}")
                        return False

                    chunks_received = 0
                    content_parts = []

                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:]  # Remove "data: " prefix

                            if data_str.strip() == "[DONE]":
                                break

                            try:
                                chunk_data = json.loads(data_str)
                                chunks_received += 1

                                if "choices" in chunk_data and chunk_data["choices"]:
                                    delta = chunk_data["choices"][0].get("delta", {})
                                    if "content" in delta:
                                        content_parts.append(delta["content"])

                            except json.JSONDecodeError:
                                continue

                    full_content = "".join(content_parts)
                    print(f"✅ Streaming successful: received {chunks_received} chunks")
                    print(f"Content: {full_content}")
                    return True

        except Exception as e:
            print(f"❌ Streaming request failed: {e}")
            return False

    async def test_control_plane_integration(self) -> bool:
        """Test control plane hook endpoints with a no-op policy."""
        print("\n🎛️  Testing control plane integration (hooks)...")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Pre hook: expect pass-through (result_type none) for NoOp
                pre_req = {
                    "user_api_key_dict": {"user_id": "test-user"},
                    "cache": None,
                    "data": {
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": "Test message"}],
                    },
                    "call_type": "completion",
                }
                pre_res = await client.post(
                    f"{self.control_plane_url}/hooks/pre", json=pre_req
                )
                if pre_res.status_code != 200:
                    print(
                        f"❌ /hooks/pre status: {pre_res.status_code} body: {pre_res.text}"
                    )
                    return False
                pre_json = pre_res.json()
                if pre_json.get("result_type") not in {"none", "dict", "string"}:
                    print(f"❌ /hooks/pre unexpected result_type: {pre_json}")
                    return False

                # Post-success hook: expect no replacement for NoOp
                post_req = {
                    "data": pre_req["data"],
                    "user_api_key_dict": pre_req["user_api_key_dict"],
                    "response": {
                        "choices": [{"message": {"role": "assistant", "content": "ok"}}]
                    },
                }
                post_res = await client.post(
                    f"{self.control_plane_url}/hooks/post_success", json=post_req
                )
                if post_res.status_code != 200:
                    print(
                        f"❌ /hooks/post_success status: {post_res.status_code} body: {post_res.text}"
                    )
                    return False
                post_json = post_res.json()
                if post_json.get("replace") not in {True, False}:
                    print(f"❌ /hooks/post_success unexpected payload: {post_json}")
                    return False

                # Stream chunk: expect action present for NoOp (pass)
                chunk_req = {
                    "user_api_key_dict": pre_req["user_api_key_dict"],
                    "request_data": pre_req["data"],
                    "chunk": {"choices": [{"delta": {"content": "hello"}}]},
                    "chunk_index": 1,
                    "accumulated_text": "hello",
                }
                chunk_res = await client.post(
                    f"{self.control_plane_url}/hooks/stream_chunk", json=chunk_req
                )
                if chunk_res.status_code != 200:
                    print(
                        f"❌ /hooks/stream_chunk status: {chunk_res.status_code} body: {chunk_res.text}"
                    )
                    return False
                chunk_json = chunk_res.json()
                if chunk_json.get("action") not in {
                    "pass",
                    "suppress",
                    "edit",
                    "replace_stream",
                }:
                    print(f"❌ /hooks/stream_chunk unexpected payload: {chunk_json}")
                    return False

                print("✅ Control plane hooks responded correctly")
                return True

        except Exception as e:
            print(f"❌ Control plane test failed: {e}")
            return False

    async def test_suspicious_content(self) -> bool:
        """Test how the system handles potentially suspicious content."""
        print("\n🚨 Testing suspicious content handling...")

        headers = {
            "Authorization": f"Bearer {self.master_key}",
            "Content-Type": "application/json",
        }

        # Test with content that might trigger monitoring
        request_data = {
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": "Please write a simple Python script that prints 'Hello World'",
                }
            ],
            "max_tokens": 200,
            "temperature": 0.7,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.proxy_url}/chat/completions",
                    headers=headers,
                    json=request_data,
                )

                if response.status_code == 200:
                    result = response.json()
                    content = result["choices"][0]["message"]["content"]
                    print("✅ Suspicious content test completed")
                    print(f"Response preview: {content[:100]}...")

                    # Check if any interventions occurred (would need to check logs or control plane)
                    return True
                else:
                    print(
                        f"❌ Suspicious content test failed with status {response.status_code}"
                    )
                    return False

        except Exception as e:
            print(f"❌ Suspicious content test failed: {e}")
            return False

    async def run_all_tests(self) -> bool:
        """Run all tests and return overall success."""
        print("🧪 Starting Luthien Control proxy tests...\n")

        tests = [
            ("Health Checks", self.test_health_checks),
            ("Simple Completion", self.test_simple_completion),
            ("Streaming Completion", self.test_streaming_completion),
            ("Control Plane Integration", self.test_control_plane_integration),
            ("Suspicious Content Handling", self.test_suspicious_content),
        ]

        results = []

        for test_name, test_func in tests:
            try:
                result = await test_func()
                results.append(result)
            except Exception as e:
                print(f"❌ {test_name} failed with exception: {e}")
                results.append(False)

        print("\n📊 Test Results Summary:")
        print(f"   Passed: {sum(results)}/{len(results)} tests")

        if all(results):
            print("🎉 All tests passed! Luthien Control is working correctly.")
        else:
            print("⚠️  Some tests failed. Check the output above for details.")

        return all(results)


async def main():
    """Main test function."""
    tester = LuthienTester()
    success = await tester.run_all_tests()

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
