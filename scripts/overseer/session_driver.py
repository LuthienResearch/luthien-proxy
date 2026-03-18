"""Runs Claude Code CLI inside a Docker sandbox container via docker compose exec.

Each call to run_turn() executes one `claude -p` invocation, parsing the
stream-json output into a TurnSummary. The driver tracks session_id across
turns so subsequent calls use --resume for conversation continuity.

Timeout is inactivity-based: the turn is killed only after idle_timeout_seconds
of silence on stdout (no new output from the subprocess). This lets long
productive turns run indefinitely while catching stuck sessions.
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
        api_key: str | None = None,
        auth_token: str | None = None,
        idle_timeout_seconds: int = 300,
        compose_project: str | None = None,
        model: str | None = None,
    ):
        if not api_key and not auth_token:
            raise ValueError("Either api_key or auth_token must be provided")
        self.container_name = container_name
        self.gateway_url = gateway_url
        self.api_key = api_key
        self.auth_token = auth_token
        self.idle_timeout_seconds = idle_timeout_seconds
        self.compose_project = compose_project
        self.model = model
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
        if self.model:
            cmd.extend(["--model", self.model])
        if session_id:
            cmd.extend(["--resume", session_id])
        cmd.append(prompt)
        return cmd

    async def _read_with_idle_timeout(
        self,
        proc: asyncio.subprocess.Process,
    ) -> tuple[bytes, bytes, bool]:
        """Read stdout/stderr incrementally, killing proc if stdout goes idle.

        Returns (stdout_bytes, stderr_bytes, timed_out).
        """
        assert proc.stdout is not None
        assert proc.stderr is not None

        stdout_chunks: list[bytes] = []
        last_activity = time.monotonic()
        timed_out = False

        async def drain_stderr() -> bytes:
            assert proc.stderr is not None
            return await proc.stderr.read()

        stderr_task = asyncio.create_task(drain_stderr())

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        proc.stdout.read(8192),
                        timeout=self.idle_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    idle_duration = time.monotonic() - last_activity
                    logger.warning(
                        "No output for %.0fs (idle_timeout=%ds), killing turn",
                        idle_duration,
                        self.idle_timeout_seconds,
                    )
                    timed_out = True
                    proc.kill()
                    break

                if not chunk:
                    break

                stdout_chunks.append(chunk)
                last_activity = time.monotonic()
        finally:
            stderr_bytes = await stderr_task
            await proc.wait()

        return b"".join(stdout_chunks), stderr_bytes, timed_out

    async def run_turn(self, prompt: str) -> TurnSummary:
        """Execute one turn via docker compose exec -T and parse output."""
        turn_number = self.turn_count + 1
        cmd = self._build_command(prompt, session_id=self.session_id)

        exec_cmd = ["docker", "compose"]
        if self.compose_project:
            exec_cmd.extend(["-p", self.compose_project])
        exec_cmd.extend(["exec", "-T", "-e", f"ANTHROPIC_BASE_URL={self.gateway_url}"])
        if self.auth_token:
            exec_cmd.extend(["-e", f"ANTHROPIC_AUTH_TOKEN={self.auth_token}"])
        if self.api_key:
            exec_cmd.extend(["-e", f"ANTHROPIC_API_KEY={self.api_key}"])
        exec_cmd.append(self.container_name)
        exec_cmd.extend(cmd)

        safe_cmd = []
        for arg in exec_cmd:
            if arg.startswith("ANTHROPIC_AUTH_TOKEN=") or arg.startswith("ANTHROPIC_API_KEY="):
                key, _, val = arg.partition("=")
                safe_cmd.append(f"{key}={val[:12]}...")
            else:
                safe_cmd.append(arg)
        logger.info("Running turn %d: %s", turn_number, safe_cmd)

        start_time = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *exec_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes, timed_out = await self._read_with_idle_timeout(proc)

        if timed_out:
            await self._kill_stale_claude_processes()

        end_time = time.monotonic()
        stdout = stdout_bytes.decode(errors="replace")
        stderr = stderr_bytes.decode(errors="replace")
        summary = summarize_turn(stdout, turn_number, start_time, end_time)

        if timed_out:
            idle_s = self.idle_timeout_seconds
            summary.anomalies.append(f"Turn idle-timed out after {idle_s}s of no output")
            logger.warning("Turn %d idle-timed out after %ds of silence", turn_number, idle_s)
        elif proc.returncode != 0:
            msg = f"claude exited with code {proc.returncode}"
            if stderr.strip():
                msg += f": {stderr.strip()[:500]}"
            summary.anomalies.append(msg)
            logger.warning("Turn %d anomaly: %s", turn_number, msg)

        if self.session_id is None and summary.session_id:
            self.session_id = summary.session_id

        self.turn_count += 1
        return summary

    async def _kill_stale_claude_processes(self) -> None:
        """Kill any leftover claude processes in the sandbox after a timeout."""
        kill_cmd = ["docker", "compose"]
        if self.compose_project:
            kill_cmd.extend(["-p", self.compose_project])
        kill_cmd.extend(
            [
                "exec",
                "-T",
                self.container_name,
                "sh",
                "-c",
                "pkill -f claude || true",
            ]
        )
        kill_proc = await asyncio.create_subprocess_exec(
            *kill_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await kill_proc.communicate()
        logger.info("Killed stale claude processes in sandbox")
