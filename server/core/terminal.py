"""
Terminal Executor — Sandboxed command execution for agents.
Agents can run code, see output, and iterate on errors.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Callable

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30  # seconds
MAX_OUTPUT_SIZE = 50_000  # characters


@dataclass
class CommandResult:
    command: str
    stdout: str
    stderr: str
    return_code: int
    duration: float
    timed_out: bool

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "stdout": self.stdout[-MAX_OUTPUT_SIZE:],
            "stderr": self.stderr[-MAX_OUTPUT_SIZE:],
            "return_code": self.return_code,
            "duration": round(self.duration, 2),
            "timed_out": self.timed_out,
            "success": self.return_code == 0,
        }


# Commands that require user approval
DANGEROUS_PATTERNS = [
    "rm -rf", "rm -r", "rmdir",
    "sudo", "chmod", "chown",
    "pip install", "npm install", "brew install",
    "curl", "wget",
    "kill", "pkill",
    "> /dev/", "mkfs",
]


class TerminalExecutor:
    """
    Executes commands in the workspace directory with timeout and output capture.
    Streams output to a callback for real-time display in the UI.
    """

    def __init__(self, max_concurrent: int = 3):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_processes: dict[str, asyncio.subprocess.Process] = {}

    def is_dangerous(self, command: str) -> bool:
        """Check if a command matches a dangerous pattern."""
        cmd_lower = command.lower().strip()
        return any(pat in cmd_lower for pat in DANGEROUS_PATTERNS)

    async def execute(
        self,
        command: str,
        cwd: str,
        timeout: float = DEFAULT_TIMEOUT,
        output_callback: Optional[Callable] = None,
    ) -> CommandResult:
        """
        Execute a command in the given working directory.
        Streams output via callback if provided.
        """
        async with self._semaphore:
            start_time = time.time()
            timed_out = False

            logger.info(f"Executing: {command} (cwd={cwd})")

            try:
                process = await asyncio.create_subprocess_shell(
                    command,
                    cwd=cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=None,  # Inherit parent env
                )

                proc_id = str(id(process))
                self._active_processes[proc_id] = process

                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        process.communicate(),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    timed_out = True
                    process.kill()
                    stdout_bytes, stderr_bytes = await process.communicate()

                stdout = stdout_bytes.decode("utf-8", errors="replace")
                stderr = stderr_bytes.decode("utf-8", errors="replace")

                # Stream output via callback
                if output_callback:
                    await output_callback({
                        "command": command,
                        "stdout": stdout[-MAX_OUTPUT_SIZE:],
                        "stderr": stderr[-MAX_OUTPUT_SIZE:],
                        "return_code": process.returncode or -1,
                        "timed_out": timed_out,
                    })

                duration = time.time() - start_time
                result = CommandResult(
                    command=command,
                    stdout=stdout,
                    stderr=stderr,
                    return_code=process.returncode or -1,
                    duration=duration,
                    timed_out=timed_out,
                )

                if result.return_code != 0:
                    logger.warning(
                        f"Command failed (rc={result.return_code}): {command}\n"
                        f"stderr: {stderr[:200]}"
                    )
                else:
                    logger.info(f"Command succeeded: {command} ({duration:.1f}s)")

                return result

            except Exception as e:
                duration = time.time() - start_time
                logger.error(f"Command execution error: {e}")
                return CommandResult(
                    command=command,
                    stdout="",
                    stderr=str(e),
                    return_code=-1,
                    duration=duration,
                    timed_out=False,
                )
            finally:
                self._active_processes.pop(proc_id, None)

    async def kill_all(self):
        """Kill all active processes."""
        for proc_id, proc in list(self._active_processes.items()):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        self._active_processes.clear()
