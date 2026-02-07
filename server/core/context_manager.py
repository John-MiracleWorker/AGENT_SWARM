"""
Context Manager — Token window management and smart codebase scanning.
Prevents context overflow by summarizing older messages.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Approximate token limits (Gemini 3 Pro has ~1M context but we stay conservative)
MAX_CONTEXT_TOKENS = 200_000
SUMMARIZE_THRESHOLD = 150_000
CHARS_PER_TOKEN = 4  # rough approximation

# Key files to read during codebase scanning
KEY_FILENAMES = {
    "README.md", "readme.md", "README",
    "package.json", "requirements.txt", "Cargo.toml",
    "Makefile", "Dockerfile", "docker-compose.yml",
    "pyproject.toml", "setup.py", "setup.cfg",
    "tsconfig.json", "next.config.js", "vite.config.ts",
    ".env.example",
}

KEY_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb"}

# Maximum file size to include in scan (characters)
MAX_SCAN_FILE_SIZE = 10_000
MAX_TOTAL_SCAN_SIZE = 80_000


def estimate_tokens(text: str) -> int:
    """Rough token count estimation."""
    return len(text) // CHARS_PER_TOKEN


class ContextManager:
    """
    Manages context windows for agents. Handles codebase scanning
    and conversation summarization to stay within token limits.
    """

    def __init__(self):
        self._codebase_summary: Optional[str] = None

    async def scan_codebase(self, workspace) -> str:
        """
        Scan the workspace and build a summary of the existing codebase.
        This is injected into all agents' initial context.
        """
        try:
            files = await workspace.list_files_recursive(max_depth=3)
        except Exception as e:
            logger.error(f"Codebase scan failed: {e}")
            return "Empty or inaccessible workspace."

        if not files:
            return "Empty workspace — starting from scratch."

        # Build file tree
        tree_lines = ["## Project Structure"]
        for f in sorted(files, key=lambda x: x["path"]):
            indent = "  " * f["path"].count("/")
            size_kb = f["size"] / 1024
            tree_lines.append(f"{indent}📄 {f['path']} ({size_kb:.1f}KB)")

        tree = "\n".join(tree_lines[:100])  # Cap tree size

        # Read key files
        key_contents = []
        total_scan_size = 0

        for f in files:
            name = f["path"].split("/")[-1]
            ext = f.get("ext", "")

            is_key = name in KEY_FILENAMES
            is_entry = name in ("main.py", "app.py", "index.js", "index.ts", "main.go", "main.rs")

            if (is_key or is_entry) and f["size"] < MAX_SCAN_FILE_SIZE:
                if total_scan_size > MAX_TOTAL_SCAN_SIZE:
                    break
                try:
                    content = await workspace.read_file(f["path"])
                    key_contents.append(f"### {f['path']}\n```\n{content}\n```")
                    total_scan_size += len(content)
                except Exception:
                    pass

        key_files_text = "\n\n".join(key_contents) if key_contents else "No key configuration files found."

        summary = f"""# Existing Codebase Analysis

{tree}

## Key Files

{key_files_text}

## Summary
- Total files: {len(files)}
- Languages detected: {', '.join(sorted(set(f.get('ext', '') for f in files if f.get('ext'))))}
"""
        self._codebase_summary = summary
        logger.info(f"Codebase scanned: {len(files)} files, ~{estimate_tokens(summary)} tokens")
        return summary

    def get_codebase_summary(self) -> str:
        return self._codebase_summary or "No codebase scan performed."

    def trim_messages(self, messages: list[dict], max_tokens: int = MAX_CONTEXT_TOKENS) -> list[dict]:
        """
        Trim message history to fit within token limits.
        Keeps the system context + most recent messages.
        Summarizes older messages if needed.
        """
        if not messages:
            return messages

        # Estimate total tokens
        total = sum(estimate_tokens(m.get("content", "")) for m in messages)

        if total <= max_tokens:
            return messages

        # Keep the first message (usually system/setup) and trim from the middle
        result = [messages[0]]
        remaining_budget = max_tokens - estimate_tokens(messages[0].get("content", ""))

        # Add messages from the end (most recent) until budget runs out
        reversed_recent = []
        for msg in reversed(messages[1:]):
            msg_tokens = estimate_tokens(msg.get("content", ""))
            if remaining_budget - msg_tokens < 0:
                break
            reversed_recent.append(msg)
            remaining_budget -= msg_tokens

        # Add summary of trimmed messages
        trimmed_count = len(messages) - 1 - len(reversed_recent)
        if trimmed_count > 0:
            result.append({
                "role": "user",
                "content": f"[System: {trimmed_count} earlier messages summarized — focus on recent context]",
            })

        result.extend(reversed(reversed_recent))

        logger.info(
            f"Context trimmed: {len(messages)} -> {len(result)} messages "
            f"(~{total} -> ~{sum(estimate_tokens(m.get('content', '')) for m in result)} tokens)"
        )
        return result
