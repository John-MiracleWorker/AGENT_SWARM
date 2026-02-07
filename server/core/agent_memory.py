"""
Agent Memory — Persists lessons and patterns across missions.
Agents extract insights from errors, successful patterns, and reviewer feedback.
Memories are auto-injected into system prompts for future missions.
"""

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MEMORY_DIR = Path.home() / ".agent_swarm" / "memory"


class AgentMemory:
    """Persistent memory store for agent-learned lessons."""

    def __init__(self):
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self._memories: list[dict] = []
        self._load_all()

    def _load_all(self):
        """Load all memories from disk."""
        self._memories = []
        filepath = MEMORY_DIR / "memories.json"
        if filepath.exists():
            try:
                with open(filepath) as fh:
                    self._memories = json.load(fh)
            except Exception as e:
                logger.warning(f"Failed to load memories: {e}")

    def _save_all(self):
        """Persist all memories to disk."""
        filepath = MEMORY_DIR / "memories.json"
        try:
            with open(filepath, "w") as fh:
                json.dump(self._memories, fh, indent=2)
        except Exception as e:
            logger.error(f"Failed to save memories: {e}")

    def save_lesson(
        self,
        agent_role: str,
        lesson: str,
        context: str = "",
        mission_id: str = "",
        lesson_type: str = "general",
    ):
        """
        Save a lesson learned by an agent.
        lesson_type: 'error_recovery', 'pattern', 'feedback', 'general'
        """
        memory = {
            "id": uuid.uuid4().hex[:12],
            "agent_role": agent_role,
            "lesson": lesson,
            "context": context,
            "mission_id": mission_id,
            "type": lesson_type,
            "timestamp": time.time(),
            "timestamp_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
            "use_count": 0,
        }
        self._memories.append(memory)
        self._save_all()
        logger.info(f"Memory saved [{agent_role}]: {lesson[:60]}...")

    def get_relevant_memories(self, agent_role: str, limit: int = 5) -> list[dict]:
        """
        Get memories relevant to an agent role.
        Returns most recent + most used memories for the role.
        """
        role_memories = [m for m in self._memories if m["agent_role"] == agent_role]
        general_memories = [m for m in self._memories if m["agent_role"] == "general"]

        # Sort by recency and usage
        combined = role_memories + general_memories
        combined.sort(key=lambda m: (m.get("use_count", 0), m.get("timestamp", 0)), reverse=True)

        # Mark as used
        for m in combined[:limit]:
            m["use_count"] = m.get("use_count", 0) + 1

        if combined[:limit]:
            self._save_all()

        return combined[:limit]

    def format_for_prompt(self, agent_role: str) -> str:
        """Format relevant memories as a system prompt injection."""
        memories = self.get_relevant_memories(agent_role)
        if not memories:
            return ""

        lines = ["## Lessons from Previous Missions"]
        for m in memories:
            lines.append(f"- [{m['type']}] {m['lesson']}")
            if m.get("context"):
                lines.append(f"  Context: {m['context']}")
        return "\n".join(lines)

    def list_memories(self, limit: int = 100) -> list[dict]:
        """List all memories for the UI."""
        return sorted(
            self._memories,
            key=lambda m: m.get("timestamp", 0),
            reverse=True,
        )[:limit]

    def delete_memory(self, memory_id: str):
        """Delete a specific memory."""
        self._memories = [m for m in self._memories if m["id"] != memory_id]
        self._save_all()
