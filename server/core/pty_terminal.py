"""
Interactive PTY Terminal — Real shell sessions with WebSocket streaming.
Provides users with a fully interactive terminal that can run any command
on the host machine, with real-time I/O streaming.
"""

import asyncio
import fcntl
import logging
import os
import pty
import select
import signal
import struct
import termios
import time
from dataclasses import dataclass
from typing import Optional, Callable

logger = logging.getLogger(__name__)

DEFAULT_SHELL = os.environ.get("SHELL", "/bin/zsh")
MAX_SESSIONS = 5
READ_CHUNK = 4096


@dataclass
class TerminalSession:
    """Represents a single terminal session."""
    session_id: str
    pid: int
    fd: int           # PTY master file descriptor
    cwd: str
    created_at: float
    shell: str
    cols: int = 120
    rows: int = 30
    active: bool = True


class InteractiveTerminal:
    """
    Manages interactive PTY terminal sessions.
    Each session runs a real shell process that the user can interact with
    through WebSocket-streamed input/output.
    """

    def __init__(self, max_sessions: int = MAX_SESSIONS):
        self._sessions: dict[str, TerminalSession] = {}
        self._max_sessions = max_sessions
        self._readers: dict[str, asyncio.Task] = {}
        self._output_callbacks: dict[str, Callable] = {}

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def list_sessions(self) -> list[dict]:
        """List all active terminal sessions."""
        return [
            {
                "session_id": s.session_id,
                "cwd": s.cwd,
                "shell": s.shell,
                "created_at": s.created_at,
                "cols": s.cols,
                "rows": s.rows,
                "active": s.active,
            }
            for s in self._sessions.values()
        ]

    async def create_session(
        self,
        session_id: str,
        cwd: str = "",
        shell: str = DEFAULT_SHELL,
        cols: int = 120,
        rows: int = 30,
        output_callback: Optional[Callable] = None,
    ) -> TerminalSession:
        """
        Create a new interactive terminal session.
        Returns the session info and starts streaming output.
        """
        if len(self._sessions) >= self._max_sessions:
            # Kill oldest session
            oldest = min(self._sessions.values(), key=lambda s: s.created_at)
            await self.kill_session(oldest.session_id)

        if not cwd:
            cwd = os.path.expanduser("~")

        # Create PTY pair
        master_fd, slave_fd = pty.openpty()

        # Set terminal size
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        # Set master to non-blocking
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # Fork the shell process
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env["LANG"] = "en_US.UTF-8"

        pid = os.fork()
        if pid == 0:
            # Child process — become the shell
            os.close(master_fd)
            os.setsid()

            # Set slave as controlling terminal
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

            # Redirect stdin/stdout/stderr to slave PTY
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)

            if slave_fd > 2:
                os.close(slave_fd)

            os.chdir(cwd)
            os.execvpe(shell, [shell, "-l"], env)
            # If exec fails, exit
            os._exit(1)

        # Parent process
        os.close(slave_fd)

        session = TerminalSession(
            session_id=session_id,
            pid=pid,
            fd=master_fd,
            cwd=cwd,
            created_at=time.time(),
            shell=shell,
            cols=cols,
            rows=rows,
        )

        self._sessions[session_id] = session

        if output_callback:
            self._output_callbacks[session_id] = output_callback

        # Start the output reader task
        self._readers[session_id] = asyncio.create_task(
            self._read_output(session_id)
        )

        logger.info(f"Terminal session created: {session_id} (pid={pid}, shell={shell})")
        return session

    async def write_input(self, session_id: str, data: str):
        """Write user input to a terminal session."""
        session = self._sessions.get(session_id)
        if not session or not session.active:
            return

        try:
            os.write(session.fd, data.encode("utf-8"))
        except OSError as e:
            logger.error(f"Write to terminal {session_id} failed: {e}")
            session.active = False

    async def resize(self, session_id: str, cols: int, rows: int):
        """Resize the terminal window."""
        session = self._sessions.get(session_id)
        if not session or not session.active:
            return

        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(session.fd, termios.TIOCSWINSZ, winsize)
            session.cols = cols
            session.rows = rows

            # Send SIGWINCH to the shell process
            os.kill(session.pid, signal.SIGWINCH)
        except OSError as e:
            logger.debug(f"Resize failed for {session_id}: {e}")

    async def _read_output(self, session_id: str):
        """Continuously read output from the PTY and send via callback."""
        session = self._sessions.get(session_id)
        if not session:
            return

        loop = asyncio.get_event_loop()

        try:
            while session.active:
                try:
                    # Use asyncio-friendly file descriptor reading
                    data = await loop.run_in_executor(
                        None,
                        self._blocking_read,
                        session.fd,
                        session_id,
                    )

                    if data is None:
                        # Process exited
                        session.active = False
                        break

                    if data and session_id in self._output_callbacks:
                        try:
                            callback = self._output_callbacks[session_id]
                            if asyncio.iscoroutinefunction(callback):
                                await callback(session_id, data)
                            else:
                                callback(session_id, data)
                        except Exception as e:
                            logger.debug(f"Output callback error: {e}")

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"Read error for {session_id}: {e}")
                    await asyncio.sleep(0.1)

        except Exception as e:
            logger.error(f"Output reader crashed for {session_id}: {e}")
        finally:
            session.active = False

    def _blocking_read(self, fd: int, session_id: str) -> Optional[str]:
        """Blocking read from PTY fd (run in executor)."""
        try:
            # Wait for data with timeout
            ready, _, _ = select.select([fd], [], [], 0.1)
            if not ready:
                return ""

            data = os.read(fd, READ_CHUNK)
            if not data:
                return None  # EOF — process exited

            return data.decode("utf-8", errors="replace")

        except OSError:
            return None  # File descriptor closed

    async def kill_session(self, session_id: str):
        """Kill a terminal session."""
        session = self._sessions.get(session_id)
        if not session:
            return

        session.active = False

        # Cancel reader task
        reader = self._readers.pop(session_id, None)
        if reader:
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):
                pass

        # Kill the shell process
        try:
            os.kill(session.pid, signal.SIGTERM)
            await asyncio.sleep(0.5)
            try:
                os.kill(session.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass

        # Close the file descriptor
        try:
            os.close(session.fd)
        except OSError:
            pass

        # Wait for child process
        try:
            os.waitpid(session.pid, os.WNOHANG)
        except ChildProcessError:
            pass

        # Cleanup
        self._sessions.pop(session_id, None)
        self._output_callbacks.pop(session_id, None)

        logger.info(f"Terminal session killed: {session_id}")

    async def kill_all(self):
        """Kill all terminal sessions."""
        for sid in list(self._sessions.keys()):
            await self.kill_session(sid)
