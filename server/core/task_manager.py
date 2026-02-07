"""
Task Manager — Task tracking with status transitions and message bus integration.
"""

import uuid
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    DONE = "done"
    BLOCKED = "blocked"
    WAITING = "waiting"


@dataclass
class Task:
    id: str
    title: str
    description: str
    status: TaskStatus
    assignee: Optional[str] = None
    created_by: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    dependencies: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "assignee": self.assignee,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "dependencies": self.dependencies,
            "tags": self.tags,
        }


class TaskManager:
    """Manages tasks for a mission."""

    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self.planning_complete: bool = False

    def create_task(
        self,
        title: str,
        description: str,
        created_by: str,
        assignee: Optional[str] = None,
        dependencies: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
    ) -> Task:
        task = Task(
            id=str(uuid.uuid4())[:8],
            title=title,
            description=description,
            status=TaskStatus.TODO,
            assignee=assignee,
            created_by=created_by,
            dependencies=dependencies or [],
            tags=tags or [],
        )
        self._tasks[task.id] = task
        logger.info(f"Task created: [{task.id}] {title} -> {assignee or 'unassigned'}")
        return task

    def _resolve_task(self, task_id_or_title: str) -> Task:
        """Resolve a task by ID, exact title, or substring title match."""
        # 1. Direct ID match
        if task_id_or_title in self._tasks:
            return self._tasks[task_id_or_title]

        # 2. Exact title match (case-insensitive)
        needle = task_id_or_title.strip().lower()
        for task in self._tasks.values():
            if task.title.strip().lower() == needle:
                logger.info(f"Resolved task by title: '{task_id_or_title}' → [{task.id}]")
                return task

        # 3. Substring match (LLMs often abbreviate)
        matches = [
            t for t in self._tasks.values()
            if needle in t.title.lower() or t.title.lower() in needle
        ]
        if len(matches) == 1:
            logger.info(f"Resolved task by substring: '{task_id_or_title}' → [{matches[0].id}]")
            return matches[0]

        raise ValueError(f"Task not found: {task_id_or_title}")

    def update_status(self, task_id: str, status: TaskStatus, agent_id: str = "") -> Task:
        task = self._resolve_task(task_id)
        old_status = task.status
        task.status = status
        task.updated_at = time.time()
        logger.info(f"Task [{task_id}] {old_status.value} -> {status.value}")
        return task

    def assign_task(self, task_id: str, assignee: str) -> Task:
        if task_id not in self._tasks:
            raise ValueError(f"Task not found: {task_id}")
        task = self._tasks[task_id]
        task.assignee = assignee
        task.updated_at = time.time()
        logger.info(f"Task [{task_id}] assigned to {assignee}")
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def get_tasks_for_agent(self, agent_id: str) -> list[Task]:
        return [t for t in self._tasks.values() if t.assignee == agent_id]

    def get_tasks_by_status(self, status: TaskStatus) -> list[Task]:
        return [t for t in self._tasks.values() if t.status == status]

    def list_tasks(self) -> list[dict]:
        return [t.to_dict() for t in self._tasks.values()]

    def get_summary(self) -> dict:
        """Get task board summary."""
        return {
            "total": len(self._tasks),
            "todo": len(self.get_tasks_by_status(TaskStatus.TODO)),
            "in_progress": len(self.get_tasks_by_status(TaskStatus.IN_PROGRESS)),
            "in_review": len(self.get_tasks_by_status(TaskStatus.IN_REVIEW)),
            "done": len(self.get_tasks_by_status(TaskStatus.DONE)),
            "blocked": len(self.get_tasks_by_status(TaskStatus.BLOCKED)),
        }

    @property
    def has_tasks(self) -> bool:
        """Whether any tasks have been created."""
        return len(self._tasks) > 0

    def mark_planning_complete(self):
        """Called by orchestrator after all initial tasks are created."""
        self.planning_complete = True
        logger.info("📋 Planning phase complete — completion checks enabled")

    @property
    def all_done(self) -> bool:
        """Check if all tasks are complete. Only valid after planning is finalized."""
        if not self.planning_complete:
            return False
        if not self._tasks:
            return False
        return all(t.status == TaskStatus.DONE for t in self._tasks.values())

    def clear(self):
        """Clear all tasks for a new mission."""
        self._tasks.clear()
        self.planning_complete = False

    def format_task_board(self) -> str:
        """Format task board for inclusion in agent system prompts."""
        if not self._tasks:
            return "No tasks created yet."
        status_emoji = {
            TaskStatus.TODO: "⬜",
            TaskStatus.IN_PROGRESS: "🔵",
            TaskStatus.IN_REVIEW: "🟡",
            TaskStatus.DONE: "✅",
            TaskStatus.BLOCKED: "🔴",
            TaskStatus.WAITING: "⏳",
        }
        lines = []
        for t in self._tasks.values():
            emoji = status_emoji.get(t.status, "❓")
            assignee = f" → {t.assignee}" if t.assignee else ""
            lines.append(f"- {emoji} [{t.id}] {t.title}{assignee}")
        summary = self.get_summary()
        header = f"**{summary.get('done', 0)}/{summary.get('total', 0)} done**"
        return header + "\n" + "\n".join(lines)

