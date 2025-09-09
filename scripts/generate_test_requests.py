"""
Generate test requests against the LiteLLM proxy.

Usage examples:
  uv run python scripts/generate_test_requests.py --n 3 --model gpt-4o
  uv run python scripts/generate_test_requests.py --n 1 --stream
  uv run python scripts/generate_test_requests.py --system "You are terse." --user "Hello"
"""

from __future__ import annotations

import argparse
import asyncio
import os

import httpx


async def send_request(
    client: httpx.AsyncClient,
    url: str,
    model: str,
    system: str,
    user: str,
    stream: bool,
    auth_key: str | None,
):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": stream,
        "max_tokens": 128,
        "temperature": 0.3,
    }
    headers = {}
    if auth_key:
        headers["Authorization"] = f"Bearer {auth_key}"
    r = await client.post(url, json=payload, headers=headers, timeout=60)
    if stream:
        # In LiteLLM, stream=true still returns event-stream; keep it simple here
        print(f"status={r.status_code} (stream response length={len(r.text)})")
    else:
        try:
            data = r.json()
        except Exception:
            print(f"status={r.status_code} body={r.text[:200]}")
            return
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "<no content>")
        )
        print(f"status={r.status_code} content={content[:120].replace('\n', ' ')}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=1, help="Number of requests")
    parser.add_argument("--model", type=str, default=os.getenv("TEST_MODEL", "gpt-4o"))
    parser.add_argument("--system", type=str, default="You are a helpful assistant.")
    parser.add_argument("--user", type=str, default="Say a fun fact about space.")
    parser.add_argument(
        "--stream", action="store_true", help="Request streaming response"
    )
    args = parser.parse_args()

    proxy_url = os.getenv("LITELLM_URL", "http://localhost:4000")
    url = f"{proxy_url}/chat/completions"

    async with httpx.AsyncClient() as client:
        auth_key = os.getenv("LITELLM_MASTER_KEY")
        for i in range(args.n):
            print(f"→ Request {i + 1}/{args.n}")
            await send_request(
                client, url, args.model, args.system, args.user, args.stream, auth_key
            )


if __name__ == "__main__":
    asyncio.run(main())
