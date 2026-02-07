"""
File Tracker — Tracks which agents are actively working on which files.
Provides visibility into file conflicts and recent activity for the orchestrator.
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# How long (seconds) a file touch is considered "recent"
ACTIVITY_WINDOW = 120  # 2 minutes


@dataclass
class FileTouch:
    """A record of an agent touching a file."""
    agent_id: str
    path: str
    action: str  # "read", "write", "edit"
    timestamp: float = field(default_factory=time.time)


class FileTracker:
    """
    Tracks which agents are actively working on which files.
    Used to detect conflicts and provide visibility to the orchestrator.
    """

    def __init__(self, activity_window: float = ACTIVITY_WINDOW):
        self._touches: list[FileTouch] = []
        self._activity_window = activity_window

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
        if not self._touches:
            return "No recent file activity."

        # Group by agent
        agent_files: dict[str, list[str]] = {}
        for t in self._touches:
            if t.action in ("write", "edit"):
                agent_files.setdefault(t.agent_id, []).append(t.path)

        lines = []
        for agent_id, files in sorted(agent_files.items()):
            unique_files = sorted(set(files))
            lines.append(f"- **{agent_id}** modified: {', '.join(unique_files)}")

        conflicts = self.get_conflicts()
        if conflicts:
            lines.append("\n⚠️ **FILE CONFLICTS** (multiple agents editing same file):")
            for c in conflicts:
                lines.append(f"- `{c['path']}` — edited by: {', '.join(c['agents'])}")

        return "\n".join(lines) if lines else "No recent write activity."
