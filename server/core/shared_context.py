"""
Shared Context Store — Cross-agent knowledge sharing for the swarm.

Provides a central place where agents record:
- File changes (who changed what, when, and why)
- Key decisions (architectural choices, trade-offs)
- Discovered issues (bugs, blockers, risks)
- Agent handoff context (what the next agent needs to know)

This replaces ad-hoc message-based context sharing with structured,
queryable knowledge that any agent can access during their think phase.
"""

import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class EntryType(str, Enum):
    FILE_CHANGE = "file_change"
    DECISION = "decision"
    ISSUE = "issue"
    HANDOFF_CONTEXT = "handoff_context"
    DEPENDENCY_INFO = "dependency_info"


@dataclass
class ContextEntry:
    id: int
    entry_type: EntryType
    agent_id: str
    agent_role: str
    content: str
    timestamp: float = field(default_factory=time.time)
    # Optional structured data
    files: list[str] = field(default_factory=list)
    task_id: Optional[str] = None
    target_agent: Optional[str] = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.entry_type.value,
            "agent_id": self.agent_id,
            "agent_role": self.agent_role,
            "content": self.content,
            "timestamp": self.timestamp,
            "files": self.files,
            "task_id": self.task_id,
            "target_agent": self.target_agent,
            "tags": self.tags,
        }


