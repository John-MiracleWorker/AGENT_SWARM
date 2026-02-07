"""
Orchestrator Health Monitor — Detects stalled orchestrators and recovers the swarm.

The orchestrator is the single point of failure in the swarm. If it stalls
(stops making progress), all agents are blocked waiting for tasks. This
module provides:

- Heartbeat tracking: detects when the orchestrator hasn't acted in too long
- Stall detection: identifies when tasks aren't progressing
- Recovery actions: unblocks agents, escalates to user, or redistributes work
"""

import asyncio
import time
import logging
from typing import Optional, Callable, Awaitable

logger = logging.getLogger(__name__)

# Thresholds
HEARTBEAT_INTERVAL = 30  # Orchestrator should act at least every 30s
STALL_THRESHOLD = 90  # Mission is stalled if no task progress for 90s
MAX_STALL_WARNINGS = 3  # After 3 warnings, escalate to user


class OrchestratorHealthMonitor:
    """Monitors orchestrator health and detects stalled missions."""

    def __init__(self):
        self._last_heartbeat: float = 0
        self._last_task_progress: float = 0
        self._stall_warnings: int = 0
        self._monitor_task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._on_stall_callback: Optional[Callable[..., Awaitable]] = None
        self._on_recovery_callback: Optional[Callable[..., Awaitable]] = None

    def set_callbacks(
        self,
        on_stall: Optional[Callable[..., Awaitable]] = None,
        on_recovery: Optional[Callable[..., Awaitable]] = None,
    ):
        """Set callbacks for stall detection and recovery."""
        self._on_stall_callback = on_stall
        self._on_recovery_callback = on_recovery

    def record_heartbeat(self):
        """Called when the orchestrator performs any action."""
        self._last_heartbeat = time.time()

    def record_task_progress(self):
        """Called when any task changes status."""
        self._last_task_progress = time.time()
        # Reset stall warnings on progress
        if self._stall_warnings > 0:
            logger.info("[HealthMon] Mission progressing again, resetting stall warnings")
            self._stall_warnings = 0

    async def start(self):
        """Start the health monitor loop."""
        self._running = True
        self._last_heartbeat = time.time()
        self._last_task_progress = time.time()
        self._stall_warnings = 0
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("[HealthMon] Orchestrator health monitor started")

    async def stop(self):
        """Stop the health monitor."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("[HealthMon] Orchestrator health monitor stopped")

    def get_status(self) -> dict:
        """Get current health status."""
        now = time.time()
        heartbeat_age = now - self._last_heartbeat if self._last_heartbeat else 0
        progress_age = now - self._last_task_progress if self._last_task_progress else 0
        return {
            "healthy": heartbeat_age < HEARTBEAT_INTERVAL * 2,
            "seconds_since_heartbeat": round(heartbeat_age, 1),
            "seconds_since_progress": round(progress_age, 1),
            "stall_warnings": self._stall_warnings,
            "stalled": self._stall_warnings >= MAX_STALL_WARNINGS,
        }

    async def _monitor_loop(self):
        """Periodic check for orchestrator health."""
        while self._running:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                now = time.time()

                # Check heartbeat
                heartbeat_age = now - self._last_heartbeat
                if heartbeat_age > HEARTBEAT_INTERVAL * 2:
                    logger.warning(
                        f"[HealthMon] Orchestrator hasn't acted in {heartbeat_age:.0f}s"
                    )

                # Check task progress
                progress_age = now - self._last_task_progress
                if progress_age > STALL_THRESHOLD:
                    self._stall_warnings += 1
                    logger.warning(
                        f"[HealthMon] No task progress for {progress_age:.0f}s "
                        f"(warning {self._stall_warnings}/{MAX_STALL_WARNINGS})"
                    )

                    if self._on_stall_callback:
                        try:
                            await self._on_stall_callback(
                                warning_count=self._stall_warnings,
                                seconds_stalled=progress_age,
                                is_critical=self._stall_warnings >= MAX_STALL_WARNINGS,
                            )
                        except Exception as e:
                            logger.error(f"[HealthMon] Stall callback error: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[HealthMon] Monitor error: {e}")
                await asyncio.sleep(10)

    def reset(self):
        """Reset all tracking for a new mission."""
        self._last_heartbeat = 0
        self._last_task_progress = 0
        self._stall_warnings = 0
