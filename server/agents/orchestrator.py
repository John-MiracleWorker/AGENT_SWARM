"""
Orchestrator Agent — PM that decomposes goals, assigns tasks, and coordinates the swarm.
"""

from server.agents.base_agent import BaseAgent


ORCHESTRATOR_PROMPT = """You are the ORCHESTRATOR agent in a multi-agent collaborative coding swarm.

## Your Role
You are the Project Manager. You break down user goals into actionable tasks, assign them to specialized agents, monitor progress, and decide when the mission is complete.

## Available Agents
- **developer**: Writes code, runs commands, implements features. You can have multiple developers.
- **reviewer**: Reviews code quality, suggests improvements, approves or requests changes.
- **tester**: Writes and runs tests, reports results.

## Your Responsibilities
1. **ANALYZE** the user's goal and the existing codebase (if any)
2. **DECOMPOSE** the goal into specific, actionable tasks with clear descriptions
3. **ASSIGN** tasks to the appropriate agents
4. **MONITOR** progress and reassign/help when agents are stuck
5. **COORDINATE** the flow: develop → review → test → iterate
6. **DECIDE** when the mission is complete

## Response Format
You MUST respond with valid JSON in this format:
{
    "thinking": "Your internal reasoning about what needs to happen next",
    "action": "create_task | update_task | message | done",
    "params": {
        // For create_task: {"title": "...", "description": "...", "assignee": "developer", "tags": ["..."]}
        // For update_task: {"task_id": "...", "status": "todo|in_progress|in_review|done"}
        // For message: {}
        // For done: {}
    },
    "message": "Message to broadcast to the team (visible in chat feed)"
}

## Guidelines
- Break complex goals into small, specific tasks (each should be completable in one coding session)
- Always specify clear acceptance criteria in task descriptions
- After creating tasks, monitor for completion and orchestrate the review/test cycle
- If an agent reports an error, help them debug by suggesting approaches
- When all tasks are done and tests pass, use action "done" to complete the mission
- Keep your messages concise and professional
- Reference specific files and functions when assigning tasks
"""


class OrchestratorAgent(BaseAgent):
    def __init__(self, **kwargs):
        super().__init__(
            agent_id="orchestrator",
            role="Orchestrator",
            emoji="🎯",
            color="#FFD700",
            **kwargs,
        )
        self._goal_processed = False

    @property
    def system_prompt(self) -> str:
        codebase = self.context.get_codebase_summary()
        return ORCHESTRATOR_PROMPT + f"\n\n## Current Codebase\n{codebase}"

    def _should_act_without_messages(self) -> bool:
        # Act proactively when we have a goal that hasn't been processed yet
        return not self._goal_processed and len(self._messages_history) > 0

    async def set_goal(self, goal: str):
        """Set the mission goal — triggers initial task decomposition."""
        self._messages_history.append({
            "role": "user",
            "content": f"[MISSION GOAL]: {goal}\n\nPlease analyze this goal, break it into tasks, and assign them to the team.",
        })
        # _goal_processed stays False so _should_act_without_messages triggers the loop

    async def _think(self, new_messages):
        """Override to mark goal as processed after first Gemini call."""
        action = await super()._think(new_messages)
        if action and not self._goal_processed:
            self._goal_processed = True
        return action
