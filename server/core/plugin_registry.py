"""
Plugin Registry — Comprehensive tool system for agents.
Agents can call external tools (linters, formatters, search, Docker, etc.)
as structured actions instead of raw terminal commands.

Includes intelligent tool selection — agents get context-aware tool suggestions
based on what they're currently doing.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    """A registered plugin/tool that agents can invoke."""
    name: str
    description: str
    command_template: str          # e.g. "eslint {path}" or "pytest {path} -v"
    requires_approval: bool = False
    category: str = "utility"
    icon: str = "🔧"
    tags: list[str] = field(default_factory=list)   # For intelligent matching
    output_hint: str = ""           # Tell agent what to expect
    dangerous: bool = False         # Requires confirmation


# ──────────────────────────────────────────────
#  Built-in Tools — grouped by category
# ──────────────────────────────────────────────

BUILTIN_TOOLS = [
    # ── Search & Navigation ──
    Tool(
        name="grep",
        description="Search for a pattern in files recursively (regex supported)",
        command_template='cd {workspace} && grep -rn --include="*.py" --include="*.js" --include="*.ts" --include="*.jsx" --include="*.tsx" --include="*.css" --include="*.html" "{pattern}" {path}',
        category="search",
        icon="🔍",
        tags=["find", "search", "pattern", "locate", "reference"],
        output_hint="Returns matching lines with file paths and line numbers",
    ),
    Tool(
        name="grep_all",
        description="Search for a pattern in ALL file types recursively",
        command_template='cd {workspace} && grep -rn "{pattern}" {path} --include="*" --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=__pycache__',
        category="search",
        icon="🔎",
        tags=["find", "search", "pattern", "all"],
        output_hint="Returns matching lines across all file types",
    ),
    Tool(
        name="find_files",
        description="Find files by name or extension pattern",
        command_template='cd {workspace} && find {path} -name "{pattern}" -not -path "*/node_modules/*" -not -path "*/.git/*" -not -path "*/__pycache__/*" 2>/dev/null | head -50',
        category="search",
        icon="📂",
        tags=["find", "locate", "file", "name", "extension"],
        output_hint="Returns list of matching file paths",
    ),
    Tool(
        name="tree",
        description="Show directory tree structure (max 3 levels)",
        command_template='cd {workspace} && find {path} -maxdepth 3 -not -path "*/node_modules/*" -not -path "*/.git/*" -not -path "*/__pycache__/*" | head -100 | sort',
        category="search",
        icon="🌳",
        tags=["structure", "layout", "directory", "project", "overview"],
        output_hint="Returns indented directory tree",
    ),

    # ── Code Quality ──
    Tool(
        name="lint_python",
        description="Run Python syntax and lint check",
        command_template="cd {workspace} && python3 -m py_compile {path} && echo '✅ No syntax errors'",
        category="quality",
        icon="🐍",
        tags=["lint", "check", "python", "syntax", "compile"],
        output_hint="Reports syntax errors or confirms clean",
    ),
    Tool(
        name="lint_js",
        description="Run ESLint on JavaScript/TypeScript files",
        command_template="cd {workspace} && npx eslint {path} --no-error-on-unmatched-pattern 2>/dev/null || echo '(ESLint not configured)'",
        category="quality",
        icon="📐",
        tags=["lint", "javascript", "typescript", "eslint"],
        output_hint="Reports JS/TS linting issues",
    ),
    Tool(
        name="format_python",
        description="Auto-format Python files with black (or autopep8 fallback)",
        command_template="cd {workspace} && (python3 -m black {path} 2>/dev/null || python3 -m autopep8 --in-place {path} 2>/dev/null || echo 'No formatter available')",
        category="quality",
        icon="✨",
        tags=["format", "style", "python", "black", "autopep8"],
        output_hint="Reformats files and reports changes",
    ),
    Tool(
        name="format_js",
        description="Auto-format JS/TS files with prettier",
        command_template="cd {workspace} && npx prettier --write {path} 2>/dev/null || echo '(Prettier not configured)'",
        category="quality",
        icon="💅",
        tags=["format", "javascript", "typescript", "prettier"],
        output_hint="Reformats files and reports changes",
    ),
    Tool(
        name="type_check",
        description="Type-check Python files with mypy",
        command_template="cd {workspace} && python3 -m mypy {path} --ignore-missing-imports 2>/dev/null || echo '(mypy not installed)'",
        category="quality",
        icon="🔰",
        tags=["type", "check", "mypy", "python", "types"],
        output_hint="Reports type errors and suggestions",
    ),

    # ── Testing ──
    Tool(
        name="test",
        description="Run Python test suite with pytest",
        command_template="cd {workspace} && python3 -m pytest {path} -v --tb=short 2>&1 | head -100",
        category="testing",
        icon="🧪",
        tags=["test", "pytest", "unit", "integration", "verify"],
        output_hint="Returns test results with pass/fail counts",
    ),
    Tool(
        name="test_js",
        description="Run JavaScript tests with npm test",
        command_template="cd {workspace} && npm test -- --watchAll=false 2>&1 | head -100",
        category="testing",
        icon="🧫",
        tags=["test", "jest", "mocha", "javascript", "npm"],
        output_hint="Returns JS test results",
    ),
    Tool(
        name="test_single",
        description="Run a single test file",
        command_template="cd {workspace} && python3 -m pytest {path} -v --tb=long 2>&1",
        category="testing",
        icon="🎯",
        tags=["test", "single", "specific", "debug"],
        output_hint="Detailed results for one test file",
    ),

    # ── Security ──
    Tool(
        name="security_scan",
        description="Scan for security issues with bandit",
        command_template="cd {workspace} && python3 -m bandit -r {path} -f json 2>/dev/null | python3 -m json.tool 2>/dev/null || python3 -m bandit -r {path} 2>/dev/null || echo '(bandit not installed)'",
        category="security",
        icon="🛡️",
        tags=["security", "vulnerability", "audit", "bandit"],
        output_hint="Reports security vulnerabilities by severity",
    ),
    Tool(
        name="secret_scan",
        description="Scan for hardcoded secrets (API keys, passwords, tokens)",
        command_template=(
            'cd {workspace} && grep -rn --include="*.py" --include="*.js"'
            ' --include="*.ts" --include="*.env"'
            ' -iE "(api_key|secret|password|token|private_key)\\s*=" {path}'
            ' 2>/dev/null | head -20'
        ),
        category="security",
        icon="🔑",
        tags=["secret", "key", "password", "credential", "leak"],
        output_hint="Reports potential hardcoded secrets",
    ),

    # ── Dependencies ──
    Tool(
        name="deps_check",
        description="Check for outdated dependencies",
        command_template="cd {workspace} && (pip list --outdated 2>/dev/null | head -20) || (npm outdated 2>/dev/null) || echo 'No package manager found'",
        category="deps",
        icon="📦",
        tags=["dependency", "outdated", "update", "package"],
        output_hint="Lists outdated packages with current/latest versions",
    ),
    Tool(
        name="install_python",
        description="Install Python dependencies from requirements.txt",
        command_template="cd {workspace} && pip install -r requirements.txt 2>&1 | tail -10",
        category="deps",
        icon="📥",
        tags=["install", "pip", "requirements", "python"],
        requires_approval=True,
        output_hint="Installs packages and reports results",
    ),
    Tool(
        name="install_npm",
        description="Install Node.js dependencies from package.json",
        command_template="cd {workspace} && npm install 2>&1 | tail -10",
        category="deps",
        icon="📥",
        tags=["install", "npm", "node", "javascript"],
        requires_approval=True,
        output_hint="Installs packages and reports results",
    ),

    # ── File Operations ──
    Tool(
        name="file_info",
        description="Get detailed info about a file (size, type, encoding, line count)",
        command_template='cd {workspace} && echo "=== {path} ===" && file {path} && wc -l {path} && ls -lh {path}',
        category="info",
        icon="📋",
        tags=["info", "metadata", "size", "lines", "file"],
        output_hint="Returns file type, encoding, size, and line count",
    ),
    Tool(
        name="count_lines",
        description="Count lines of code by language",
        command_template="cd {workspace} && (cloc {path} 2>/dev/null || (echo 'Python:' && find {path} -name '*.py' | xargs wc -l 2>/dev/null | tail -1; echo 'JavaScript:' && find {path} -name '*.js' -o -name '*.ts' | xargs wc -l 2>/dev/null | tail -1))",
        category="info",
        icon="📊",
        tags=["count", "lines", "statistics", "metrics", "loc"],
        output_hint="Returns line counts by language",
    ),
    Tool(
        name="search_replace",
        description="Search and replace text across files (dry run first, then apply)",
        command_template='cd {workspace} && find {path} -type f \\( -name "*.py" -o -name "*.js" -o -name "*.ts" \\) -exec grep -l "{pattern}" {{}} \\; 2>/dev/null | head -20',
        category="refactor",
        icon="🔄",
        tags=["replace", "refactor", "rename", "find-replace"],
        output_hint="Lists files containing the pattern for review before replacing",
    ),
    Tool(
        name="head",
        description="Show the first N lines of a file",
        command_template="cd {workspace} && head -50 {path}",
        category="info",
        icon="📖",
        tags=["view", "preview", "top", "beginning"],
        output_hint="First 50 lines of the file",
    ),
    Tool(
        name="tail",
        description="Show the last N lines of a file",
        command_template="cd {workspace} && tail -50 {path}",
        category="info",
        icon="📖",
        tags=["view", "end", "bottom", "last"],
        output_hint="Last 50 lines of the file",
    ),

    # ── Git ──
    Tool(
        name="git_status",
        description="Show git repository status",
        command_template="cd {workspace} && git status --short",
        category="git",
        icon="📊",
        tags=["git", "status", "changes", "modified"],
        output_hint="Short status of modified/added/deleted files",
    ),
    Tool(
        name="git_diff",
        description="Show unstaged changes in the workspace",
        command_template="cd {workspace} && git diff --stat && echo '---' && git diff {path}",
        category="git",
        icon="📝",
        tags=["git", "diff", "changes", "compare"],
        output_hint="Shows line-by-line diff of changes",
    ),
    Tool(
        name="git_log",
        description="Show recent git commit history",
        command_template="cd {workspace} && git log --oneline -20",
        category="git",
        icon="📜",
        tags=["git", "log", "history", "commits"],
        output_hint="Last 20 commits with short messages",
    ),
    Tool(
        name="git_blame",
        description="Show git blame for a file (who changed what)",
        command_template="cd {workspace} && git blame {path} 2>/dev/null | head -50",
        category="git",
        icon="👤",
        tags=["git", "blame", "author", "history"],
        output_hint="Line-by-line attribution with commit SHAs",
    ),

    # ── Build & Run ──
    Tool(
        name="build",
        description="Build the project (auto-detects build system)",
        command_template="cd {workspace} && (make 2>/dev/null || npm run build 2>/dev/null || python3 setup.py build 2>/dev/null || echo 'No build system detected')",
        category="build",
        icon="🏗️",
        tags=["build", "compile", "make", "webpack"],
        requires_approval=True,
        output_hint="Build output with errors/warnings",
    ),
    Tool(
        name="run_script",
        description="Run a Python or Node script",
        command_template="cd {workspace} && timeout 30 python3 {path} 2>&1 || timeout 30 node {path} 2>&1",
        category="build",
        icon="▶️",
        tags=["run", "execute", "script", "python", "node"],
        requires_approval=True,
        output_hint="Script output (30s timeout)",
    ),

    # ── Documentation ──
    Tool(
        name="doc_check",
        description="Check for missing docstrings in Python files",
        command_template='cd {workspace} && python3 -c "import ast, sys; t=ast.parse(open(\\"{path}\\\").read()); missing=[n.name for n in ast.walk(t) if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and not ast.get_docstring(n)]; print(f\\"Missing docstrings: {{missing}}\\") if missing else print(\\"All documented ✅\\")" 2>/dev/null || echo "Could not parse file"',
        category="quality",
        icon="📝",
        tags=["docs", "docstring", "documentation", "comments"],
        output_hint="Lists functions/classes missing docstrings",
    ),

    # ── Docker & Infra ──
    Tool(
        name="docker_build",
        description="Build Docker image from Dockerfile",
        command_template="cd {workspace} && docker build -t agent-swarm-build . 2>&1 | tail -20",
        category="infra",
        icon="🐳",
        tags=["docker", "container", "build", "image"],
        requires_approval=True,
        output_hint="Docker build output",
    ),
    Tool(
        name="docker_ps",
        description="List running Docker containers",
        command_template="docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || echo '(Docker not running)'",
        category="infra",
        icon="🐳",
        tags=["docker", "containers", "running", "list"],
        output_hint="Table of running containers",
    ),
    Tool(
        name="port_check",
        description="Check what process is using a specific port",
        command_template="lsof -i :{pattern} 2>/dev/null || echo 'Port {pattern} is free'",
        category="infra",
        icon="🔌",
        tags=["port", "network", "process", "listen"],
        output_hint="Process info for ports in use",
    ),

    # ── Performance ──
    Tool(
        name="complexity",
        description="Analyze code complexity (cyclomatic complexity)",
        command_template="cd {workspace} && python3 -m mccabe --min 5 {path} 2>/dev/null || echo '(mccabe not installed — pip install mccabe)'",
        category="quality",
        icon="📈",
        tags=["complexity", "cyclomatic", "quality", "metrics"],
        output_hint="Functions with complexity above threshold",
    ),
    Tool(
        name="disk_usage",
        description="Show disk usage of workspace directories",
        command_template='cd {workspace} && du -sh {path}/* 2>/dev/null | sort -rh | head -20',
        category="info",
        icon="💾",
        tags=["disk", "size", "space", "usage"],
        output_hint="Directory sizes sorted by size",
    ),
]


# ──────────────────────────────────────────────
#  Intelligent Tool Selection
# ──────────────────────────────────────────────

# Maps task context keywords → relevant tool names
CONTEXT_TOOL_MAP = {
    # When writing code
    "writing": ["lint_python", "lint_js", "format_python", "format_js", "type_check"],
    "implementing": ["lint_python", "lint_js", "test", "grep", "find_files"],
    "coding": ["lint_python", "lint_js", "format_python", "test"],
    
    # When debugging
    "debugging": ["grep", "grep_all", "test_single", "git_diff", "file_info"],
    "error": ["grep", "test_single", "lint_python", "lint_js", "git_diff"],
    "bug": ["grep", "test_single", "git_diff", "git_blame"],
    "fix": ["grep", "test", "lint_python", "git_diff"],
    
    # When reviewing
    "reviewing": ["lint_python", "lint_js", "type_check", "security_scan", "secret_scan", "test", "complexity"],
    "review": ["security_scan", "test", "lint_python", "complexity", "doc_check"],
    
    # When testing
    "testing": ["test", "test_js", "test_single"],
    "verify": ["test", "lint_python", "type_check"],
    
    # When refactoring
    "refactoring": ["grep", "search_replace", "test", "format_python", "git_diff"],
    "rename": ["grep", "search_replace", "find_files"],
    
    # When exploring
    "understanding": ["tree", "find_files", "grep", "count_lines", "head"],
    "exploring": ["tree", "find_files", "file_info", "grep", "count_lines"],
    "onboarding": ["tree", "count_lines", "deps_check", "find_files"],
    
    # When deploying
    "deploying": ["test", "security_scan", "secret_scan", "build", "docker_build"],
    "shipping": ["test", "security_scan", "build"],
    
    # When setting up
    "setup": ["install_python", "install_npm", "deps_check", "tree"],
    "installing": ["install_python", "install_npm"],
}


class PluginRegistry:
    """Registry for agent-invokable tools with intelligent selection."""

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
                "tags": t.tags,
            }
            for t in self._tools.values()
        ]

    def list_by_category(self, category: str) -> list[dict]:
        """List tools filtered by category."""
        return [
            {"name": t.name, "description": t.description, "icon": t.icon}
            for t in self._tools.values()
            if t.category == category
        ]

    def get_categories(self) -> list[str]:
        """Get all unique tool categories."""
        return sorted(set(t.category for t in self._tools.values()))

    def build_command(
        self,
        tool_name: str,
        workspace: str,
        path: str = ".",
        pattern: str = "",
    ) -> Optional[str]:
        """Build the actual command string for a tool invocation."""
        tool = self.get_tool(tool_name)
        if not tool:
            return None
        try:
            return tool.command_template.format(
                workspace=workspace,
                path=path,
                pattern=pattern,
            )
        except KeyError:
            # Template uses keys not provided — fill what we can
            return tool.command_template.replace("{workspace}", workspace).replace("{path}", path).replace("{pattern}", pattern)

    # ──────────────────────────────────────────
    #  Intelligent Tool Selection
    # ──────────────────────────────────────────

    def suggest_tools(self, context: str, max_suggestions: int = 5) -> list[dict]:
        """
        Intelligently suggest tools based on the current task context.
        Analyzes the context string for keywords and returns ranked suggestions.
        """
        context_lower = context.lower()
        scores: dict[str, float] = {}

        # Score from CONTEXT_TOOL_MAP keyword matches
        for keyword, tool_names in CONTEXT_TOOL_MAP.items():
            if keyword in context_lower:
                for i, name in enumerate(tool_names):
                    if name in self._tools:
                        # Higher score for earlier entries (more relevant)
                        scores[name] = scores.get(name, 0) + (1.0 - i * 0.1)

        # Score from tool tag matches
        words = set(re.findall(r'\b\w+\b', context_lower))
        for tool in self._tools.values():
            for tag in tool.tags:
                if tag in words:
                    scores[tool.name] = scores.get(tool.name, 0) + 0.5

        # Sort by score and return top suggestions
        ranked = sorted(scores.items(), key=lambda x: -x[1])[:max_suggestions]
        result = []
        for name, score in ranked:
            tool = self._tools[name]
            result.append({
                "name": tool.name,
                "description": tool.description,
                "icon": tool.icon,
                "category": tool.category,
                "relevance_score": round(score, 2),
            })

        return result

    def get_tools_for_prompt(self, context: str = "") -> str:
        """
        Format tools as part of agent system prompt.
        If context is provided, highlight suggested tools first.
        """
        lines = [
            "## Available Tools",
            "You can use these tools via the `use_tool` action.",
            "Specify: {\"action\": \"use_tool\", \"params\": {\"tool\": \"<name>\", \"path\": \"<target>\", \"pattern\": \"<search_term>\"}}",
            "",
        ]

        # If we have context, show intelligent suggestions first
        if context:
            suggestions = self.suggest_tools(context, max_suggestions=5)
            if suggestions:
                lines.append("### 💡 Suggested for current task:")
                for s in suggestions:
                    lines.append(f"  - **{s['name']}** ({s['icon']}): {s['description']}")
                lines.append("")

        # Group by category
        categories: dict[str, list[Tool]] = {}
        for tool in self._tools.values():
            categories.setdefault(tool.category, []).append(tool)

        for cat, tools in sorted(categories.items()):
            lines.append(f"### {cat.title()}")
            for t in tools:
                approval_note = " ⚠️ requires approval" if t.requires_approval else ""
                lines.append(f"- **{t.name}** ({t.icon}): {t.description}{approval_note}")
            lines.append("")

        return "\n".join(lines)
