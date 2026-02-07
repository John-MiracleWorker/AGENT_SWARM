"""
Plugin Registry — Structured tool system for agents.
Agents can call external tools (linters, formatters, Docker, etc.) as structured actions
instead of raw terminal commands.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    """A registered plugin/tool that agents can invoke."""
    name: str
    description: str
    command_template: str  # e.g. "eslint {path}" or "pytest {path} -v"
    requires_approval: bool = False
    category: str = "utility"
    icon: str = "🔧"


# Built-in tools
BUILTIN_TOOLS = [
    Tool(
        name="lint",
        description="Run linter on a file or directory",
        command_template="cd {workspace} && find . -name '*.py' -exec python3 -m py_compile {} +",
        category="quality",
        icon="🔍",
    ),
    Tool(
        name="format",
        description="Auto-format Python files with black",
        command_template="cd {workspace} && python3 -m black {path}",
        category="quality",
        icon="✨",
    ),
    Tool(
        name="test",
        description="Run test suite",
        command_template="cd {workspace} && python3 -m pytest {path} -v --tb=short",
        category="testing",
        icon="🧪",
    ),
    Tool(
        name="type_check",
        description="Type-check Python files with mypy",
        command_template="cd {workspace} && python3 -m mypy {path}",
        category="quality",
        icon="🔰",
    ),
    Tool(
        name="security_scan",
        description="Scan for security issues with bandit",
        command_template="cd {workspace} && python3 -m bandit -r {path}",
        category="security",
        icon="🛡️",
    ),
    Tool(
        name="count_lines",
        description="Count lines of code",
        command_template="find {workspace} -name '*.py' -o -name '*.js' -o -name '*.ts' | head -50 | xargs wc -l",
        category="info",
        icon="📊",
    ),
    Tool(
        name="dependency_check",
        description="Check for outdated dependencies",
        command_template="cd {workspace} && pip list --outdated 2>/dev/null || npm outdated 2>/dev/null",
        category="deps",
        icon="📦",
    ),
    Tool(
        name="git_blame",
        description="Show git blame for a file",
        command_template="cd {workspace} && git blame {path}",
        category="git",
        icon="📜",
    ),
]


class PluginRegistry:
    """Registry for agent-invokable tools."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        for tool in BUILTIN_TOOLS:
            self._tools[tool.name] = tool

    def register(self, tool: Tool):
        """Register a new tool."""
        self._tools[tool.name] = tool
        logger.info(f"Plugin registered: {tool.name}")

    def get_tool(self, name: str) -> Optional[Tool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[dict]:
        """List all available tools for the UI."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "category": t.category,
                "icon": t.icon,
                "requires_approval": t.requires_approval,
            }
            for t in self._tools.values()
        ]

    def build_command(self, tool_name: str, workspace: str, path: str = ".") -> Optional[str]:
        """Build the actual command string for a tool invocation."""
        tool = self.get_tool(tool_name)
        if not tool:
            return None
        return tool.command_template.format(workspace=workspace, path=path)

    def get_tools_for_prompt(self) -> str:
        """Format tools as part of agent system prompt."""
        lines = ["## Available Tools", "You can use these tools via the `use_tool` action:"]
        for t in self._tools.values():
            lines.append(f"- **{t.name}** ({t.icon}): {t.description}")
        return "\n".join(lines)
