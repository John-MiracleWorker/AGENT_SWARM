"""
Workspace Manager — Shared file workspace with locking, diff tracking,
and optimistic concurrency control to prevent agents from overwriting each other.
"""

import asyncio
import hashlib
import os
import difflib
import logging
from pathlib import Path
from typing import Optional

import aiofiles

from server.core.file_tracker import FileTracker

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """
    Manages the shared file workspace. All file operations are
    path-validated and locked to prevent concurrent write conflicts.
    """

    def __init__(self):
        self._root: Optional[Path] = None
        self._file_locks: dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()
        self._file_versions: dict[str, str] = {}  # path -> last content for diff

        # Optimistic concurrency: track content hashes
        self._file_hashes: dict[str, str] = {}          # path -> current hash on disk
        self._agent_reads: dict[tuple, str] = {}         # (agent_id, path) -> hash when agent last read

        # File activity tracker for conflict detection
        self.file_tracker = FileTracker()

    def set_root(self, path: str):
        """Set the root working directory for this mission."""
        root = Path(path).resolve()
        if not root.exists():
            root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise ValueError(f"Path is not a directory: {path}")
        self._root = root
        logger.info(f"Workspace root set to: {root}")

    @property
    def root(self) -> Path:
        if not self._root:
            raise RuntimeError("Workspace root not set. Call set_root() first.")
        return self._root

    def _validate_path(self, rel_path: str) -> Path:
        """Validate a path is within the workspace root."""
        full = (self.root / rel_path).resolve()
        if not str(full).startswith(str(self.root)):
            raise ValueError(f"Path escapes workspace: {rel_path}")
        return full

    async def _get_lock(self, path: str) -> asyncio.Lock:
        """Get or create a lock for a specific file path."""
        async with self._lock:
            if path not in self._file_locks:
                self._file_locks[path] = asyncio.Lock()
            return self._file_locks[path]

    @staticmethod
    def _hash(content: str) -> str:
        return hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()

    async def read_file(self, rel_path: str, agent_id: str = "") -> str:
        """Read a file from the workspace. Records a content hash for the agent."""
        full = self._validate_path(rel_path)
        if not full.exists():
            raise FileNotFoundError(f"File not found: {rel_path}")
        async with aiofiles.open(full, "r") as f:
            content = await f.read()

        # Record hash so we can detect stale writes later
        h = self._hash(content)
        self._file_hashes[rel_path] = h
        if agent_id:
            self._agent_reads[(agent_id, rel_path)] = h
            self.file_tracker.record(agent_id, rel_path, "read")

        return content

    def _check_stale(self, agent_id: str, rel_path: str) -> Optional[str]:
        """
        Check if a file has been modified since the agent last read it.
        Returns an error message if stale, None if OK.
        """
        if not agent_id:
            return None  # No agent tracking, skip check

        key = (agent_id, rel_path)
        if key not in self._agent_reads:
            # Agent never read this file — only allow if file doesn't exist (new file)
            if rel_path in self._file_hashes:
                return (
                    f"You haven't read '{rel_path}' yet. "
                    f"Use read_file first before modifying it."
                )
            return None  # New file, OK to write

        agent_hash = self._agent_reads[key]
        current_hash = self._file_hashes.get(rel_path)

        if current_hash and agent_hash != current_hash:
            return (
                f"File '{rel_path}' was modified by another agent since you last read it. "
                f"Use read_file to get the latest content before editing."
            )
        return None

    async def write_file(self, rel_path: str, content: str, agent_id: str = "") -> dict:
        """
        Write a file to the workspace with locking.
        Creates a backup before overwriting existing files.
        Checks for stale writes if agent_id is provided.
        Returns a diff dict showing what changed.
        """
        full = self._validate_path(rel_path)
        lock = await self._get_lock(rel_path)

        async with lock:
            # Get old content for diff
            old_content = ""
            if full.exists():
                async with aiofiles.open(full, "r") as f:
                    old_content = await f.read()
                # Create backup before overwriting
                await self._backup_file(full, old_content)

            # Ensure parent directory exists
            full.parent.mkdir(parents=True, exist_ok=True)

            # Write new content
            async with aiofiles.open(full, "w") as f:
                await f.write(content)

            # Update hash tracking
            new_hash = self._hash(content)
            self._file_hashes[rel_path] = new_hash
            if agent_id:
                self._agent_reads[(agent_id, rel_path)] = new_hash
                self.file_tracker.record(agent_id, rel_path, "write")

            # Generate diff
            diff = self._generate_diff(rel_path, old_content, content)

            # Store for future diffs
            self._file_versions[rel_path] = content

            logger.info(f"Wrote file: {rel_path} ({len(content)} chars)")
            return diff

    async def edit_file(self, rel_path: str, search: str, replace: str, agent_id: str = "") -> dict:
        """
        Surgical inline edit — find `search` text in the file and replace with `replace`.
        Much safer than write_file as it only changes the targeted section.
        Checks for stale writes if agent_id is provided.
        Returns a diff dict showing what changed.
        """
        full = self._validate_path(rel_path)
        lock = await self._get_lock(rel_path)

        async with lock:
            if not full.exists():
                raise FileNotFoundError(f"Cannot edit — file not found: {rel_path}")

            async with aiofiles.open(full, "r") as f:
                old_content = await f.read()

            # Check for stale write (file changed since agent last read it)
            stale_msg = self._check_stale(agent_id, rel_path)
            if stale_msg:
                raise ValueError(stale_msg)

            # Validate search string exists
            if search not in old_content:
                raise ValueError(
                    f"Search text not found in {rel_path}. "
                    f"The file may have been modified. Use read_file first to see current content."
                )

            # Count occurrences
            count = old_content.count(search)
            if count > 1:
                logger.warning(f"edit_file: '{search[:50]}...' found {count} times in {rel_path}, replacing first occurrence")

            # Warn if another agent recently modified this file
            recent_writers = self.file_tracker.get_recent_writers(rel_path, exclude=agent_id)
            if recent_writers:
                logger.warning(
                    f"⚠️ File conflict: {agent_id} editing '{rel_path}' "
                    f"which was recently modified by: {', '.join(recent_writers)}"
                )

            # Create backup before editing
            await self._backup_file(full, old_content)

            # Perform the replacement (first occurrence only)
            new_content = old_content.replace(search, replace, 1)

            # Write
            async with aiofiles.open(full, "w") as f:
                await f.write(new_content)

            # Update hash tracking
            new_hash = self._hash(new_content)
            self._file_hashes[rel_path] = new_hash
            if agent_id:
                self._agent_reads[(agent_id, rel_path)] = new_hash
                self.file_tracker.record(agent_id, rel_path, "edit")

            # Generate diff
            diff = self._generate_diff(rel_path, old_content, new_content)
            self._file_versions[rel_path] = new_content

            logger.info(f"Edited file: {rel_path} (replaced {len(search)} chars → {len(replace)} chars)")
            return diff

    async def _backup_file(self, full_path: Path, content: str):
        """Save a timestamped backup of a file before modification."""
        import time
        backup_dir = self.root / ".backups"
        backup_dir.mkdir(exist_ok=True)

        rel = full_path.relative_to(self.root)
        # Flatten path for backup filename: src/app.js → src__app.js
        flat_name = str(rel).replace("/", "__").replace("\\", "__")
        ts = int(time.time())
        backup_path = backup_dir / f"{flat_name}.{ts}.bak"

        async with aiofiles.open(backup_path, "w") as f:
            await f.write(content)
        logger.debug(f"Backup saved: {backup_path.name}")

    async def delete_file(self, rel_path: str) -> bool:
        """Delete a file from the workspace."""
        full = self._validate_path(rel_path)
        lock = await self._get_lock(rel_path)

        async with lock:
            if full.exists():
                full.unlink()
                self._file_versions.pop(rel_path, None)
                logger.info(f"Deleted file: {rel_path}")
                return True
            return False

    async def list_files(self, rel_path: str = "") -> list[dict]:
        """List files and directories in the workspace."""
        target = self._validate_path(rel_path) if rel_path else self.root
        if not target.exists():
            return []

        entries = []
        try:
            for entry in sorted(target.iterdir()):
                # Skip hidden files and common noise
                if entry.name.startswith('.') and entry.name not in ('.env',):
                    continue
                if entry.name in ('__pycache__', 'node_modules', '.git', 'venv', '.venv'):
                    continue

                rel = str(entry.relative_to(self.root))
                if entry.is_dir():
                    children = sum(1 for _ in entry.iterdir()) if entry.is_dir() else 0
                    entries.append({
                        "name": entry.name,
                        "path": rel,
                        "type": "directory",
                        "children": children,
                    })
                else:
                    entries.append({
                        "name": entry.name,
                        "path": rel,
                        "type": "file",
                        "size": entry.stat().st_size,
                    })
        except PermissionError:
            logger.warning(f"Permission denied: {target}")

        return entries

    async def list_files_recursive(self, max_depth: int = 4) -> list[dict]:
        """List all files recursively for codebase scanning."""
        files = []
        for root, dirs, filenames in os.walk(self.root):
            # Filter directories
            dirs[:] = [
                d for d in dirs
                if not d.startswith('.') and d not in ('__pycache__', 'node_modules', 'venv', '.venv')
            ]

            depth = str(root).replace(str(self.root), '').count(os.sep)
            if depth >= max_depth:
                dirs.clear()
                continue

            for fname in filenames:
                if fname.startswith('.') and fname != '.env':
                    continue
                full = Path(root) / fname
                rel = str(full.relative_to(self.root))
                files.append({
                    "path": rel,
                    "size": full.stat().st_size,
                    "ext": full.suffix,
                })

        return files

    def _generate_diff(self, path: str, old: str, new: str) -> dict:
        """Generate a unified diff between old and new content."""
        if not old:
            return {
                "path": path,
                "type": "created",
                "additions": len(new.splitlines()),
                "deletions": 0,
                "diff": f"+++ {path} (new file)\n" + "\n".join(
                    f"+{line}" for line in new.splitlines()[:50]
                ),
            }

        old_lines = old.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{path}", tofile=f"b/{path}",
            lineterm=""
        ))

        additions = sum(1 for l in diff_lines if l.startswith('+') and not l.startswith('+++'))
        deletions = sum(1 for l in diff_lines if l.startswith('-') and not l.startswith('---'))

        return {
            "path": path,
            "type": "modified",
            "additions": additions,
            "deletions": deletions,
            "diff": "\n".join(diff_lines[:100]),  # Cap diff size
        }
