#!/usr/bin/env python3
# ABOUTME: Test script for V2 proxy - sends test requests to verify basic functionality
# ABOUTME: Can be used for manual testing during development

"""Test the V2 proxy with sample requests."""

import asyncio
import json
import os

import httpx

# Configuration
PROXY_URL = os.getenv("PROXY_URL", "http://localhost:8000")
API_KEY = os.getenv("PROXY_API_KEY", "test-key")


async def test_health():
    """Test health endpoint."""
    print("Testing health endpoint...")
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{PROXY_URL}/health")
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}\n")


async def test_openai_completion():
    """Test OpenAI chat completions endpoint (non-streaming)."""
    print("Testing OpenAI endpoint (non-streaming)...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{PROXY_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "Say hello in 3 words"}],
                "max_tokens": 50,
                "stream": False,
            },
        )
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Response: {data['choices'][0]['message']['content']}\n")
        else:
            print(f"Error: {response.text}\n")


async def test_openai_streaming():
    """Test OpenAI endpoint with streaming."""
    print("Testing OpenAI endpoint (streaming)...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream(
            "POST",
            f"{PROXY_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "Count to 5"}],
                "max_tokens": 50,
                "stream": True,
            },
        ) as response:
            print(f"Status: {response.status_code}")
            if response.status_code == 200:
                print("Streaming response: ", end="", flush=True)
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            content = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if content:
                                print(content, end="", flush=True)
                        except json.JSONDecodeError:
                            pass
                print("\n")
            else:
                print(f"Error: {await response.aread()}\n")


async def test_anthropic_messages():
    """Test Anthropic messages endpoint (non-streaming)."""
    print("Testing Anthropic endpoint (non-streaming)...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{PROXY_URL}/v1/messages",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "claude-3-5-sonnet-20241022",
                "messages": [{"role": "user", "content": "Say hello in 3 words"}],
                "max_tokens": 50,
                "stream": False,
            },
        )
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Response: {data['content'][0]['text']}\n")
        else:
            print(f"Error: {response.text}\n")


async def main():
    """Run all tests."""
    print(f"Testing proxy at: {PROXY_URL}\n")

    try:
        await test_health()
        await test_openai_completion()
        await test_openai_streaming()
        await test_anthropic_messages()
    except Exception as exc:
        print(f"Test failed: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
