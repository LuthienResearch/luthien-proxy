"""ABOUTME: Utilities for retrieving and parsing docker compose logs in E2E tests.

ABOUTME: Provides functions to get logs from containers with ANSI stripping and filtering.
"""

import re
import subprocess


def get_container_logs(container: str, since_seconds: int = 10) -> str:
    """Get recent logs from a docker compose container.

    Args:
        container: Container name (e.g., "litellm-proxy", "control-plane")
        since_seconds: How many seconds back to retrieve logs (default: 10)

    Returns:
        Container logs with ANSI escape codes stripped
    """
    result = subprocess.run(
        [
            "docker",
            "compose",
            "logs",
            "--since",
            f"{since_seconds}s",
            "--no-color",
            container,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    # Strip ANSI escape codes that were embedded in logs by applications
    ansi_escape = re.compile(r"\x1b\[[0-9;]*m")
    return ansi_escape.sub("", result.stdout)


def get_litellm_logs(since_seconds: int = 10) -> str:
    """Get recent logs from the litellm-proxy container.

    Args:
        since_seconds: How many seconds back to retrieve logs (default: 10)

    Returns:
        LiteLLM proxy logs with ANSI codes stripped
    """
    return get_container_logs("litellm-proxy", since_seconds)


def get_control_plane_logs(since_seconds: int = 10) -> str:
    """Get recent logs from the control-plane container.

    Args:
        since_seconds: How many seconds back to retrieve logs (default: 10)

    Returns:
        Control plane logs with ANSI codes stripped
    """
    return get_container_logs("control-plane", since_seconds)


def filter_logs_by_pattern(logs: str, pattern: str) -> list[str]:
    """Filter log lines matching a pattern.

    Args:
        logs: Log string to filter
        pattern: String pattern to search for in each line

    Returns:
        List of log lines containing the pattern
    """
    return [line for line in logs.splitlines() if pattern in line]


def extract_stream_ids(logs: str) -> set[str]:
    """Extract all stream IDs (UUIDs) from logs.

    Args:
        logs: Log string to search

    Returns:
        Set of unique stream IDs found in logs
    """
    uuid_pattern = re.compile(r"\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]")
    return {match.group(1) for match in uuid_pattern.finditer(logs)}


def find_most_recent_match(logs: str, pattern: str) -> str | None:
    """Find the most recent (last) log line matching a pattern.

    Args:
        logs: Log string to search
        pattern: String pattern to search for

    Returns:
        The last matching line, or None if no match found
    """
    matches = filter_logs_by_pattern(logs, pattern)
    return matches[-1] if matches else None
