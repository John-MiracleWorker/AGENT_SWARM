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
from server.core.gemini_client import GeminiClient
from server.core.workspace import WorkspaceManager
from server.core.task_manager import TaskManager, TaskStatus
from server.core.terminal import TerminalExecutor
from server.core.context_manager import ContextManager

logger = logging.getLogger(__name__)


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

                # Small delay to prevent tight loops
                await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.agent_id}] Event loop error: {e}", exc_info=True)
                await asyncio.sleep(5)

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

            elif action_type == "done":
                # Agent signals completion
                pass

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

    async def _broadcast_status(self):
        """Broadcast agent status to UI."""
        await self.bus.publish(
            sender=self.agent_id,
            sender_role=self.role,
            msg_type=MessageType.AGENT_STATUS,
            content=self.status.value,
            data=self.get_status_dict(),
        )
