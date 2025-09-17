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

    async def _get_counters(self, client: httpx.AsyncClient) -> dict:
        """Fetch in-memory hook counters from the control plane.

        We intentionally use the lightweight counters endpoint to keep this
        script simple and avoid DB dependencies.
        """
        try:
            res = await client.get(f"{self.control_plane_url}/api/hooks/counters")
            res.raise_for_status()
            data = res.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

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

        test_model = os.getenv("TEST_MODEL", "gpt-5")
        request_data = {
            "model": test_model,  # Use a model from env/config
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

        test_model = os.getenv("TEST_MODEL", "gpt-5")
        request_data = {
            "model": test_model,
            "messages": [{"role": "user", "content": "Count from 1 to 5, one number per line."}],
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
        """Validate hook counters increase when calling the LiteLLM proxy.

        We test both non-streaming and streaming flows by:
        - capturing counters before
        - making a call to `proxy_url/chat/completions`
        - asserting deltas in the relevant counters
        """
        print("\n🎛️  Testing control plane integration (counter deltas via proxy calls)...")

        headers = {
            "Authorization": f"Bearer {self.master_key}",
            "Content-Type": "application/json",
        }
        model = os.getenv("TEST_MODEL", "gpt-5")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # 1) Non-streaming call → expect pre + success
                before_sync = await self._get_counters(client)
                sync_payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": "Say SYNC_OK"}],
                }
                sync_resp = await client.post(
                    f"{self.proxy_url}/chat/completions",
                    headers=headers,
                    json=sync_payload,
                )
                if sync_resp.status_code != 200:
                    print(f"❌ Proxy sync failed: {sync_resp.status_code} {sync_resp.text}")
                    return False
                after_sync = await self._get_counters(client)
                pre_delta = after_sync.get("async_pre_call_hook", 0) - before_sync.get("async_pre_call_hook", 0)
                success_delta = after_sync.get("async_post_call_success_hook", 0) - before_sync.get(
                    "async_post_call_success_hook", 0
                )
                if pre_delta < 1 or success_delta < 1:
                    print(f"❌ Expected pre/success to increment ≥1; pre+{pre_delta}, success+{success_delta}")
                    return False

                # 2) Streaming call → expect iterator events (and pre)
                before_stream = await self._get_counters(client)
                stream_payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": "List five kinds of fruit."}],
                    "stream": True,
                }

                try:
                    async with client.stream(
                        "POST",
                        f"{self.proxy_url}/chat/completions",
                        headers=headers,
                        json=stream_payload,
                    ) as response:
                        if response.status_code != 200:
                            print(f"❌ Proxy stream failed: {response.status_code} {await response.aread()}")
                            return False
                        # Consume stream fully
                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[6:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                _ = json.loads(data)
                            except Exception:
                                pass
                except Exception as e:
                    print(f"❌ Stream exception: {e}")
                    return False

                after_stream = await self._get_counters(client)
                iter_delta = after_stream.get("async_post_call_streaming_iterator_hook", 0) - before_stream.get(
                    "async_post_call_streaming_iterator_hook", 0
                )
                if iter_delta < 1:
                    print(f"❌ Expected streaming iterator to increment ≥1; iterator+{iter_delta}")
                    return False

                print("✅ Hook counters increased as expected (pre, success, iterator)")
                return True

        except Exception as e:
            print(f"❌ Control plane test failed: {e}")
            return False

    async def run_all_tests(self) -> bool:
        """Run all tests and return overall success."""
        print("🧪 Starting Luthien Control proxy tests...\n")

        tests = [
            ("Health Checks", self.test_health_checks),
            ("Simple Completion", self.test_simple_completion),
            ("Streaming Completion", self.test_streaming_completion),
            ("Control Plane Integration", self.test_control_plane_integration),
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
