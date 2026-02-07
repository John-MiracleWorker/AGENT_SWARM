"""
Agent Spawner — Dynamically create/destroy agent instances at runtime.

The orchestrator can spawn additional agents (e.g., developer-2) when it
decides parallel work is needed, and kill them when they're no longer useful.
"""

import asyncio
import logging
from typing import Optional

from server.agents.developer import DeveloperAgent
from server.agents.reviewer import ReviewerAgent
from server.agents.tester import TesterAgent

logger = logging.getLogger(__name__)

# Per-role limits to prevent runaway spawning
ROLE_LIMITS = {
    "developer": 3,
    "reviewer": 2,
    "tester": 2,
}

# Map role names → agent classes
ROLE_CLASSES = {
    "developer": DeveloperAgent,
    "reviewer": ReviewerAgent,
    "tester": TesterAgent,
}

# Colors for spawned agent instances (cycle through these)
SPAWN_COLORS = {
    "developer": ["#00E5FF", "#00BCD4", "#0097A7"],
    "reviewer": ["#AA00FF", "#9C27B0", "#7B1FA2"],
    "tester": ["#00E676", "#4CAF50", "#388E3C"],
}

SPAWN_EMOJIS = {
    "developer": ["💻", "⌨️", "🛠️"],
    "reviewer": ["🔍", "🧐", "📝"],
    "tester": ["🧪", "🔬", "✅"],
}


class AgentSpawner:
    """Manages dynamic creation and destruction of agent instances."""

    def __init__(self):
        self._counters: dict[str, int] = {}  # role → next ID number

    def _count_role(self, agents: dict, role: str) -> int:
        """Count how many agents of a given role currently exist."""
        role_lower = role.lower()
        return sum(
            1 for a in agents.values()
            if a.role.lower() == role_lower
        )

    def _next_id(self, role: str) -> str:
        """Generate the next agent ID for a role (developer-2, developer-3, etc.)."""
        count = self._counters.get(role, 1)
        count += 1
        self._counters[role] = count
        return f"{role}-{count}"

    async def spawn_agent(
        self,
        role: str,
        reason: str = "",
        state=None,
    ) -> Optional[dict]:
        """
        Spawn a new agent of the given role.

        Returns agent status dict on success, None if at capacity.
        """
        from server.core.message_bus import MessageType

        role = role.lower()

        if role not in ROLE_CLASSES:
            logger.warning(f"Unknown role: {role}")
            return None

        # Check capacity
        current_count = self._count_role(state.agents, role)
        limit = ROLE_LIMITS.get(role, 2)
        if current_count >= limit:
            logger.warning(f"Cannot spawn {role}: at capacity ({current_count}/{limit})")
            await state.message_bus.publish(
                sender="system",
                sender_role="System",
                msg_type=MessageType.SYSTEM,
                content=f"⚠️ Cannot spawn another {role} — already at max capacity ({current_count}/{limit})",
            )
            return None

        # Generate unique ID
        agent_id = self._next_id(role)

        # Pick color and emoji for this instance
        idx = current_count % len(SPAWN_COLORS.get(role, ["#888"]))
        color = SPAWN_COLORS.get(role, ["#888"])[idx]
        emoji = SPAWN_EMOJIS.get(role, ["🤖"])[idx]

        # Build agent kwargs (same as mission launch)
        agent_kwargs = dict(
            gemini=state.gemini,
            message_bus=state.message_bus,
            workspace=state.workspace,
            task_manager=state.task_manager,
            terminal=state.terminal,
            context_manager=state.context_manager,
        )

        # Instantiate agent
        AgentClass = ROLE_CLASSES[role]
        agent = AgentClass(agent_id=agent_id, **agent_kwargs)
        agent.color = color
        agent.emoji = emoji

        # Register and start
        state.agents[agent_id] = agent
        await agent.start()

        logger.info(f"🆕 Spawned agent: {agent_id} (role={role}, reason={reason})")

        # Broadcast spawn event
        await state.message_bus.publish(
            sender="system",
            sender_role="System",
            msg_type=MessageType.AGENT_STATUS,
            content=f"🆕 Spawned new agent: {agent_id}",
            data={
                "event": "agent_spawned",
                "id": agent_id,
                "role": role.capitalize(),
                "color": color,
                "emoji": emoji,
                "status": "idle",
                "reason": reason,
            },
        )

        return agent.get_status_dict()

    async def kill_agent(
        self,
        agent_id: str,
        state=None,
    ) -> bool:
        """
        Stop and remove a dynamically spawned agent.

        Cannot kill core agents (orchestrator, developer, reviewer, tester).
        """
        from server.core.message_bus import MessageType

        # Protect core agents from being killed
        core_agents = {"orchestrator", "developer", "reviewer", "tester"}
        if agent_id in core_agents:
            logger.warning(f"Cannot kill core agent: {agent_id}")
            return False

        agent = state.agents.get(agent_id)
        if not agent:
            logger.warning(f"Agent not found: {agent_id}")
            return False

        # Stop and remove
        await agent.stop()
        del state.agents[agent_id]

        logger.info(f"🗑️ Killed agent: {agent_id}")

        # Broadcast kill event
        await state.message_bus.publish(
            sender="system",
            sender_role="System",
            msg_type=MessageType.AGENT_STATUS,
            content=f"🗑️ Agent removed: {agent_id}",
            data={
                "event": "agent_killed",
                "id": agent_id,
            },
        )

        return True

    def get_spawn_info(self, agents: dict) -> dict:
        """Get info about current spawn state and capacity."""
        info = {}
        for role, limit in ROLE_LIMITS.items():
            count = self._count_role(agents, role)
            info[role] = {
                "current": count,
                "limit": limit,
                "can_spawn": count < limit,
            }
        return info