class SharedContextStore:
    """
    Central knowledge store shared across all agents.

    Agents write context entries when they:
    - Modify files (what changed and why)
    - Make decisions (trade-offs, approach chosen)
    - Discover issues (bugs, blockers)
    - Hand off work (what the next agent needs to know)
    - Discover dependency info (library versions, API contracts)
    """

    def __init__(self, max_entries: int = 200):
        self._entries: list[ContextEntry] = []
        self._max_entries = max_entries
        self._next_id = 1

    def record_file_change(
        self,
        agent_id: str,
        agent_role: str,
        files: list[str],
        description: str,
        task_id: Optional[str] = None,
    ) -> ContextEntry:
        """Record that an agent changed files."""
        entry = self._add_entry(
            entry_type=EntryType.FILE_CHANGE,
            agent_id=agent_id,
            agent_role=agent_role,
            content=description,
            files=files,
            task_id=task_id,
        )
        logger.info(f"[SharedCtx] {agent_id} changed {len(files)} file(s): {description[:80]}")
        return entry

    def record_decision(
        self,
        agent_id: str,
        agent_role: str,
        decision: str,
        rationale: str = "",
        files: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
    ) -> ContextEntry:
        """Record an architectural or implementation decision."""
        content = decision
        if rationale:
            content += f"\nRationale: {rationale}"
        entry = self._add_entry(
            entry_type=EntryType.DECISION,
            agent_id=agent_id,
            agent_role=agent_role,
            content=content,
            files=files or [],
            tags=tags or [],
        )
        logger.info(f"[SharedCtx] Decision by {agent_id}: {decision[:80]}")
        return entry

    def record_issue(
        self,
        agent_id: str,
        agent_role: str,
        issue: str,
        files: Optional[list[str]] = None,
        task_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> ContextEntry:
        """Record a discovered issue (bug, blocker, risk)."""
        entry = self._add_entry(
            entry_type=EntryType.ISSUE,
            agent_id=agent_id,
            agent_role=agent_role,
            content=issue,
            files=files or [],
            task_id=task_id,
            tags=tags or ["issue"],
        )
        logger.info(f"[SharedCtx] Issue from {agent_id}: {issue[:80]}")
        return entry

    def record_handoff(
        self,
        agent_id: str,
        agent_role: str,
        target_agent: str,
        context: str,
        files: Optional[list[str]] = None,
        task_id: Optional[str] = None,
    ) -> ContextEntry:
        """Record handoff context for the next agent working on a task."""
        entry = self._add_entry(
            entry_type=EntryType.HANDOFF_CONTEXT,
            agent_id=agent_id,
            agent_role=agent_role,
            content=context,
            files=files or [],
            task_id=task_id,
            target_agent=target_agent,
        )
        logger.info(f"[SharedCtx] Handoff {agent_id} -> {target_agent}: {context[:80]}")
        return entry

    # --- Query methods ---

    def get_file_history(self, file_path: str, limit: int = 10) -> list[dict]:
        """Get all context entries related to a specific file."""
        entries = [
            e for e in self._entries
            if file_path in e.files
        ]
        return [e.to_dict() for e in entries[-limit:]]

    def get_task_context(self, task_id: str) -> list[dict]:
        """Get all context entries related to a specific task."""
        entries = [
            e for e in self._entries
            if e.task_id == task_id
        ]
        return [e.to_dict() for e in entries]

    def get_handoff_context(self, agent_id: str) -> list[dict]:
        """Get handoff context entries targeted at a specific agent."""
        entries = [
            e for e in self._entries
            if e.target_agent == agent_id
            and e.entry_type == EntryType.HANDOFF_CONTEXT
        ]
        return [e.to_dict() for e in entries]

    def get_recent_decisions(self, limit: int = 10) -> list[dict]:
        """Get recent decisions for context."""
        decisions = [
            e for e in self._entries
            if e.entry_type == EntryType.DECISION
        ]
        return [e.to_dict() for e in decisions[-limit:]]

    def get_open_issues(self) -> list[dict]:
        """Get all recorded issues."""
        issues = [
            e for e in self._entries
            if e.entry_type == EntryType.ISSUE
        ]
        return [e.to_dict() for e in issues]

    def get_agent_summary(self, agent_id: str, limit: int = 15) -> list[dict]:
        """Get recent entries by a specific agent."""
        entries = [
            e for e in self._entries
            if e.agent_id == agent_id
        ]
        return [e.to_dict() for e in entries[-limit:]]

    def get_summary_for_agent(self, agent_id: str, max_tokens: int = 2000) -> str:
        """
        Build a compact text summary of shared context relevant to an agent.
        Used to inject into agent system prompts for awareness.
        """
        sections = []

        # 1. Handoff context directed at this agent
        handoffs = self.get_handoff_context(agent_id)
        if handoffs:
            lines = [f"  - [{h['agent_id']}] {h['content'][:150]}" for h in handoffs[-3:]]
            sections.append("Handoffs for you:\n" + "\n".join(lines))

        # 2. Recent decisions
        decisions = self.get_recent_decisions(5)
        if decisions:
            lines = [f"  - [{d['agent_id']}] {d['content'][:120]}" for d in decisions]
            sections.append("Recent decisions:\n" + "\n".join(lines))

        # 3. Recent file changes
        file_changes = [
            e for e in self._entries
            if e.entry_type == EntryType.FILE_CHANGE
        ][-5:]
        if file_changes:
            lines = [f"  - [{e.agent_id}] {', '.join(e.files[:3])}: {e.content[:80]}" for e in file_changes]
            sections.append("Recent file changes:\n" + "\n".join(lines))

        # 4. Open issues
        issues = self.get_open_issues()[-3:]
        if issues:
            lines = [f"  - [{i['agent_id']}] {i['content'][:120]}" for i in issues]
            sections.append("Known issues:\n" + "\n".join(lines))

        if not sections:
            return ""

        result = "## Shared Team Context\n" + "\n\n".join(sections)
        # Rough token limit
        if len(result) > max_tokens * 4:
            result = result[:max_tokens * 4] + "\n... (truncated)"
        return result

    def clear(self):
        """Clear all entries for a new mission."""
        self._entries.clear()
        self._next_id = 1

    # --- Internals ---

    def _add_entry(
        self,
        entry_type: EntryType,
        agent_id: str,
        agent_role: str,
        content: str,
        files: Optional[list[str]] = None,
        task_id: Optional[str] = None,
        target_agent: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> ContextEntry:
        entry = ContextEntry(
            id=self._next_id,
            entry_type=entry_type,
            agent_id=agent_id,
            agent_role=agent_role,
            content=content,
            files=files or [],
            task_id=task_id,
            target_agent=target_agent,
            tags=tags or [],
        )
        self._next_id += 1
        self._entries.append(entry)

        # Evict oldest entries if over limit
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]

        return entry
