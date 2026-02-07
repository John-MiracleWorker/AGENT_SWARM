"""
Dynamic Agent — A fully configurable agent that the Orchestrator can create at runtime.

Unlike the fixed Developer/Reviewer/Tester agents, a DynamicAgent's role, capabilities,
and system prompt are defined by the Orchestrator based on what the mission needs.
This enables the swarm to create specialized agents like:
  - "database-architect" — focused on schema design
  - "ui-designer" — focused on CSS, layout, and visual polish
  - "security-auditor" — focused on finding vulnerabilities
  - "devops-engineer" — focused on CI/CD, Docker, deployment
  - "api-designer" — focused on API structure and documentation
"""

from server.agents.base_agent import BaseAgent


# Capability sets the orchestrator can mix and match
CAPABILITY_SETS = {
    "code": {
        "actions": ["edit_file", "write_file", "read_file", "run_command", "use_terminal", "list_files"],
        "description": "Can read, edit, write, and execute code",
    },
    "read_only": {
        "actions": ["read_file", "list_files"],
        "description": "Can only read files (no writes or commands)",
    },
    "review": {
        "actions": ["read_file", "list_files"],
        "description": "Can read files for review purposes",
    },
    "test": {
        "actions": ["edit_file", "write_file", "read_file", "run_command", "use_terminal", "list_files"],
        "description": "Can write test files and execute tests",
    },
    "communicate": {
        "actions": ["message", "suggest_task", "update_task", "request_review"],
        "description": "Can communicate with the team and manage tasks",
    },
    "research": {
        "actions": ["read_file", "list_files", "use_tool", "message", "suggest_task", "update_task"],
        "description": "Can research web/documentation sources and report findings",
    },
}

# Default capabilities for dynamic agents
DEFAULT_CAPABILITIES = ["code", "communicate"]


DYNAMIC_AGENT_BASE_PROMPT = """You are a specialized agent in a multi-agent collaborative coding swarm.

## Your Identity
- **Role**: {role_name}
- **Specialization**: {specialization}

## Your Mission Context
{mission_context}

## Your Capabilities
{capabilities_text}

## IMPORTANT: Task Flow
- The Orchestrator is the brain — it creates ALL tasks
- You CANNOT create tasks directly — use `suggest_task` to propose work to the Orchestrator
- Only work on tasks assigned to you
- Update task status as you progress: in_progress → in_review → done

## Response Format
You MUST respond with valid JSON:
{{
    "thinking": "Your reasoning about the current situation",
    "action": "{available_actions_str}",
    "params": {{
        // For edit_file: {{"path": "relative/path", "search": "exact text to find", "replace": "replacement text"}}
        // For write_file: {{"path": "relative/path", "content": "file content"}} (NEW files only!)
        // For read_file: {{"path": "relative/path"}}
        // For run_command: {{"command": "shell command"}} (one-shot)
        // For use_terminal: {{"command": "cmd", "session_id": "session-name", "wait_seconds": 5}} (persistent)
        // For list_files: {{"path": "optional/subdir"}}
        // For suggest_task: {{"title": "Task title", "reason": "Why needed"}}
        // For update_task: {{"task_id": "...", "status": "in_progress|in_review|done"}}
        // For request_review: {{"files": ["path1", "path2"], "reviewers": ["reviewer"]}}
        // For message: {{}}
    }},
    "message": "Message to the team"
}}

## CRITICAL: File Editing Rules
- **ALWAYS use `edit_file` to modify existing files** — it only changes the targeted section
- **NEVER use `write_file` to modify an existing file** — it overwrites the ENTIRE file and destroys code
- Before using `edit_file`, ALWAYS `read_file` first to get the exact current content
- Only use `write_file` to create brand new files that don't exist yet

## Your Specific Guidelines
{custom_guidelines}
"""


# Color palette for dynamic agents (distinct from core agents)
DYNAMIC_COLORS = [
    "#FF6B6B",  # coral red
    "#48BB78",  # green
    "#ED8936",  # orange
    "#9F7AEA",  # purple
    "#38B2AC",  # teal
    "#F56565",  # red
    "#4FD1C5",  # cyan
    "#FC8181",  # light red
    "#68D391",  # light green
    "#F6AD55",  # light orange
]

DYNAMIC_EMOJIS = [
    "🔧", "🏗️", "🎨", "🔐", "📐",
    "🧩", "⚡", "🌐", "📊", "🔬",
]


class DynamicAgent(BaseAgent):
    """
    A fully configurable agent created at runtime by the Orchestrator.

    The orchestrator specifies:
    - role_name: e.g. "Database Architect", "Security Auditor"
    - specialization: what this agent focuses on
    - capabilities: which action sets it can use
    - custom_guidelines: specific instructions for this agent
    """

    def __init__(
        self,
        agent_id: str,
        role_name: str = "Specialist",
        specialization: str = "General purpose coding agent",
        capabilities: list[str] = None,
        custom_guidelines: str = "",
        mission_context: str = "",
        **kwargs,
    ):
        super().__init__(
            agent_id=agent_id,
            role=role_name,
            emoji=self._pick_emoji(agent_id),
            color=self._pick_color(agent_id),
            **kwargs,
        )
        self._role_name = role_name
        self._specialization = specialization
        self._capabilities = capabilities or DEFAULT_CAPABILITIES
        self._custom_guidelines = custom_guidelines
        self._mission_context = mission_context

        # Collect available actions from capability sets
        self._available_actions = set()
        for cap in self._capabilities:
            if cap in CAPABILITY_SETS:
                self._available_actions.update(CAPABILITY_SETS[cap]["actions"])
            else:
                # Allow raw action names too
                self._available_actions.add(cap)

    @staticmethod
    def _pick_color(agent_id: str) -> str:
        idx = hash(agent_id) % len(DYNAMIC_COLORS)
        return DYNAMIC_COLORS[idx]

    @staticmethod
    def _pick_emoji(agent_id: str) -> str:
        idx = hash(agent_id) % len(DYNAMIC_EMOJIS)
        return DYNAMIC_EMOJIS[idx]

    @property
    def system_prompt(self) -> str:
        # Build capabilities text
        cap_lines = []
        for cap in self._capabilities:
            if cap in CAPABILITY_SETS:
                info = CAPABILITY_SETS[cap]
                actions_str = ", ".join(f"**{a}**" for a in info["actions"])
                cap_lines.append(f"- {info['description']}: {actions_str}")
        capabilities_text = "\n".join(cap_lines) or "- All standard actions available"

        # Build available actions string
        actions_str = " | ".join(sorted(self._available_actions))

        # Build codebase context
        codebase = self.context.get_codebase_summary()

        # Build task context
        tasks = self.tasks.get_tasks_for_agent(self.agent_id)
        tasks_text = "\n".join(
            f"- [{t.status.value}] {t.title}: {t.description}" for t in tasks
        ) or "No tasks assigned yet."

        prompt = DYNAMIC_AGENT_BASE_PROMPT.format(
            role_name=self._role_name,
            specialization=self._specialization,
            mission_context=self._mission_context,
            capabilities_text=capabilities_text,
            available_actions_str=actions_str,
            custom_guidelines=self._custom_guidelines or "Follow best practices for your specialization.",
        )

        return (
            prompt
            + f"\n\n## Current Codebase\n{codebase}"
            + f"\n\n## Your Assigned Tasks\n{tasks_text}"
        )

    def get_status_dict(self) -> dict:
        base = super().get_status_dict()
        base["specialization"] = self._specialization
        base["is_dynamic"] = True
        return base
