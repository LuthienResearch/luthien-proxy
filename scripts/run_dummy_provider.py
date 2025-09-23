"""Launch the deterministic dummy provider used for demo traffic."""

from __future__ import annotations

import argparse

import uvicorn
from demo_lib import DeterministicLLMProvider, create_dummy_provider_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the deterministic demo provider")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host interface to bind (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=4010,
        help="Port to bind (default: 4010)",
    )
    args = parser.parse_args()

    provider = DeterministicLLMProvider()
    app = create_dummy_provider_app(provider)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
