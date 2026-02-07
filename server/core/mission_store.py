"""
Mission Store — Persists completed mission data to disk for history/replay.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STORE_DIR = Path.home() / ".agent_swarm" / "history"


class MissionStore:
    """JSON-file-based mission history store."""

    def __init__(self):
        STORE_DIR.mkdir(parents=True, exist_ok=True)
        self._missions: list[dict] = []
        self._load_all()

    def _load_all(self):
        """Load all mission files from disk."""
        self._missions = []
        for f in sorted(STORE_DIR.glob("*.json"), reverse=True):
            try:
                with open(f) as fh:
                    self._missions.append(json.load(fh))
            except Exception as e:
                logger.warning(f"Failed to load mission {f}: {e}")

    def save_mission(
        self,
        mission_id: str,
        goal: str,
        workspace_path: str,
        tasks: list[dict],
        cost_usd: float,
        duration_seconds: float,
        agents: list[str],
        status: str = "completed",
    ):
        """Save a completed mission to disk."""
        record = {
            "id": mission_id,
            "goal": goal,
            "workspace": workspace_path,
            "tasks": tasks,
            "cost_usd": round(cost_usd, 4),
            "duration_seconds": round(duration_seconds, 1),
            "agents": agents,
            "status": status,
            "timestamp": time.time(),
            "timestamp_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        filepath = STORE_DIR / f"{mission_id}.json"
        try:
            with open(filepath, "w") as fh:
                json.dump(record, fh, indent=2)
            self._missions.insert(0, record)
            logger.info(f"Mission saved: {mission_id}")
        except Exception as e:
            logger.error(f"Failed to save mission: {e}")

    def list_missions(self, limit: int = 50) -> list[dict]:
        """List past missions (most recent first)."""
        return [
            {
                "id": m["id"],
                "goal": m["goal"][:100],
                "status": m.get("status", "completed"),
                "cost_usd": m.get("cost_usd", 0),
                "duration_seconds": m.get("duration_seconds", 0),
                "timestamp": m.get("timestamp_iso", ""),
                "task_count": len(m.get("tasks", [])),
            }
            for m in self._missions[:limit]
        ]

    def get_mission(self, mission_id: str) -> Optional[dict]:
        """Get full details for a specific mission."""
        for m in self._missions:
            if m["id"] == mission_id:
                return m
        # Try loading from disk
        filepath = STORE_DIR / f"{mission_id}.json"
        if filepath.exists():
            try:
                with open(filepath) as fh:
                    return json.load(fh)
            except Exception:
                pass
        return None
