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

    def update_status(self, task_id: str, status: TaskStatus, agent_id: str = "") -> Task:
        if task_id not in self._tasks:
            raise ValueError(f"Task not found: {task_id}")
        task = self._tasks[task_id]
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
