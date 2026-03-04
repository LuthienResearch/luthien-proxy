"""CLI entry point for the overseer: wire together session_driver, overseer_llm, and report_server.

Runs Claude Code inside a Docker sandbox, analyzes each turn via the overseer LLM,
and serves a live-updating dashboard. Runnable as:

    python -m scripts.overseer.main --task "Build a todo app"
"""

import argparse
import asyncio
import logging
import os
import subprocess
import sys
import time

import anthropic
from scripts.overseer.overseer_llm import analyze_turn
from scripts.overseer.report_server import ReportServer
from scripts.overseer.session_driver import SessionDriver

logger = logging.getLogger(__name__)

DEFAULT_API_KEY = "sk-luthien-dev-key"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Overseer: monitor Claude Code through the proxy gateway")
    parser.add_argument("--task", required=True, help="Initial task prompt for Claude Code")
    parser.add_argument("--max-turns", type=int, default=20, help="Stop after N turns (default: 20)")
    parser.add_argument("--timeout", type=int, default=600, help="Stop after N seconds total (default: 600)")
    parser.add_argument("--port", type=int, default=8080, help="Report server port (default: 8080)")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001", help="Overseer LLM model")
    parser.add_argument(
        "--sandbox-model",
        default="claude-haiku-4-5-20251001",
        help="Model for Claude Code inside sandbox (default: claude-haiku-4-5-20251001)",
    )
    parser.add_argument("--gateway-url", default="http://gateway:8000", help="Proxy URL from container perspective")
    parser.add_argument("--api-key", default=None, help="API key for sandbox auth to proxy (default: PROXY_API_KEY env)")
    parser.add_argument("--turn-timeout", type=int, default=600, help="Timeout per turn in seconds (default: 600)")
    parser.add_argument("--compose-project", default=None, help="Docker Compose project name (default: from COMPOSE_PROJECT_NAME env)")
    return parser.parse_args(argv)


def ensure_sandbox_running(compose_project: str | None = None) -> None:
    """Verify the sandbox container is running. Exit with instructions if not."""
    cmd = ["docker", "compose"]
    if compose_project:
        cmd.extend(["-p", compose_project])
    cmd.extend(["ps", "--format", "{{.Name}} {{.Status}}", "sandbox"])
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0 or "Up" not in result.stdout:
        logger.error(
            "Sandbox container is not running. Start it first:\n"
            "  docker compose --profile overseer up -d"
        )
        sys.exit(1)
    logger.info("Sandbox container is up")


async def run_overseer(args: argparse.Namespace) -> None:
    """Main overseer loop: drive turns, analyze, and report."""
    api_key = args.api_key or os.environ.get("PROXY_API_KEY", DEFAULT_API_KEY)
    compose_project = args.compose_project or os.environ.get("COMPOSE_PROJECT_NAME")

    ensure_sandbox_running(compose_project)

    report = ReportServer(port=args.port)
    report.task = args.task
    await report.start()
    logger.info("Dashboard running at http://localhost:%d", args.port)

    driver = SessionDriver(
        container_name="sandbox",
        gateway_url=args.gateway_url,
        api_key=api_key,
        timeout_seconds=args.turn_timeout,
        compose_project=compose_project,
        model=args.sandbox_model,
    )

    overseer_client = anthropic.AsyncAnthropic()

    report.set_status("running")
    current_prompt = args.task
    session_start = time.monotonic()

    try:
        for turn in range(1, args.max_turns + 1):
            elapsed = time.monotonic() - session_start
            if elapsed >= args.timeout:
                logger.info("Global timeout reached after %.0fs", elapsed)
                break

            logger.info("Turn %d: sending prompt (%d chars)", turn, len(current_prompt))
            summary = await driver.run_turn(current_prompt)
            report.add_turn(summary)

            if summary.anomalies:
                for a in summary.anomalies:
                    logger.warning("Rule anomaly (turn %d): %s", turn, a)

            analysis = await analyze_turn(summary, args.task, args.model, client=overseer_client)
            report.add_llm_anomalies(turn, analysis.anomalies)

            if analysis.anomalies:
                for a in analysis.anomalies:
                    logger.warning("LLM anomaly (turn %d): %s", turn, a)

            status = "OK" if summary.is_success else "ERROR"
            logger.info(
                "Turn %d result: %s | cost=$%.4f | duration=%.1fs",
                turn,
                status,
                summary.cost_usd,
                summary.duration_seconds,
            )

            current_prompt = analysis.next_prompt
            if not current_prompt:
                logger.info("Overseer LLM returned empty next_prompt, stopping")
                break

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception:
        logger.exception("Overseer loop failed")
    finally:
        report.set_status("finished")
        logger.info(
            "Session complete: %d turns, $%.4f total cost, %d anomalies",
            len(report.turns),
            report.total_cost,
            len(report.all_anomalies),
        )
        logger.info("Dashboard still running at http://localhost:%d -- Ctrl+C to exit", args.port)
        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            logger.info("Shutting down dashboard")
        finally:
            await report.stop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = parse_args()
    asyncio.run(run_overseer(args))


if __name__ == "__main__":
    main()
