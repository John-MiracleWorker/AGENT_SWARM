"""
Message Bus — In-memory pub/sub for inter-agent communication.
All messages are logged and forwarded to WebSocket clients.
"""

import asyncio
import time
import uuid
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Any

logger = logging.getLogger(__name__)


class MessageType(str, Enum):
    CHAT = "chat"
    CODE_UPDATE = "code_update"
    TASK_ASSIGNED = "task_assigned"
    REVIEW_REQUEST = "review_request"
    REVIEW_RESULT = "review_result"
    DEBATE = "debate"
    TEST_RESULT = "test_result"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_RESPONSE = "approval_response"
    TERMINAL_OUTPUT = "terminal_output"
    FILE_UPDATE = "file_update"
    SYSTEM = "system"
    AGENT_STATUS = "agent_status"
    THOUGHT = "thought"


@dataclass
class Message:
    id: str
    timestamp: float
    sender: str
    sender_role: str
    msg_type: MessageType
    content: str
    data: dict = field(default_factory=dict)
    channel: str = "general"
    mentions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "sender": self.sender,
            "sender_role": self.sender_role,
            "type": self.msg_type.value,
            "content": self.content,
            "data": self.data,
            "channel": self.channel,
            "mentions": self.mentions,
        }


class MessageBus:
    """
    In-memory pub/sub message bus. Agents publish messages,
    subscribers (other agents + WebSocket handler) receive them.
    """

    def __init__(self, max_history: int = 500):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._agent_queues: dict[str, asyncio.Queue] = {}
        self._history: list[Message] = []
        self._max_history = max_history
        self._ws_callbacks: list[Callable] = []
        self._lock = asyncio.Lock()

    def subscribe_agent(self, agent_id: str) -> asyncio.Queue:
        """Create a message queue for an agent."""
        queue = asyncio.Queue()
        self._agent_queues[agent_id] = queue
        return queue

    def unsubscribe_agent(self, agent_id: str):
        """Remove an agent's queue."""
        self._agent_queues.pop(agent_id, None)

    def register_ws_callback(self, callback: Callable):
        """Register a WebSocket callback for broadcasting to frontend."""
        self._ws_callbacks.append(callback)

    def unregister_ws_callback(self, callback: Callable):
        """Remove a WebSocket callback."""
        if callback in self._ws_callbacks:
            self._ws_callbacks.remove(callback)

    async def publish(
        self,
        sender: str,
        sender_role: str,
        msg_type: MessageType,
        content: str,
        data: Optional[dict] = None,
        channel: str = "general",
        mentions: Optional[list[str]] = None,
    ) -> Message:
        """Publish a message to all subscribers."""
        msg = Message(
            id=str(uuid.uuid4()),
            timestamp=time.time(),
            sender=sender,
            sender_role=sender_role,
            msg_type=msg_type,
            content=content,
            data=data or {},
            channel=channel,
            mentions=mentions or [],
        )

        # Store in history
        async with self._lock:
            self._history.append(msg)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

        # Deliver to agent queues (skip sender)
        for agent_id, queue in self._agent_queues.items():
            if agent_id != sender:
                # If message has mentions, only deliver to mentioned agents
                # (unless it's a broadcast type)
                if mentions and agent_id not in mentions and msg_type not in (
                    MessageType.SYSTEM, MessageType.AGENT_STATUS, MessageType.TASK_ASSIGNED
                ):
                    continue
                try:
                    queue.put_nowait(msg)
                except asyncio.QueueFull:
                    logger.warning(f"Queue full for agent {agent_id}")

        # Forward to WebSocket clients
        msg_dict = msg.to_dict()
        for cb in self._ws_callbacks:
            try:
                await cb(msg_dict)
            except Exception as e:
                logger.error(f"WebSocket callback error: {e}")

        logger.debug(f"[{sender}] {msg_type.value}: {content[:100]}")
        return msg

    def get_history(
        self,
        channel: Optional[str] = None,
        msg_type: Optional[MessageType] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Get recent message history with optional filters."""
        msgs = self._history
        if channel:
            msgs = [m for m in msgs if m.channel == channel]
        if msg_type:
            msgs = [m for m in msgs if m.msg_type == msg_type]
        return [m.to_dict() for m in msgs[-limit:]]

    def get_agent_messages(self, agent_id: str, limit: int = 20) -> list[dict]:
        """Get messages relevant to a specific agent."""
        msgs = [
            m for m in self._history
            if m.sender == agent_id
            or agent_id in m.mentions
            or not m.mentions  # broadcast messages
        ]
        return [m.to_dict() for m in msgs[-limit:]]
