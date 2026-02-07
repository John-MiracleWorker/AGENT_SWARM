"""
Base Agent — Abstract base class for all agents in the swarm.
Implements the observe → think → act event loop.
"""

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional
from enum import Enum

from server.core.message_bus import MessageBus, MessageType, Message
from server.core.gemini_client import GeminiClient, BudgetExhaustedError
from server.core.workspace import WorkspaceManager
from server.core.task_manager import TaskManager, TaskStatus
from server.core.terminal import TerminalExecutor
from server.core.context_manager import ContextManager

logger = logging.getLogger(__name__)

# Error recovery constants
MAX_CONSECUTIVE_ERRORS = 5
RETRY_BACKOFF_BASE = 2
MAX_RETRY_ATTEMPTS = 3


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

    async def _event_loop(self):
        """Main observe → think → act loop."""
        while self._running:
            try:
                if self._paused:
                    self.status = AgentStatus.PAUSED
                    await asyncio.sleep(1)
                    continue

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

                # Reset error counter on success
                self._consecutive_errors = 0
                self._error_backoff = 1.0

                # Small delay to prevent tight loops
                await asyncio.sleep(1)

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

        # Trim context if needed
        trimmed = self.context.trim_messages(self._messages_history)

        # Broadcast thinking bubble
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
            return None

    async def _act(self, action: dict):
        """Execute a structured action from Gemini."""
        action_type = action.get("action", "message")
        params = action.get("params", {})
        message = action.get("message", "")

        try:
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
                diff = await self.workspace.write_file(path, content)
                await self.bus.publish(
                    sender=self.agent_id,
                    sender_role=self.role,
                    msg_type=MessageType.FILE_UPDATE,
                    content=f"Wrote file: {path}",
                    data={"diff": diff, "path": path},
                )

            elif action_type == "read_file":
                path = params.get("path", "")
                content = await self.workspace.read_file(path)
                # Add file content to agent's context
                self._messages_history.append({
                    "role": "user",
                    "content": f"[File content of {path}]:\n```\n{content}\n```",
                })

            elif action_type == "run_command":
                command = params.get("command", "")
                # Check if dangerous
                if self.terminal.is_dangerous(command):
                    await self._request_approval("run_command", params, f"Agent wants to run: `{command}`")
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
                task = self.tasks.create_task(
                    title=params.get("title", ""),
                    description=params.get("description", ""),
                    created_by=self.agent_id,
                    assignee=params.get("assignee"),
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

            elif action_type == "update_task":
                task_id = params.get("task_id", "")
                status_str = params.get("status", "")
                if status_str:
                    status = TaskStatus(status_str)
                    task = self.tasks.update_status(task_id, status, self.agent_id)
                    await self.bus.publish(
                        sender=self.agent_id,
                        sender_role=self.role,
                        msg_type=MessageType.TASK_ASSIGNED,
                        content=f"Task [{task_id}] updated to {status.value}",
                        data=task.to_dict(),
                    )
                    # Check if all tasks are now complete → auto-stop
                    if status == TaskStatus.DONE and self.tasks.all_done:
                        await self._trigger_mission_complete()

            elif action_type == "request_review":
                await self.bus.publish(
                    sender=self.agent_id,
                    sender_role=self.role,
                    msg_type=MessageType.REVIEW_REQUEST,
                    content=message or "Please review my code",
                    data=params,
                    mentions=params.get("reviewers", []),
                )

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

            elif action_type == "done":
                # Orchestrator signals mission complete
                await self._trigger_mission_complete()

            elif action_type == "message":
                # Just a chat message, no file action
                pass

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

    async def _request_approval(self, action_type: str, params: dict, description: str):
        """Request user approval for a dangerous action."""
        import uuid
        approval_id = str(uuid.uuid4())[:8]
        self._pending_approvals[approval_id] = asyncio.get_event_loop().create_future()

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

    async def resolve_approval(self, approval_id: str, approved: bool):
        """Resolve a pending approval request."""
        if approval_id in self._pending_approvals:
            self._pending_approvals[approval_id].set_result(approved)

    async def inject_message(self, content: str):
        """Inject a message from the user into the agent's context."""
        self._messages_history.append({
            "role": "user",
            "content": f"[USER DIRECTIVE]: {content}",
        })

    async def _trigger_mission_complete(self):
        """Handle mission completion — stop all agents, save history, and broadcast finish."""
        summary = self.tasks.get_summary()
        logger.info(f"[{self.agent_id}] 🏁 Mission complete! Tasks: {summary}")

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
