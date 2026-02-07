"""
Task Manager — Task tracking with dependency graph, workflow pipeline, and status transitions.

Improvements:
- Task dependency graph: tasks can declare `depends_on` and are auto-blocked until deps complete
- Workflow pipeline: enforced status transitions (todo -> in_progress -> in_review -> done)
- Blocked status: automatically managed based on dependency resolution
- Dependency cycle detection: prevents circular dependencies
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


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Valid status transitions enforce the workflow pipeline
VALID_TRANSITIONS = {
    TaskStatus.TODO: {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED},
    TaskStatus.BLOCKED: {TaskStatus.TODO, TaskStatus.IN_PROGRESS},
    TaskStatus.IN_PROGRESS: {TaskStatus.IN_REVIEW, TaskStatus.BLOCKED, TaskStatus.TODO},
    TaskStatus.IN_REVIEW: {TaskStatus.DONE, TaskStatus.IN_PROGRESS},  # review can reject back
    TaskStatus.DONE: set(),  # terminal state — reopening requires orchestrator override
}


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
    priority: TaskPriority = TaskPriority.MEDIUM
    # Workflow tracking
    requires_review: bool = True  # Must go through review before done
    requires_testing: bool = False  # Must go through testing before done
    reviewed_by: Optional[str] = None
    tested_by: Optional[str] = None
    # Handoff tracking
    handoff_to: Optional[str] = None  # Next agent to hand off to
    handoff_reason: Optional[str] = None

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
            "priority": self.priority.value,
            "requires_review": self.requires_review,
            "requires_testing": self.requires_testing,
            "reviewed_by": self.reviewed_by,
            "tested_by": self.tested_by,
            "handoff_to": self.handoff_to,
            "handoff_reason": self.handoff_reason,
        }


class TaskManager:
    """Manages tasks with dependency graph and enforced workflow pipeline."""

    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self.planning_complete: bool = False
        self._status_change_callbacks: list = []

    def on_status_change(self, callback):
        """Register a callback for task status changes: callback(task, old_status, new_status)."""
        self._status_change_callbacks.append(callback)

    def create_task(
        self,
        title: str,
        description: str,
        created_by: str,
        assignee: Optional[str] = None,
        dependencies: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
        priority: str = "medium",
        requires_review: bool = True,
        requires_testing: bool = False,
    ) -> Task:
        task_id = str(uuid.uuid4())[:8]

        # Validate dependencies exist
        deps = dependencies or []
        valid_deps = []
        for dep_id in deps:
            if dep_id in self._tasks:
                valid_deps.append(dep_id)
            else:
                logger.warning(f"Task [{task_id}] dependency '{dep_id}' not found, skipping")

        # Detect circular dependencies
        if valid_deps and self._would_create_cycle(task_id, valid_deps):
            logger.error(f"Task [{task_id}] would create dependency cycle, removing conflicting deps")
            valid_deps = []

        # Auto-block if unresolved dependencies exist
        has_unresolved = any(
            self._tasks[d].status != TaskStatus.DONE for d in valid_deps
        )
        initial_status = TaskStatus.BLOCKED if has_unresolved else TaskStatus.TODO

        try:
            prio = TaskPriority(priority)
        except ValueError:
            prio = TaskPriority.MEDIUM

        task = Task(
            id=task_id,
            title=title,
            description=description,
            status=initial_status,
            assignee=assignee,
            created_by=created_by,
            dependencies=valid_deps,
            tags=tags or [],
            priority=prio,
            requires_review=requires_review,
            requires_testing=requires_testing,
        )
        self._tasks[task.id] = task
        status_note = f" (BLOCKED — waiting on {valid_deps})" if has_unresolved else ""
        logger.info(f"Task created: [{task.id}] {title} -> {assignee or 'unassigned'}{status_note}")
        return task

    def update_status(self, task_id: str, status: TaskStatus, agent_id: str = "") -> Task:
        """Update task status with workflow validation."""
        if task_id not in self._tasks:
            raise ValueError(f"Task not found: {task_id}")
        task = self._tasks[task_id]
        old_status = task.status

        # Validate transition (orchestrator can override)
        if agent_id != "orchestrator" and status not in VALID_TRANSITIONS.get(old_status, set()):
            raise ValueError(
                f"Invalid transition: {old_status.value} -> {status.value}. "
                f"Valid: {[s.value for s in VALID_TRANSITIONS.get(old_status, set())]}"
            )

        # Block transition to IN_PROGRESS if dependencies aren't met
        if status == TaskStatus.IN_PROGRESS:
            unresolved = self._get_unresolved_deps(task_id)
            if unresolved:
                task.status = TaskStatus.BLOCKED
                task.updated_at = time.time()
                dep_names = [f"{d.id}:{d.title}" for d in unresolved]
                logger.warning(f"Task [{task_id}] blocked — waiting on: {dep_names}")
                return task

        # Enforce review requirement before marking done
        if status == TaskStatus.DONE and task.requires_review and not task.reviewed_by:
            raise ValueError(
                f"Task [{task_id}] requires review before completion. "
                f"Move to 'in_review' first, then a reviewer must approve."
            )

        task.status = status
        task.updated_at = time.time()
        logger.info(f"Task [{task_id}] {old_status.value} -> {status.value}")

        # Fire callbacks
        for cb in self._status_change_callbacks:
            try:
                cb(task, old_status, status)
            except Exception as e:
                logger.error(f"Status change callback error: {e}")

        # If task completed, unblock dependents
        if status == TaskStatus.DONE:
            self._resolve_dependents(task_id)

        return task

    def mark_reviewed(self, task_id: str, reviewer_id: str) -> Task:
        """Mark a task as reviewed by a specific agent."""
        if task_id not in self._tasks:
            raise ValueError(f"Task not found: {task_id}")
        task = self._tasks[task_id]
        task.reviewed_by = reviewer_id
        task.updated_at = time.time()
        logger.info(f"Task [{task_id}] reviewed by {reviewer_id}")
        return task

    def mark_tested(self, task_id: str, tester_id: str) -> Task:
        """Mark a task as tested by a specific agent."""
        if task_id not in self._tasks:
            raise ValueError(f"Task not found: {task_id}")
        task = self._tasks[task_id]
        task.tested_by = tester_id
        task.updated_at = time.time()
        logger.info(f"Task [{task_id}] tested by {tester_id}")
        return task

    def set_handoff(self, task_id: str, target_agent: str, reason: str = "") -> Task:
        """Set a handoff target for a task — next agent to pick it up."""
        if task_id not in self._tasks:
            raise ValueError(f"Task not found: {task_id}")
        task = self._tasks[task_id]
        task.handoff_to = target_agent
        task.handoff_reason = reason
        task.updated_at = time.time()
        logger.info(f"Task [{task_id}] handoff -> {target_agent}: {reason}")
        return task

    def clear_handoff(self, task_id: str) -> Task:
        """Clear a handoff after it has been picked up."""
        if task_id not in self._tasks:
            raise ValueError(f"Task not found: {task_id}")
        task = self._tasks[task_id]
        task.handoff_to = None
        task.handoff_reason = None
        task.updated_at = time.time()
        return task

    def get_pending_handoffs(self, agent_id: str) -> list[Task]:
        """Get tasks that are being handed off TO a specific agent."""
        return [
            t for t in self._tasks.values()
            if t.handoff_to == agent_id
        ]

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

    def get_actionable_tasks(self, agent_id: str) -> list[Task]:
        """Get tasks that an agent can actually work on right now (not blocked)."""
        return [
            t for t in self._tasks.values()
            if t.assignee == agent_id
            and t.status in (TaskStatus.TODO, TaskStatus.IN_PROGRESS)
        ]

    def get_blocked_tasks(self) -> list[dict]:
        """Get all blocked tasks with their unresolved dependency info."""
        result = []
        for task in self._tasks.values():
            if task.status == TaskStatus.BLOCKED:
                unresolved = self._get_unresolved_deps(task.id)
                result.append({
                    "task": task.to_dict(),
                    "waiting_on": [
                        {"id": d.id, "title": d.title, "status": d.status.value}
                        for d in unresolved
                    ],
                })
        return result

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

    def get_dependency_graph(self) -> dict:
        """Return the full dependency graph for visualization."""
        graph = {}
        for task in self._tasks.values():
            graph[task.id] = {
                "title": task.title,
                "status": task.status.value,
                "assignee": task.assignee,
                "depends_on": task.dependencies,
                "blocks": [
                    t.id for t in self._tasks.values()
                    if task.id in t.dependencies
                ],
            }
        return graph

    @property
    def has_tasks(self) -> bool:
        """Whether any tasks have been created."""
        return len(self._tasks) > 0

    def mark_planning_complete(self):
        """Called by orchestrator after all initial tasks are created."""
        self.planning_complete = True
        logger.info("Planning phase complete — completion checks enabled")

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

    # --- Dependency graph internals ---

    def _get_unresolved_deps(self, task_id: str) -> list[Task]:
        """Get dependency tasks that are not yet DONE."""
        task = self._tasks.get(task_id)
        if not task:
            return []
        return [
            self._tasks[dep_id]
            for dep_id in task.dependencies
            if dep_id in self._tasks and self._tasks[dep_id].status != TaskStatus.DONE
        ]

    def _resolve_dependents(self, completed_task_id: str):
        """When a task completes, check if any blocked tasks can now be unblocked."""
        unblocked = []
        for task in self._tasks.values():
            if task.status != TaskStatus.BLOCKED:
                continue
            if completed_task_id not in task.dependencies:
                continue
            # Check if ALL dependencies are now resolved
            remaining = self._get_unresolved_deps(task.id)
            if not remaining:
                task.status = TaskStatus.TODO
                task.updated_at = time.time()
                unblocked.append(task)
                logger.info(f"Task [{task.id}] unblocked — all dependencies resolved")

        if unblocked:
            logger.info(f"Unblocked {len(unblocked)} tasks after [{completed_task_id}] completed")
        return unblocked

    def _would_create_cycle(self, task_id: str, new_deps: list[str]) -> bool:
        """Check if adding dependencies would create a circular dependency."""
        # BFS from each dependency to see if we can reach task_id
        for dep_id in new_deps:
            visited = set()
            queue = [dep_id]
            while queue:
                current = queue.pop(0)
                if current == task_id:
                    return True
                if current in visited:
                    continue
                visited.add(current)
                if current in self._tasks:
                    queue.extend(self._tasks[current].dependencies)
        return False
