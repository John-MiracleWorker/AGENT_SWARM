"""
Base Agent — Abstract base class for all agents in the swarm.
Implements the observe → think → act event loop.
"""

import asyncio
import json
import logging
import os
import subprocess
import time
import re
import uuid
from abc import ABC, abstractmethod
from typing import Optional
from enum import Enum

from server.core.message_bus import MessageBus, MessageType, Message
from server.core.model_router import ModelRouter as GeminiClient, BudgetExhaustedError
from server.core.workspace import WorkspaceManager
from server.core.task_manager import TaskManager, TaskStatus
from server.core.terminal import TerminalExecutor
from server.core.context_manager import ContextManager

logger = logging.getLogger(__name__)

# Error recovery constants
MAX_CONSECUTIVE_ERRORS = 5
RETRY_BACKOFF_BASE = 2
MAX_RETRY_ATTEMPTS = 3

# ─── Role-based file operation enforcement ─────────────────────
# Roles that are allowed to perform write operations (edit_file, write_file)
WRITE_ROLES = {"Developer", "Orchestrator"}
# Tester can write, but ONLY to files matching these patterns
TESTER_WRITE_ALLOWED = True
TESTER_WRITE_PATTERNS = ("test_", "tests/", "spec/", "__tests__/", "_test.", ".test.")
# Reviewer is NEVER allowed to write files
REVIEWER_WRITE_ALLOWED = False   # Reviewers cannot write ANY files

# ─── Safe terminal commands (auto-approved, no user confirmation needed) ───
SAFE_COMMAND_PREFIXES = (
    "python3 -m pytest", "python -m pytest", "pytest",
    "python3 -m py_compile", "python -m py_compile",
    "python3 -c", "python -c",
    "cat ", "head ", "tail ", "wc ",
    "ls", "find ", "grep ", "rg ",
    "echo ", "pwd", "which ", "whoami",
    "tree ", "file ", "stat ",
    "diff ", "sort ", "uniq ",
    "node -e", "node --version", "npm list", "npm test", "npm run test",
)
DESTRUCTIVE_PATTERNS = (
    "rm ", "rm -", "rmdir", "mv ", "cp ",
    "pip install", "pip3 install", "npm install", "yarn add",
    "brew ", "apt ", "sudo ",
    "chmod ", "chown ",
    "kill ", "pkill ",
    "curl ", "wget ",
    "> ", ">> ", "| tee",
)


