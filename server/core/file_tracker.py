"""
File Tracker — Tracks which agents are actively working on which files.
Provides visibility into file conflicts, recent activity, and exclusive
file reservations to prevent concurrent edits.
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# How long (seconds) a file touch is considered "recent"
ACTIVITY_WINDOW = 120  # 2 minutes

# How long (seconds) a file reservation lasts before auto-expiring
RESERVATION_TTL = 300  # 5 minutes


@dataclass
class FileTouch:
    """A record of an agent touching a file."""
    agent_id: str
    path: str
    action: str  # "read", "write", "edit"
    timestamp: float = field(default_factory=time.time)


@dataclass
class FileReservation:
    """An exclusive lock on a file held by an agent."""
    agent_id: str
    path: str
    created_at: float = field(default_factory=time.time)
    ttl: float = RESERVATION_TTL

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl


class FileTracker:
    """
    Tracks which agents are actively working on which files.
    Provides:
    - Activity logging (who touched what, when)
    - Conflict detection (multiple writers on same file)
    - Exclusive reservations (prevent concurrent edits)
    """

    def __init__(self, activity_window: float = ACTIVITY_WINDOW):
        self._touches: list[FileTouch] = []
        self._activity_window = activity_window
        # Exclusive reservations: path -> FileReservation
        self._reservations: dict[str, FileReservation] = {}

    # ─── Reservations ────────────────────────────────────────────

    def reserve(self, agent_id: str, path: str) -> bool:
        """
        Reserve a file for exclusive editing.
        Returns True if the reservation was granted (or agent already owns it).
        Returns False if another agent holds the reservation.
        """
        self._prune_reservations()
        existing = self._reservations.get(path)
        if existing:
            if existing.agent_id == agent_id:
                # Refresh the TTL
                existing.created_at = time.time()
                return True
            # Another agent holds the reservation
            logger.warning(
                f"🔒 File reservation denied: {agent_id} tried to reserve '{path}' "
                f"but {existing.agent_id} holds it"
            )
            return False
        # Grant reservation
        self._reservations[path] = FileReservation(agent_id=agent_id, path=path)
        logger.info(f"🔒 {agent_id} reserved '{path}'")
        return True

    def release(self, agent_id: str, path: str):
        """Release a file reservation. Only the owning agent can release."""
        existing = self._reservations.get(path)
        if existing and existing.agent_id == agent_id:
            del self._reservations[path]
            logger.info(f"🔓 {agent_id} released '{path}'")

    def release_all(self, agent_id: str):
        """Release all reservations held by an agent."""
        to_remove = [p for p, r in self._reservations.items() if r.agent_id == agent_id]
        for p in to_remove:
            del self._reservations[p]
        if to_remove:
            logger.info(f"🔓 {agent_id} released {len(to_remove)} reservations")

    def get_owner(self, path: str) -> Optional[str]:
        """Get the agent that currently has a file reserved, or None."""
        self._prune_reservations()
        res = self._reservations.get(path)
        return res.agent_id if res else None

    def _prune_reservations(self):
        """Remove expired reservations."""
        expired = [p for p, r in self._reservations.items() if r.is_expired]
        for p in expired:
            logger.info(f"🔓 Reservation expired: '{p}' (was held by {self._reservations[p].agent_id})")
            del self._reservations[p]

    # ─── Activity Tracking ───────────────────────────────────────

    def record(self, agent_id: str, path: str, action: str):
        """Record that an agent touched a file."""
        self._touches.append(FileTouch(
            agent_id=agent_id,
            path=path,
            action=action,
        ))
        self._prune()

    def _prune(self):
        """Remove stale activity records."""
        cutoff = time.time() - self._activity_window
        self._touches = [t for t in self._touches if t.timestamp > cutoff]

    def get_recent_agents(self, path: str, exclude: Optional[str] = None) -> list[str]:
        """Get agents that recently touched a file (within activity window)."""
        self._prune()
        agents = set()
        for t in self._touches:
            if t.path == path and t.agent_id != exclude:
                agents.add(t.agent_id)
        return list(agents)

    def get_recent_writers(self, path: str, exclude: Optional[str] = None) -> list[str]:
        """Get agents that recently WROTE to a file."""
        self._prune()
        agents = set()
        for t in self._touches:
            if t.path == path and t.action in ("write", "edit") and t.agent_id != exclude:
                agents.add(t.agent_id)
        return list(agents)

    def get_agent_files(self, agent_id: str) -> list[str]:
        """Get files an agent has recently touched."""
        self._prune()
        return list(set(t.path for t in self._touches if t.agent_id == agent_id))

    def get_conflicts(self) -> list[dict]:
        """Get files being touched by 2+ agents (potential conflicts)."""
        self._prune()
        # Group write/edit touches by file
        file_agents: dict[str, set[str]] = {}
        for t in self._touches:
            if t.action in ("write", "edit"):
                file_agents.setdefault(t.path, set()).add(t.agent_id)

        conflicts = []
        for path, agents in file_agents.items():
            if len(agents) > 1:
                conflicts.append({
                    "path": path,
                    "agents": sorted(agents),
                })
        return conflicts

    def get_activity_summary(self) -> str:
        """Get a human-readable summary of recent file activity for the orchestrator."""
        self._prune()
        self._prune_reservations()

        lines = []

        # Show active reservations
        if self._reservations:
            lines.append("🔒 **Active File Reservations:**")
            for path, res in sorted(self._reservations.items()):
                remaining = max(0, res.ttl - (time.time() - res.created_at))
                lines.append(f"- `{path}` → **{res.agent_id}** ({remaining:.0f}s remaining)")

        if not self._touches:
            if not lines:
                return "No recent file activity."
            return "\n".join(lines)

        # Group by agent
        agent_files: dict[str, list[str]] = {}
        for t in self._touches:
            if t.action in ("write", "edit"):
                agent_files.setdefault(t.agent_id, []).append(t.path)

        if agent_files:
            lines.append("\n📝 **Recent Modifications:**")
            for agent_id, files in sorted(agent_files.items()):
                unique_files = sorted(set(files))
                lines.append(f"- **{agent_id}** modified: {', '.join(unique_files)}")

        conflicts = self.get_conflicts()
        if conflicts:
            lines.append("\n⚠️ **FILE CONFLICTS** (multiple agents editing same file):")
            for c in conflicts:
                lines.append(f"- `{c['path']}` — edited by: {', '.join(c['agents'])}")

        return "\n".join(lines) if lines else "No recent write activity."
