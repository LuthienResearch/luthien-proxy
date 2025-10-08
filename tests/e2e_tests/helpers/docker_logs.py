"""ABOUTME: Utilities for retrieving and parsing docker compose logs in E2E tests.

ABOUTME: Provides functions to get logs from containers with ANSI stripping and filtering.
"""

import datetime as _dt
import re
import subprocess
import time
from typing import Optional


def _filter_by_identifier(logs: str, identifier: Optional[str]) -> str:
    """Restrict logs to lines containing the identifier when provided."""

    if not identifier:
        return logs

    lines = [line for line in logs.splitlines() if identifier in line]
    return "\n".join(lines)


def _format_timestamp(ts: float) -> str:
    """Return an ISO8601 timestamp suitable for docker compose --since/--until."""

    return _dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_container_logs(
    container: str,
    since_seconds: int = 10,
    *,
    since_time: Optional[float] = None,
    until_time: Optional[float] = None,
    call_id: Optional[str] = None,
    retries: int = 5,
    retry_delay: float = 0.2,
) -> str:
    """Get logs from a docker compose container.

    Args:
        container: Container name (e.g., "litellm-proxy", "control-plane")
        since_seconds: Fallback window if since_time is not provided.
        since_time: Optional epoch timestamp to start collecting logs.
        until_time: Optional epoch timestamp to stop collecting logs.
        call_id: Optional identifier to filter for.
        retries: Additional attempts to fetch logs if the first call is empty.
        retry_delay: Delay between retries in seconds.

    Returns:
        Container logs with ANSI escape codes stripped
    """
    attempt = 0
    while True:
        cmd = ["docker", "compose", "logs"]

        if since_time is not None:
            cmd.extend(["--since", _format_timestamp(since_time)])
        else:
            cmd.extend(["--since", f"{since_seconds}s"])

        if until_time is not None:
            cmd.extend(["--until", _format_timestamp(until_time)])

        cmd.extend(["--no-color", container])

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        ansi_escape = re.compile(r"\x1b\[[0-9;]*m")
        logs = ansi_escape.sub("", result.stdout)
        filtered = _filter_by_identifier(logs, call_id)
        if filtered or attempt >= retries:
            return filtered

        attempt += 1
        time.sleep(retry_delay)


def get_litellm_logs(
    since_seconds: int = 10,
    *,
    since_time: Optional[float] = None,
    until_time: Optional[float] = None,
    call_id: Optional[str] = None,
    retries: int = 5,
    retry_delay: float = 0.2,
) -> str:
    """Get recent logs from the litellm-proxy container.

    Args:
        since_seconds: How many seconds back to retrieve logs (default: 10)
        since_time: Optional epoch timestamp to start collecting logs.
        until_time: Optional epoch timestamp to stop collecting logs.

    Returns:
        LiteLLM proxy logs with ANSI codes stripped
    """
    return get_container_logs(
        "litellm-proxy",
        since_seconds,
        since_time=since_time,
        call_id=call_id,
        retries=retries,
        retry_delay=retry_delay,
    )


def get_control_plane_logs(
    since_seconds: int = 10,
    *,
    since_time: Optional[float] = None,
    until_time: Optional[float] = None,
    call_id: Optional[str] = None,
    retries: int = 5,
    retry_delay: float = 0.2,
) -> str:
    """Get recent logs from the control-plane container.

    Args:
        since_seconds: How many seconds back to retrieve logs (default: 10)
        since_time: Optional epoch timestamp to start collecting logs.
        until_time: Optional epoch timestamp to stop collecting logs.

    Returns:
        Control plane logs with ANSI codes stripped
    """
    return get_container_logs(
        "control-plane",
        since_seconds,
        since_time=since_time,
        call_id=call_id,
        retries=retries,
        retry_delay=retry_delay,
    )


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
