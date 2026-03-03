"""Runs Claude Code CLI inside a Docker sandbox container via docker compose exec.

Each call to run_turn() executes one `claude -p` invocation, parsing the
stream-json output into a TurnSummary. The driver tracks session_id across
turns so subsequent calls use --resume for conversation continuity.
"""

import asyncio
import logging
import time

from scripts.overseer.stream_parser import TurnSummary, summarize_turn

logger = logging.getLogger(__name__)


class SessionDriver:
    def __init__(
        self,
        container_name: str,
        gateway_url: str,
        api_key: str,
        timeout_seconds: int = 300,
    ):
        self.container_name = container_name
        self.gateway_url = gateway_url
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.session_id: str | None = None
        self.turn_count = 0

    def _build_command(self, prompt: str, session_id: str | None = None) -> list[str]:
        """Build the claude CLI command args (NOT the docker exec prefix)."""
        cmd = [
            "claude",
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]
        if session_id:
            cmd.extend(["--resume", session_id])
        cmd.append(prompt)
        return cmd

    async def run_turn(self, prompt: str) -> TurnSummary:
        """Execute one turn via docker compose exec -T and parse output."""
        turn_number = self.turn_count + 1
        cmd = self._build_command(prompt, session_id=self.session_id)

        exec_cmd = [
            "docker",
            "compose",
            "exec",
            "-T",
            "-e",
            f"ANTHROPIC_BASE_URL={self.gateway_url}",
            "-e",
            f"ANTHROPIC_API_KEY={self.api_key}",
            self.container_name,
        ] + cmd

        logger.info("Running turn %d: %s", turn_number, exec_cmd)

        start_time = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *exec_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise
        end_time = time.monotonic()

        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")

        summary = summarize_turn(stdout, turn_number, start_time, end_time)

        if proc.returncode != 0:
            msg = f"claude exited with code {proc.returncode}"
            if stderr.strip():
                msg += f": {stderr.strip()[:500]}"
            summary.anomalies.append(msg)
            logger.warning("Turn %d anomaly: %s", turn_number, msg)

        if self.session_id is None and summary.session_id:
            self.session_id = summary.session_id

        self.turn_count += 1
        return summary
