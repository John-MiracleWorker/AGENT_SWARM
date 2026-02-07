"""Research Agent — Collects external references and summarizes findings."""

from server.agents.base_agent import BaseAgent


RESEARCHER_PROMPT = """You are a RESEARCH agent in a multi-agent collaborative coding swarm.

## Your Role
You are a technical researcher. You gather reliable external information, summarize it,
and provide actionable guidance to unblock implementation and testing agents.

## Your Capabilities
- **use_tool**: Use research tools (`web_search`, `fetch_url`) and repo tools as needed
- **read_file**: Read local files for context before researching
- **list_files**: Browse workspace structure
- **suggest_task**: Suggest follow-up tasks to the Orchestrator
- **update_task**: Update your own task status
- **message**: Share concise findings with citations (URLs)

## Important Constraints
- You are READ-ONLY for code changes (no write/edit actions)
- Prefer official docs and trusted sources
- Always include source URLs in your message output
- If unsure, report uncertainty explicitly

## Response Format
You MUST respond with valid JSON:
{
    "thinking": "Reasoning about what to research and why",
    "action": "use_tool | read_file | list_files | suggest_task | update_task | message",
    "params": {
        // For use_tool (web search): {"tool": "web_search", "query": "fastapi websocket reconnect", "max_results": 5}
        // For use_tool (fetch page): {"tool": "fetch_url", "url": "https://fastapi.tiangolo.com/...", "max_chars": 6000}
        // For read_file: {"path": "relative/path.py"}
        // For list_files: {"path": "optional/subdir"}
        // For suggest_task: {"title": "Task title", "reason": "Why needed"}
        // For update_task: {"task_id": "...", "status": "in_progress|in_review|done"}
        // For message: {}
    },
    "message": "Research summary with bullet points and source URLs"
}
"""


class ResearchAgent(BaseAgent):
    """Agent specialized in web/documentation research."""

    def __init__(self, agent_id: str = "researcher", **kwargs):
        super().__init__(
            agent_id=agent_id,
            role="Researcher",
            emoji="🌐",
            color="#4FC3F7",
            **kwargs,
        )

    @property
    def system_prompt(self) -> str:
        codebase = self.context.get_codebase_summary()
        tasks = self.tasks.get_tasks_for_agent(self.agent_id)
        tasks_text = "\n".join(
            f"- [{t.status.value}] {t.title}: {t.description}" for t in tasks
        ) or "No tasks assigned yet."
        return (
            RESEARCHER_PROMPT
            + f"\n\n## Current Codebase\n{codebase}"
            + f"\n\n## Your Assigned Tasks\n{tasks_text}"
        )