class AgentStatus(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    ACTING = "acting"
    WAITING = "waiting"
    PAUSED = "paused"
    STOPPED = "stopped"


class BaseAgent(ABC):
    """
    Base agent with observe → think → act loop.
    All specialized agents inherit from this.
    """

    def __init__(
        self,
        agent_id: str,
        role: str,
        emoji: str,
        color: str,
        gemini: GeminiClient,
        message_bus: MessageBus,
        workspace: WorkspaceManager,
        task_manager: TaskManager,
        terminal: TerminalExecutor,
        context_manager: ContextManager,
    ):
        self.agent_id = agent_id
        self.role = role
        self.emoji = emoji
        self.color = color
        self.gemini = gemini
        self.bus = message_bus
        self.workspace = workspace
        self.tasks = task_manager
        self.terminal = terminal
        self.context = context_manager

        self.status = AgentStatus.IDLE
        self._inbox: asyncio.Queue = self.bus.subscribe_agent(agent_id)
        self._messages_history: list[dict] = []
        self._running = False
        self._paused = False
        self._pending_approvals: dict[str, asyncio.Future] = {}
        self._loop_task: Optional[asyncio.Task] = None
        self._consecutive_errors: int = 0
        self._error_backoff: float = 1.0
        self._last_thinking_broadcast: float = 0  # throttle thought broadcasts
        self.model_override: Optional[str] = None  # Pin to specific model (e.g. senior dev)
        self._task_failures: dict[str, int] = {}  # task_id → consecutive failure count
        self._task_last_error: dict[str, str] = {}  # task_id → last error description

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """Role-specific system prompt for Gemini."""
        pass

    def get_status_dict(self) -> dict:
        return {
            "id": self.agent_id,
            "role": self.role,
            "emoji": self.emoji,
            "color": self.color,
            "status": self.status.value,
        }

    async def start(self):
        """Start the agent's event loop."""
        self._running = True
        self._loop_task = asyncio.create_task(self._event_loop())
        logger.info(f"[{self.agent_id}] Started")

    async def stop(self):
        """Stop the agent."""
        self._running = False
        self.status = AgentStatus.STOPPED
        if self._loop_task:
            self._loop_task.cancel()
        self.bus.unsubscribe_agent(self.agent_id)
        logger.info(f"[{self.agent_id}] Stopped")

    def pause(self):
        self._paused = True
        self.status = AgentStatus.PAUSED

    def resume(self):
        self._paused = False

    def _has_assigned_tasks(self) -> bool:
        """Check if this agent has any tasks assigned by the orchestrator."""
        tasks = self.tasks.get_tasks_for_agent(self.agent_id)
        return len(tasks) > 0

    def _should_wait_for_tasks(self) -> bool:
        """
        Non-orchestrator agents should wait for tasks before acting.
        Override in orchestrator to return False.
        """
        return True

    async def _event_loop(self):
        """Main observe → think → act loop."""
        while self._running:
            try:
                if self._paused:
                    self.status = AgentStatus.PAUSED
                    await asyncio.sleep(1)
                    continue

                # GATE: Non-orchestrator agents wait for task assignments OR actionable messages
                if self._should_wait_for_tasks() and not self._has_assigned_tasks():
                    # Collect messages but check if any are actionable (not just system noise)
                    pending = await self._observe()
                    actionable = any(
                        m.msg_type in (
                            MessageType.TASK_ASSIGNED,
                            MessageType.REVIEW_REQUEST,
                            MessageType.REVIEW_RESULT,
                            MessageType.ASK_HELP,
                            MessageType.SHARE_INSIGHT,
                            MessageType.PROPOSE_APPROACH,
                            MessageType.CHALLENGE,
                        )
                        or self.agent_id in m.mentions
                        for m in pending
                    )
                    if not actionable:
                        # Buffer messages so they're not lost
                        for m in pending:
                            self._inbox.put_nowait(m)
                        self.status = AgentStatus.IDLE
                        await asyncio.sleep(2)
                        continue
                    # Actionable message received — put messages back and fall through to normal flow
                    for m in pending:
                        self._inbox.put_nowait(m)

                # OBSERVE — collect new messages
                new_messages = await self._observe()

                if not new_messages and not self._should_act_without_messages():
                    self.status = AgentStatus.IDLE
                    await asyncio.sleep(2)
                    continue

                # THINK — call Gemini for a decision
                self.status = AgentStatus.THINKING
                await self._broadcast_status()

                action = await self._think(new_messages)
                if not action:
                    await asyncio.sleep(2)
                    continue

                # ACT — execute the action
                self.status = AgentStatus.ACTING
                await self._broadcast_status()
                await self._act(action)

                # ─── Per-task failure tracking for self-debugging ──────
                # Check if the action resulted in an error by scanning recent history
                action_type = action.get("action", "")
                active_task_id = action.get("params", {}).get("task_id", "")
                # If no explicit task_id, find the current in-progress task
                if not active_task_id:
                    in_progress = [
                        t for t in self.tasks.get_tasks_for_agent(self.agent_id)
                        if hasattr(t, 'status') and t.status.value == "in_progress"
                    ]
                    if in_progress:
                        active_task_id = in_progress[0].id

                if active_task_id and self._messages_history:
                    last_msg = self._messages_history[-1].get("content", "")
                    error_signals = ["error", "Error", "failed", "Failed", "❌", "BLOCKED", "Cannot"]
                    if any(sig in last_msg for sig in error_signals) and action_type in (
                        "write_file", "edit_file", "run_command", "use_terminal"
                    ):
                        self._task_failures[active_task_id] = self._task_failures.get(active_task_id, 0) + 1
                        # Store the error for introspection context
                        self._task_last_error[active_task_id] = last_msg[:300]
                        logger.info(
                            f"[{self.agent_id}] Task [{active_task_id}] failure #{self._task_failures[active_task_id]}"
                        )
                    else:
                        # Reset on successful action for this task
                        if active_task_id in self._task_failures and action_type in (
                            "write_file", "edit_file", "run_command", "use_terminal"
                        ):
                            self._task_failures[active_task_id] = 0

                # Reset error counter on success & notify router
                self._consecutive_errors = 0
                self._error_backoff = 1.0
                # De-escalate model routing on success
                self.gemini.record_agent_success(self.agent_id)

                # Delay between cycles to prevent tight loops
                await asyncio.sleep(3)

            except asyncio.CancelledError:
                break
            except BudgetExhaustedError as be:
                logger.warning(f"[{self.agent_id}] Budget exhausted: {be}")
                await self.bus.publish(Message(
                    sender=self.agent_id,
                    sender_role=self.role,
                    msg_type=MessageType.STATUS,
                    content=f"⚠️ Budget limit reached — agent paused",
                    target="broadcast",
                ))
                # Trigger mission complete on budget exhaustion
                try:
                    await self._trigger_mission_complete()
                except Exception:
                    pass
                break
            except Exception as e:
                self._consecutive_errors += 1
                # Track failure for model escalation
                self.gemini.record_agent_failure(self.agent_id)
                logger.error(
                    f"[{self.agent_id}] Error #{self._consecutive_errors}: {e}",
                    exc_info=True,
                )

                if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.error(f"[{self.agent_id}] Too many errors ({MAX_CONSECUTIVE_ERRORS}), auto-pausing")
                    # Save lesson about the failure
                    try:
                        from server.main import state
                        state.agent_memory.save_lesson(
                            agent_role=self.role,
                            lesson=f"Repeated failure: {str(e)[:200]}",
                            context=f"Failed {MAX_CONSECUTIVE_ERRORS} times consecutively",
                            mission_id=getattr(state, 'mission_id', ''),
                            lesson_type="error_recovery",
                        )
                    except Exception:
                        pass
                    await self.bus.publish(Message(
                        sender=self.agent_id,
                        sender_role=self.role,
                        msg_type=MessageType.STATUS,
                        content=f"🔴 Auto-paused after {MAX_CONSECUTIVE_ERRORS} consecutive errors: {str(e)[:100]}",
                        target="broadcast",
                    ))
                    self.pause()
                else:
                    # Exponential backoff
                    wait = min(self._error_backoff * RETRY_BACKOFF_BASE, 30)
                    self._error_backoff = wait
                    logger.info(f"[{self.agent_id}] Retrying in {wait:.1f}s...")
                    await asyncio.sleep(wait)

    def _should_act_without_messages(self) -> bool:
        """Override in subclass if agent should act proactively."""
        return False

    async def _observe(self) -> list[Message]:
        """Read new messages from the bus."""
        messages = []
        try:
            while True:
                msg = self._inbox.get_nowait()
                messages.append(msg)
        except asyncio.QueueEmpty:
            pass
        return messages

    async def _think(self, new_messages: list[Message]) -> Optional[dict]:
        """Call Gemini with context and get a structured action response."""
        # Add new messages to history
        for msg in new_messages:
            self._messages_history.append({
                "role": "user",
                "content": f"[{msg.sender_role} @{msg.sender}] ({msg.msg_type.value}): {msg.content}"
                + (f"\nData: {json.dumps(msg.data)}" if msg.data else ""),
            })

        if not self._messages_history:
            return None

        # ─── Self-debugging introspection ─────────────────────
        # If the agent has been failing on a task, inject a reflection prompt
        # that forces it to reason about WHY instead of blindly retrying.
        current_tasks = self.tasks.get_tasks_for_agent(self.agent_id)
        for task in current_tasks:
            tid = task.id if hasattr(task, 'id') else str(task)
            fail_count = self._task_failures.get(tid, 0)
            if fail_count >= 2:
                last_err = self._task_last_error.get(tid, "unknown error")
                reflection = (
                    f"[System — Self-Reflection Required] ⚠️ You have failed {fail_count} times on task [{tid}]. "
                    f"Last error: {last_err}\n\n"
                    f"STOP and think critically before your next attempt:\n"
                    f"1. What specific error did you hit and WHY did it occur?\n"
                    f"2. Why did your previous approach fail fundamentally (not just syntactically)?\n"
                    f"3. What DIFFERENT approach could work? Don't retry the same thing.\n"
                    f"4. Would another agent's expertise help? Use `ask_help` to get input.\n"
                    f"5. Should you `propose_approach` to get feedback before coding?\n\n"
                    f"Think deeply. A different strategy is needed — not the same approach with small tweaks."
                )
                # Don't inject the same reflection multiple times
                already_reflected = any(
                    "Self-Reflection Required" in m.get("content", "") and tid in m.get("content", "")
                    for m in self._messages_history[-5:]
                )
                if not already_reflected:
                    self._messages_history.append({
                        "role": "user",
                        "content": reflection,
                    })
                    logger.info(f"[{self.agent_id}] Injected self-reflection for task [{tid}] (failures={fail_count})")

        # Trim context if needed
        trimmed = self.context.trim_messages(self._messages_history)

        # Broadcast thinking bubble (throttled — max once per 10s)
        import time as _time
        now = _time.time()
        if now - self._last_thinking_broadcast >= 10:
            self._last_thinking_broadcast = now
            await self.bus.publish(
                sender=self.agent_id,
                sender_role=self.role,
                msg_type=MessageType.THOUGHT,
                content="Analyzing context and deciding next action...",
            )

        try:
            action = await self.gemini.generate(
                agent_id=self.agent_id,
                system_prompt=self.system_prompt,
                messages=trimmed,
                model_override=self.model_override,
            )

            # Broadcast thought bubble with reasoning
            thinking = action.get("thinking", "")
            if thinking:
                await self.bus.publish(
                    sender=self.agent_id,
                    sender_role=self.role,
                    msg_type=MessageType.THOUGHT,
                    content=thinking,
                )

            # Add Gemini's response to history
            self._messages_history.append({
                "role": "model",
                "content": json.dumps(action),
            })

            return action

        except Exception as e:
            logger.error(f"[{self.agent_id}] Think failed: {e}")
            # Broadcast error to chat feed so it's visible in the UI
            try:
                await self.bus.publish(
                    sender=self.agent_id,
                    sender_role=self.role,
                    msg_type=MessageType.SYSTEM,
                    content=f"⚠️ Think error: {str(e)[:200]}",
                )
            except Exception:
                pass
            return None

    def _check_write_permission(self, action_type: str, path: str) -> Optional[str]:
        """
        Check if this agent's role is allowed to perform a write operation.
        Returns an error message if blocked, None if allowed.
        """
        # Developers and Orchestrators have full write access
        if self.role in WRITE_ROLES:
            return None

        # Reviewer — NEVER allowed to write
        if self.role == "Reviewer":
            return (
                f"❌ As a Reviewer, you cannot use '{action_type}'. "
                f"Your role is to READ and REVIEW code, then use 'suggest_task' "
                f"to ask the Orchestrator to create fix tasks for the Developer."
            )

        # Tester — can only write test files
        if self.role == "Tester":
            if not any(pattern in path for pattern in TESTER_WRITE_PATTERNS):
                return (
                    f"❌ As a Tester, you can only write TEST files "
                    f"(paths containing: {', '.join(TESTER_WRITE_PATTERNS)}). "
                    f"'{path}' is a production file. Use 'suggest_task' to ask "
                    f"the Orchestrator to create a fix task for the Developer."
                )
            return None

        # Dynamic agents — check their capability set
        # (DynamicAgent stores available actions in _available_actions)
        if hasattr(self, '_available_actions'):
            if action_type not in self._available_actions:
                return (
                    f"❌ Action '{action_type}' is not in your capability set. "
                    f"Available actions: {', '.join(sorted(self._available_actions))}"
                )

        return None

    def _is_safe_command(self, command: str) -> bool:
        """Check if a terminal command is safe to auto-approve without user confirmation."""
        cmd = command.strip()
        # Explicit destructive patterns always need approval
        if any(pat in cmd for pat in DESTRUCTIVE_PATTERNS):
            return False
        # Commands with pipes or redirects need approval (except safe ones)
        if '|' in cmd and not cmd.startswith(('grep ', 'cat ')):
            return False
        # Check against known-safe prefixes
        return any(cmd.startswith(prefix) for prefix in SAFE_COMMAND_PREFIXES)

    async def _validate_python_syntax(self, path: str):
        """Run py_compile on a Python file after write/edit and report errors to the agent."""
        if not path.endswith('.py'):
            return
        try:
            full_path = self.workspace._validate_path(path)
            result = subprocess.run(
                ['python3', '-m', 'py_compile', str(full_path)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                error_msg = (result.stderr or result.stdout).strip()
                logger.warning(f"[{self.agent_id}] Syntax error in {path}: {error_msg}")
                self._messages_history.append({
                    "role": "user",
                    "content": (
                        f"[System] ⚠️ SYNTAX ERROR detected in '{path}' after your edit:\n"
                        f"```\n{error_msg}\n```\n"
                        f"You MUST fix this immediately using edit_file. "
                        f"First read_file to see the current state, then fix the broken lines."
                    ),
                })
            else:
                logger.debug(f"[{self.agent_id}] Syntax OK: {path}")
        except Exception as e:
            logger.debug(f"[{self.agent_id}] Syntax check skipped for {path}: {e}")

    async def _act(self, action: dict):
        """Execute a structured action from Gemini."""
        action_type = action.get("action", "message")
        params = action.get("params", {})
        message = action.get("message", "")

        try:
            # ─── Role-based write enforcement ─────────────────────
            if action_type in ("write_file", "edit_file"):
                path = params.get("path", "")
                err = self._check_write_permission(action_type, path)
                if err:
                    logger.warning(f"[{self.agent_id}] ROLE BLOCKED: {action_type} on '{path}' — {self.role}")
                    self._messages_history.append({
                        "role": "user",
                        "content": f"[System] {err}",
                    })
                    return

            # Checkpoint gate — check action against rules before executing
            try:
                from server.main import state as app_state
                checkpoint_match = app_state.checkpoints.check_action(action_type, params)
                if checkpoint_match:
                    if checkpoint_match["action"] == "pause":
                        await self._request_approval(
                            action_type, params,
                            f"🚧 Checkpoint: {checkpoint_match['label']}. Agent wants to: {action_type}"
                        )
                        return
                    elif checkpoint_match["action"] == "confirm":
                        await self._request_approval(
                            action_type, params,
                            f"⚠️ Requires confirmation: {checkpoint_match['label']}"
                        )
                        return
            except ImportError:
                pass
            if action_type == "write_file":
                path = params.get("path", "")
                content = params.get("content", "")
                # Block write_file on existing files — must use edit_file
                try:
                    full = self.workspace._validate_path(path)
                    if full.exists():
                        logger.warning(
                            f"[{self.agent_id}] Blocked write_file on existing file '{path}' — must use edit_file"
                        )
                        self._messages_history.append({
                            "role": "user",
                            "content": (
                                f"[System] ❌ Cannot use write_file on existing file '{path}'. "
                                f"write_file OVERWRITES the entire file and destroys other changes. "
                                f"Use edit_file instead to make targeted modifications. "
                                f"First use read_file to see the current content, then use edit_file "
                                f"with the exact 'search' text you want to change."
                            ),
                        })
                        return
                except Exception:
                    pass  # If path validation fails, let write_file handle it
                try:
                    diff = await self.workspace.write_file(path, content, agent_id=self.agent_id)
                    await self.bus.publish(
                        sender=self.agent_id,
                        sender_role=self.role,
                        msg_type=MessageType.FILE_UPDATE,
                        content=f"Wrote file: {path}",
                        data={"diff": diff, "path": path},
                    )
                    # Post-write syntax validation for Python files
                    await self._validate_python_syntax(path)
                except (FileNotFoundError, ValueError) as e:
                    self._messages_history.append({
                        "role": "user",
                        "content": f"[write_file error]: {str(e)}",
                    })

            elif action_type == "edit_file":
                path = params.get("path", "")
                search = params.get("search", "")
                replace = params.get("replace", "")
                if not search:
                    self._messages_history.append({
                        "role": "user",
                        "content": "[System] edit_file requires a non-empty 'search' parameter.",
                    })
                    return
                try:
                    diff = await self.workspace.edit_file(path, search, replace, agent_id=self.agent_id)
                    await self.bus.publish(
                        sender=self.agent_id,
                        sender_role=self.role,
                        msg_type=MessageType.FILE_UPDATE,
                        content=f"Edited file: {path}",
                        data={"diff": diff, "path": path},
                    )
                    # Post-edit syntax validation for Python files
                    await self._validate_python_syntax(path)
                except (FileNotFoundError, ValueError) as e:
                    self._messages_history.append({
                        "role": "user",
                        "content": f"[edit_file error]: {str(e)}",
                    })

            elif action_type == "read_file":
                path = params.get("path", "")
                content = await self.workspace.read_file(path, agent_id=self.agent_id)
                # Add file content to agent's context
                self._messages_history.append({
                    "role": "user",
                    "content": f"[File content of {path}]:\n```\n{content}\n```",
                })

            elif action_type == "run_command":
                command = params.get("command", "")
                # Auto-approve safe commands, require approval for potentially destructive ones
                if not self._is_safe_command(command):
                    approved = await self._request_approval(
                        "run_command", params,
                        f"🖥️ [{self.agent_id}] wants to run: `{command}`",
                    )
                    if not approved:
                        self._messages_history.append({
                            "role": "user",
                            "content": f"[System] Command REJECTED by user: `{command}`. Try a different approach or ask for guidance.",
                        })
                        return

                result = await self.terminal.execute(
                    command=command,
                    cwd=str(self.workspace.root),
                )
                await self.bus.publish(
                    sender=self.agent_id,
                    sender_role=self.role,
                    msg_type=MessageType.TERMINAL_OUTPUT,
                    content=f"$ {command}",
                    data=result.to_dict(),
                )
                # Feed output back to agent
                self._messages_history.append({
                    "role": "user",
                    "content": f"[Command output for `{command}`]:\nstdout: {result.stdout[:2000]}\nstderr: {result.stderr[:1000]}\nReturn code: {result.return_code}",
                })

            elif action_type == "create_task":
                # Only orchestrator can create tasks
                if self.role != "Orchestrator":
                    self._messages_history.append({
                        "role": "user",
                        "content": "[System] Only the Orchestrator can create tasks. Use `suggest_task` to suggest tasks to the Orchestrator.",
                    })
                else:
                    task = self.tasks.create_task(
                        title=params.get("title", ""),
                        description=params.get("description", ""),
                        created_by=self.agent_id,
                        assignee=params.get("assignee"),
                        dependencies=params.get("dependencies", []),
                        tags=params.get("tags", []),
                    )
                    await self.bus.publish(
                        sender=self.agent_id,
                        sender_role=self.role,
                        msg_type=MessageType.TASK_ASSIGNED,
                        content=f"Created task: {task.title}",
                        data=task.to_dict(),
                        mentions=[params.get("assignee", "")],
                    )

            elif action_type == "create_tasks":
                # Batch task creation — orchestrator only
                if self.role != "Orchestrator":
                    self._messages_history.append({
                        "role": "user",
                        "content": "[System] Only the Orchestrator can create tasks.",
                    })
                else:
                    task_list = params.get("tasks", [])
                    created = []
                    for t in task_list:
                        task = self.tasks.create_task(
                            title=t.get("title", ""),
                            description=t.get("description", ""),
                            created_by=self.agent_id,
                            assignee=t.get("assignee"),
                            dependencies=t.get("dependencies", []),
                            tags=t.get("tags", []),
                        )
                        created.append(task)
                    # Broadcast all tasks at once
                    await self.bus.publish(
                        sender=self.agent_id,
                        sender_role=self.role,
                        msg_type=MessageType.TASK_ASSIGNED,
                        content=f"📋 Created {len(created)} tasks for the mission",
                        data={"tasks": [t.to_dict() for t in created]},
                    )
                    self._messages_history.append({
                        "role": "user",
                        "content": f"[System] Successfully created {len(created)} tasks. Now call `finalize_plan` to enable completion checks.",
                    })

            elif action_type == "finalize_plan":
                # Orchestrator signals planning is complete
                if self.role != "Orchestrator":
                    self._messages_history.append({
                        "role": "user",
                        "content": "[System] Only the Orchestrator can finalize the plan.",
                    })
                else:
                    self.tasks.mark_planning_complete()
                    await self.bus.publish(
                        sender=self.agent_id,
                        sender_role=self.role,
                        msg_type=MessageType.SYSTEM,
                        content=f"📋 Plan finalized with {len(self.tasks.list_tasks())} tasks — agents can now work!",
                    )

            elif action_type == "suggest_task":
                # Non-orchestrator agents suggest tasks to the orchestrator
                # Dedup: check if a similar suggestion was recently made
                suggestion_title = params.get('title', '')
                suggestion_key = suggestion_title.lower().strip()
                if not hasattr(self, '_recent_suggestions'):
                    self._recent_suggestions = {}
                # Prune suggestions older than 2 minutes
                now = time.time()
                self._recent_suggestions = {
                    k: v for k, v in self._recent_suggestions.items()
                    if now - v < 120
                }
                if suggestion_key in self._recent_suggestions:
                    logger.info(f"[{self.agent_id}] Deduped suggestion: {suggestion_title}")
                else:
                    self._recent_suggestions[suggestion_key] = now
                    await self.bus.publish(
                        sender=self.agent_id,
                        sender_role=self.role,
                        msg_type=MessageType.CHAT,
                        content=f"💡 Task suggestion: {suggestion_title}\nReason: {params.get('reason', message)}",
                        data={"suggestion": params},
                        mentions=["orchestrator"],
                    )

            elif action_type == "update_task":
                task_id = params.get("task_id", "")
                status_str = params.get("status", "")
                if status_str:
                    try:
                        status = TaskStatus(status_str)
                        task = self.tasks.update_status(task_id, status, self.agent_id)
                    except ValueError as e:
                        self._messages_history.append({
                            "role": "user",
                            "content": f"[System] Cannot update task status: {str(e)}",
                        })
                        return

                    await self.bus.publish(
                        sender=self.agent_id,
                        sender_role=self.role,
                        msg_type=MessageType.TASK_ASSIGNED,
                        content=f"Task [{task_id}] updated to {status.value}",
                        data=task.to_dict(),
                    )
                    # Check if all tasks are now complete → auto-stop
                    if status == TaskStatus.DONE and self.tasks.all_done:
                        # Only orchestrator can actually trigger completion
                        if self.role == "Orchestrator":
                            await self._trigger_mission_complete()
                        else:
                            # Notify orchestrator that all tasks appear done
                            await self.bus.publish(
                                sender=self.agent_id,
                                sender_role=self.role,
                                msg_type=MessageType.CHAT,
                                content="🏁 All tasks appear to be done! Orchestrator, please verify and use `done` to complete the mission.",
                                mentions=["orchestrator"],
                            )
                    # Auto-trigger tester when a developer marks a coding task done
                    elif status == TaskStatus.DONE and self.role == "Developer":
                        task_obj = self.tasks.get_task(task_id)
                        task_title = task_obj.title if task_obj else ""
                        if task_title and not task_title.startswith("[Test]"):
                            await self.bus.publish(
                                sender=self.agent_id,
                                sender_role=self.role,
                                msg_type=MessageType.CHAT,
                                content=(
                                    f"✅ Task '{task_title}' implementation complete. "
                                    f"@reviewer review the code and @tester run tests to verify."
                                ),
                                mentions=["reviewer", "tester"],
                            )

            elif action_type == "handoff":
                task_id = params.get("task_id", "")
                files_touched = params.get("files_touched", [])
                commands_run = params.get("commands_run", [])
                risks = params.get("known_risks", [])
                next_role = params.get("next_role", "")

                await self.bus.publish(
                    sender=self.agent_id,
                    sender_role=self.role,
                    msg_type=MessageType.HANDOFF,
                    content=message or f"🤝 Handoff for task [{task_id}]",
                    data={
                        "task_id": task_id,
                        "files_touched": files_touched,
                        "commands_run": commands_run,
                        "known_risks": risks,
                        "next_role": next_role,
                    },
                    mentions=[next_role] if next_role else [],
                )

            elif action_type == "request_review":
                handoff_task_id = params.get("task_id", "")
                if handoff_task_id:
                    await self.bus.publish(
                        sender=self.agent_id,
                        sender_role=self.role,
                        msg_type=MessageType.HANDOFF,
                        content=f"🤝 Pre-review handoff for task [{handoff_task_id}]",
                        data={
                            "task_id": handoff_task_id,
                            "files_touched": params.get("files", []),
                            "commands_run": params.get("commands_run", []),
                            "known_risks": params.get("known_risks", []),
                            "next_role": "reviewer",
                        },
                        mentions=params.get("reviewers", []),
                    )

                await self.bus.publish(
                    sender=self.agent_id,
                    sender_role=self.role,
                    msg_type=MessageType.REVIEW_REQUEST,
                    content=message or "Please review my code",
                    data=params,
                    mentions=params.get("reviewers", []),
                )

            elif action_type == "escalate_task":
                # Developer admits defeat — ask orchestrator to spawn a senior dev
                task_id = params.get("task_id", "")
                reason = params.get("reason", "Task is too complex for me")
                try:
                    task = self.tasks._resolve_task(task_id)
                    # Mark task as blocked
                    self.tasks.update_status(task.id, TaskStatus.BLOCKED, self.agent_id)
                    await self.bus.publish(
                        sender=self.agent_id,
                        sender_role=self.role,
                        msg_type=MessageType.CHAT,
                        content=(
                            f"🆘 ESCALATION REQUEST: Task [{task.id}] '{task.title}' needs a senior developer.\n"
                            f"Reason: {reason}\n\n"
                            f"@orchestrator Please spawn a `senior_developer` using `spawn_agent` with role=`senior_developer` "
                            f"and reassign this task to them. Senior devs use a more powerful model."
                        ),
                        mentions=["orchestrator"],
                    )
                    self._messages_history.append({
                        "role": "user",
                        "content": f"[System] ✅ Escalation sent. Task [{task.id}] marked as BLOCKED. "
                                   f"Orchestrator will assign a senior developer.",
                    })
                except ValueError as e:
                    self._messages_history.append({
                        "role": "user",
                        "content": f"[System] Escalation failed: {str(e)}",
                    })

            elif action_type == "list_files":
                files = await self.workspace.list_files(params.get("path", ""))
                self._messages_history.append({
                    "role": "user",
                    "content": f"[Directory listing]:\n{json.dumps(files, indent=2)}",
                })

            elif action_type == "delete_file":
                path = params.get("path", "")
                await self._request_approval("delete_file", params, f"Agent wants to delete: `{path}`")
                return

            elif action_type == "use_tool":
                # Plugin system — execute a registered tool
                tool_name = params.get("tool", "")
                tool_path = params.get("path", ".")
                tool_pattern = params.get("pattern", "")
                tool_query = params.get("query", tool_pattern)
                tool_url = params.get("url", "")
                max_results = params.get("max_results", 5)
                max_chars = params.get("max_chars", 6000)
                try:
                    from server.main import state as app_state
                    tool_obj = app_state.plugin_registry.get_tool(tool_name)
                    if tool_obj and tool_obj.requires_approval:
                        await self._request_approval(
                            "use_tool", params,
                            f"🔧 Tool [{tool_name}] requires approval: {tool_obj.description}"
                        )
                        return

                    cmd = app_state.plugin_registry.build_command(
                        tool_name,
                        workspace=str(self.workspace.root),
                        path=tool_path,
                        pattern=tool_pattern,
                        query=tool_query,
                        url=tool_url,
                        max_results=max_results,
                        max_chars=max_chars,
                    )
                    if cmd:
                        result = await self.terminal.execute(
                            command=cmd,
                            cwd=str(self.workspace.root),
                        )
                        await self.bus.publish(
                            sender=self.agent_id,
                            sender_role=self.role,
                            msg_type=MessageType.TERMINAL_OUTPUT,
                            content=f"🔧 Tool [{tool_name}]: {cmd[:100]}",
                            data=result.to_dict(),
                        )
                        self._messages_history.append({
                            "role": "user",
                            "content": f"[Tool {tool_name} output]:\n{result.stdout[:3000]}\n{result.stderr[:500]}",
                        })
                    else:
                        available = [t['name'] for t in app_state.plugin_registry.list_tools()]
                        self._messages_history.append({
                            "role": "user",
                            "content": f"Unknown tool: {tool_name}. Available: {available}",
                        })
                except ImportError:
                    pass

            elif action_type == "spawn_agent":
                # Dynamically spawn a new agent
                role = params.get("role", "")
                reason = params.get("reason", "")
                try:
                    from server.main import state as app_state
                    result = await app_state.agent_spawner.spawn_agent(
                        role=role,
                        reason=reason,
                        state=app_state,
                    )
                    if result:
                        self._messages_history.append({
                            "role": "user",
                            "content": f"[System] Successfully spawned agent: {result['id']} (role={role}). "
                                       f"You can now assign tasks to '{result['id']}'.",
                        })
                    else:
                        self._messages_history.append({
                            "role": "user",
                            "content": f"[System] Could not spawn {role} agent — at max capacity.",
                        })
                except Exception as e:
                    logger.error(f"[{self.agent_id}] Spawn failed: {e}")
                    self._messages_history.append({
                        "role": "user",
                        "content": f"[System] Failed to spawn agent: {str(e)[:200]}",
                    })

            elif action_type == "create_novel_agent":
                # Create an entirely new specialist agent with custom role/capabilities
                role_name = params.get("role_name", "Specialist")
                specialization = params.get("specialization", "")
                capabilities = params.get("capabilities", ["code", "communicate"])
                custom_guidelines = params.get("custom_guidelines", "")
                reason = params.get("reason", "")
                try:
                    from server.main import state as app_state
                    # Pass the current mission goal as context
                    mission_ctx = ""
                    if hasattr(self, '_messages_history') and self._messages_history:
                        for msg in self._messages_history:
                            if "[MISSION GOAL]" in msg.get("content", ""):
                                mission_ctx = msg["content"]
                                break

                    result = await app_state.agent_spawner.spawn_dynamic_agent(
                        role_name=role_name,
                        specialization=specialization,
                        capabilities=capabilities,
                        custom_guidelines=custom_guidelines,
                        mission_context=mission_ctx,
                        reason=reason,
                        state=app_state,
                    )
                    if result:
                        self._messages_history.append({
                            "role": "user",
                            "content": f"[System] Successfully created novel agent: {result['id']} "
                                       f"(role={role_name}, spec={specialization}). "
                                       f"You can now assign tasks to '{result['id']}'.",
                        })
                    else:
                        self._messages_history.append({
                            "role": "user",
                            "content": f"[System] Could not create novel agent — at max capacity (4 dynamic agents).",
                        })
                except Exception as e:
                    logger.error(f"[{self.agent_id}] Novel agent spawn failed: {e}")
                    self._messages_history.append({
                        "role": "user",
                        "content": f"[System] Failed to create novel agent: {str(e)[:200]}",
                    })

            elif action_type == "kill_agent":
                # Remove a dynamically spawned agent
                target_id = params.get("agent_id", "")
                try:
                    from server.main import state as app_state
                    success = await app_state.agent_spawner.kill_agent(
                        agent_id=target_id,
                        state=app_state,
                    )
                    if success:
                        self._messages_history.append({
                            "role": "user",
                            "content": f"[System] Agent '{target_id}' has been removed from the team.",
                        })
                    else:
                        self._messages_history.append({
                            "role": "user",
                            "content": f"[System] Cannot remove agent '{target_id}' — not found or is a core agent.",
                        })
                except Exception as e:
                    logger.error(f"[{self.agent_id}] Kill agent failed: {e}")

            elif action_type == "use_terminal":
                # Interact with the interactive PTY terminal
                command = params.get("command", "")
                session_id = params.get("session_id", f"agent-{self.agent_id}")
                wait_seconds = min(params.get("wait_seconds", 3), 10)

                # Auto-approve safe commands, require approval for potentially destructive ones
                if not self._is_safe_command(command):
                    approved = await self._request_approval(
                        "use_terminal", params,
                        f"🖥️ [{self.agent_id}] wants to run in terminal '{session_id}': `{command}`",
                    )
                    if not approved:
                        self._messages_history.append({
                            "role": "user",
                            "content": f"[System] Terminal command REJECTED by user: `{command}`. Try a different approach.",
                        })
                        return

                try:
                    from server.main import state as app_state
                    terminal = app_state.interactive_terminal

                    # Create session if it doesn't exist
                    if session_id not in terminal._sessions:
                        await terminal.create_session(
                            session_id=session_id,
                            cwd=str(self.workspace.root),
                        )

                    # Capture output via a collector
                    output_chunks = []

                    def _collect(data):
                        output_chunks.append(data)

                    old_cb = terminal._output_callbacks.get(session_id)
                    terminal._output_callbacks[session_id] = _collect

                    # Write the command + Enter
                    await terminal.write_input(session_id, command + "\n")

                    # Wait for output
                    await asyncio.sleep(wait_seconds)

                    # Restore old callback
                    if old_cb:
                        terminal._output_callbacks[session_id] = old_cb
                    else:
                        terminal._output_callbacks.pop(session_id, None)

                    output = "".join(output_chunks)[:5000]

                    # Broadcast terminal activity
                    await self.bus.publish(
                        sender=self.agent_id,
                        sender_role=self.role,
                        msg_type=MessageType.TERMINAL_OUTPUT,
                        content=f"[Terminal:{session_id}] $ {command}",
                        data={"stdout": output, "session_id": session_id},
                    )

                    # Feed output back to agent
                    self._messages_history.append({
                        "role": "user",
                        "content": f"[Terminal session '{session_id}' output for `{command}`]:\n{output[:3000]}",
                    })

                except Exception as e:
                    logger.error(f"[{self.agent_id}] use_terminal failed: {e}")
                    self._messages_history.append({
                        "role": "user",
                        "content": f"[Terminal error]: {str(e)[:300]}",
                    })

            elif action_type == "done":
                # Only orchestrator can signal mission complete
                if self.role != "Orchestrator":
                    self._messages_history.append({
                        "role": "user",
                        "content": "[System] Only the Orchestrator can complete the mission. Notify the orchestrator that you believe the mission is done.",
                    })
                else:
                    # Guard: check if all tasks are actually done
                    summary = self.tasks.get_summary()
                    open_todo = summary.get("todo", 0)
                    open_progress = summary.get("in_progress", 0)
                    if open_todo > 0 or open_progress > 0:
                        # Build list of incomplete tasks for feedback
                        incomplete = [
                            t for t in self.tasks.list_tasks()
                            if t.get("status") in ("todo", "in_progress")
                        ]
                        task_list = "\n".join(
                            f"  - [{t.get('status')}] {t.get('title', 'Untitled')}"
                            for t in incomplete
                        )
                        self._messages_history.append({
                            "role": "user",
                            "content": (
                                f"[System] ❌ Cannot complete mission — {open_todo} todo and "
                                f"{open_progress} in-progress task(s) remain:\n{task_list}\n\n"
                                f"Wait for all tasks to finish, or cancel/complete them first."
                            ),
                        })
                        logger.warning(
                            f"[{self.agent_id}] Mission completion blocked: "
                            f"{open_todo} todo, {open_progress} in_progress"
                        )
                    else:
                        await self._trigger_mission_complete()

            elif action_type == "message":
                # Just a chat message, no file action
                pass

            # ─── Collaborative problem-solving actions ─────────────
            elif action_type == "ask_help":
                target = params.get("target", "orchestrator")
                question = params.get("question", message or "I need help")
                context_info = params.get("context", "")
                help_content = f"🤔 **Help needed from @{target}**\n\n"
                help_content += f"**Question:** {question}\n"
                if context_info:
                    help_content += f"**What I've tried:** {context_info}\n"
                task_id = params.get("task_id", "")
                if task_id:
                    help_content += f"**Task:** [{task_id}]\n"
                await self.bus.publish(
                    sender=self.agent_id,
                    sender_role=self.role,
                    msg_type=MessageType.ASK_HELP,
                    content=help_content,
                    mentions=[target],
                    data={"question": question, "context": context_info, "task_id": task_id},
                )

            elif action_type == "share_insight":
                insight = params.get("insight", message or "")
                files = params.get("files", [])
                insight_content = f"💡 **Insight from {self.agent_id}:**\n{insight}"
                if files:
                    insight_content += f"\n**Related files:** {', '.join(files)}"
                await self.bus.publish(
                    sender=self.agent_id,
                    sender_role=self.role,
                    msg_type=MessageType.SHARE_INSIGHT,
                    content=insight_content,
                    data={"insight": insight, "files": files},
                )

            elif action_type == "propose_approach":
                approach = params.get("approach", message or "")
                alternatives = params.get("alternatives", [])
                task_id = params.get("task_id", "")
                approach_content = f"📐 **Approach proposal from {self.agent_id}:**\n\n"
                approach_content += f"**Proposed:** {approach}\n"
                if alternatives:
                    approach_content += f"**Alternatives considered:**\n"
                    for i, alt in enumerate(alternatives, 1):
                        approach_content += f"  {i}. {alt}\n"
                if task_id:
                    approach_content += f"\n**For task:** [{task_id}]\n"
                approach_content += f"\n@orchestrator @reviewer — feedback welcome before I start coding."
                await self.bus.publish(
                    sender=self.agent_id,
                    sender_role=self.role,
                    msg_type=MessageType.PROPOSE_APPROACH,
                    content=approach_content,
                    mentions=["orchestrator", "reviewer"],
                    data={"approach": approach, "alternatives": alternatives, "task_id": task_id},
                )

            else:
                logger.warning(f"[{self.agent_id}] Unknown action: {action_type}")

            # Broadcast chat message if present
            if message:
                await self.bus.publish(
                    sender=self.agent_id,
                    sender_role=self.role,
                    msg_type=MessageType.CHAT,
                    content=message,
                )

        except Exception as e:
            logger.error(f"[{self.agent_id}] Action failed: {e}", exc_info=True)
            await self.bus.publish(
                sender=self.agent_id,
                sender_role=self.role,
                msg_type=MessageType.SYSTEM,
                content=f"Error executing {action_type}: {str(e)}",
            )

    async def _request_approval(self, action_type: str, params: dict, description: str) -> bool:
        """Request user approval for a command. Blocks until approved/rejected or timeout."""
        import uuid
        approval_id = str(uuid.uuid4())[:8]
        future = asyncio.get_event_loop().create_future()
        self._pending_approvals[approval_id] = future

        await self.bus.publish(
            sender=self.agent_id,
            sender_role=self.role,
            msg_type=MessageType.APPROVAL_REQUEST,
            content=description,
            data={
                "approval_id": approval_id,
                "action": action_type,
                "params": params,
            },
        )

        self.status = AgentStatus.WAITING
        await self._broadcast_status()

        # Block until user responds (5 minute timeout)
        try:
            approved = await asyncio.wait_for(future, timeout=300)
            return approved
        except asyncio.TimeoutError:
            logger.warning(f"[{self.agent_id}] Approval {approval_id} timed out after 5 minutes")
            self._pending_approvals.pop(approval_id, None)
            self._messages_history.append({
                "role": "user",
                "content": f"[System] Command approval timed out (5 min). Command was NOT executed: {params.get('command', '')}",
            })
            return False

    async def resolve_approval(self, approval_id: str, approved: bool):
        """Resolve a pending approval request."""
        if approval_id in self._pending_approvals:
            future = self._pending_approvals.pop(approval_id)
            if not future.done():
                future.set_result(approved)

    async def inject_message(self, content: str):
        """Inject a message from the user into the agent's context."""
        self._messages_history.append({
            "role": "user",
            "content": f"[USER DIRECTIVE]: {content}",
        })

    async def _trigger_mission_complete(self):
        """Handle mission completion — run review, then stop all agents, save history, and broadcast finish."""
        summary = self.tasks.get_summary()
        logger.info(f"[{self.agent_id}] 🏁 Mission complete! Tasks: {summary}")

        # --- Post-completion review ---
        try:
            from server.main import state
            from server.core.project_reviewer import run_review_loop

            async def on_new_tasks(issues):
                """Route review issues through the orchestrator for proper task planning."""
                # Format issues into a clear message for the orchestrator
                issue_lines = []
                for i, issue in enumerate(issues, 1):
                    sev = issue.get("severity", "unknown")
                    title = issue.get("title", "Unnamed issue")
                    desc = issue.get("description", "")
                    file_path = issue.get("file", "")
                    issue_lines.append(
                        f"  {i}. [{sev.upper()}] {title}"
                        + (f" — {file_path}" if file_path else "")
                        + (f"\n     {desc}" if desc else "")
                    )
                issues_text = "\n".join(issue_lines)

                # Re-start stopped agents so they can work on the fixes
                for agent in state.agents.values():
                    if not agent._running:
                        await agent.start()
                        logger.info(f"♻️ Restarted {agent.agent_id} for review fix cycle")

                # Send the review feedback directly to the orchestrator
                orchestrator = state.agents.get("orchestrator")
                if orchestrator:
                    orchestrator._goal_processed = False  # Allow it to act proactively
                    await orchestrator.inject_message(
                        f"[REVIEW FEEDBACK — NEEDS CHANGES]\n"
                        f"The post-completion review found {len(issues)} issue(s) that must be fixed:\n"
                        f"{issues_text}\n\n"
                        f"Create new tasks to address ALL issues above. Assign clear file ownership. "
                        f"Once all fix tasks are done, the system will re-review automatically."
                    )
                else:
                    # Fallback: create tasks directly if orchestrator is missing
                    for issue in issues:
                        self.tasks.add_task(
                            title=f"[Review] {issue.get('title', 'Fix issue')}",
                            description=issue.get("description", ""),
                            assignee=issue.get("assignee", "developer"),
                        )

                # Broadcast that review cycle is starting
                await self.bus.publish(
                    sender=self.agent_id,
                    sender_role=self.role,
                    msg_type=MessageType.TASK_ASSIGNED,
                    content=f"🔄 Review found {len(issues)} issue(s) — orchestrator is creating fix tasks",
                    data={"issues": issues},
                )

            review = await run_review_loop(state, self.tasks, self.bus, on_new_tasks=on_new_tasks)
            logger.info(f"[{self.agent_id}] 📝 Review finished: {review.get('status')} (cycle {review.get('cycle', '?')})")

        except Exception as e:
            logger.error(f"Post-completion review failed: {e}", exc_info=True)

        # --- Broadcast mission complete ---
        await self.bus.publish(
            sender=self.agent_id,
            sender_role=self.role,
            msg_type=MessageType.MISSION_COMPLETE,
            content="✅ Mission complete — all tasks finished!",
            data={"tasks": self.tasks.list_tasks(), "summary": summary},
        )

        # Auto-stop all agents and save state
        try:
            from server.main import state
            import time as _time

            # Save mission history
            duration = _time.time() - state.mission_start_time if state.mission_start_time else 0
            cost = state.gemini.get_global_usage().get("estimated_cost_usd", 0)
            state.mission_store.save_mission(
                mission_id=state.mission_id or "unknown",
                goal=state.mission_goal or "",
                workspace_path=str(self.workspace.root or ""),
                tasks=self.tasks.list_tasks(),
                cost_usd=cost,
                duration_seconds=duration,
                agents=list(state.agents.keys()),
                status="completed",
            )

            # Save memory lessons from completed tasks
            done_tasks = [t for t in self.tasks.list_tasks() if t.get("status") == "done"]
            for task in done_tasks[:3]:  # Save top 3 lessons
                state.agent_memory.save_lesson(
                    agent_role=task.get("assignee", self.role),
                    lesson=f"Successfully completed: {task.get('title', '')}",
                    context=task.get("description", ""),
                    mission_id=state.mission_id or "",
                    lesson_type="pattern",
                )

            for agent in state.agents.values():
                if agent.agent_id != self.agent_id:
                    await agent.stop()
            await state.git_manager.auto_commit("Mission complete — all tasks done")
            state.mission_active = False
            # Stop self last
            self._running = False
        except Exception as e:
            logger.error(f"Auto-stop failed: {e}")

    async def _broadcast_status(self):
        """Broadcast agent status to UI."""
        await self.bus.publish(
            sender=self.agent_id,
            sender_role=self.role,
            msg_type=MessageType.AGENT_STATUS,
            content=self.status.value,
            data=self.get_status_dict(),
        )
