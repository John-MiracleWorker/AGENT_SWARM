"""
Git Manager — Automatic version control for agent workspace.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class GitManager:
    """
    Manages git operations in the workspace directory.
    Auto-initializes repo and creates commits as agents work.
    """

    def __init__(self):
        self._repo = None
        self._root: Optional[Path] = None

    async def init_repo(self, workspace_path: str) -> bool:
        """Initialize a git repo in the workspace if not already one."""
        try:
            import git
        except ImportError:
            logger.warning("GitPython not installed. Git integration disabled.")
            return False

        self._root = Path(workspace_path)

        try:
            self._repo = git.Repo(workspace_path)
            logger.info(f"Opened existing git repo: {workspace_path}")
        except git.InvalidGitRepositoryError:
            self._repo = await asyncio.to_thread(
                git.Repo.init, workspace_path
            )
            # Create initial commit
            await self.auto_commit("Initial commit — Agent Swarm workspace")
            logger.info(f"Initialized new git repo: {workspace_path}")
        except Exception as e:
            logger.error(f"Git init failed: {e}")
            return False

        return True

    async def auto_commit(self, message: str, files: Optional[list[str]] = None) -> Optional[str]:
        """
        Stage and commit changes. If files specified, only stage those.
        Returns the commit hash or None if nothing to commit.
        """
        if not self._repo:
            return None

        try:
            def _do_commit():
                if files:
                    for f in files:
                        try:
                            self._repo.index.add([f])
                        except Exception:
                            pass
                else:
                    # Stage all changes
                    self._repo.git.add(A=True)

                # Check if there are staged changes
                if not self._repo.index.diff("HEAD") and not self._repo.untracked_files:
                    if self._repo.head.is_valid():
                        return None

                commit = self._repo.index.commit(message)
                return str(commit.hexsha)[:8]

            sha = await asyncio.to_thread(_do_commit)
            if sha:
                logger.info(f"Git commit {sha}: {message}")
            return sha

        except Exception as e:
            logger.error(f"Git commit failed: {e}")
            return None

    async def get_diff(self) -> str:
        """Get current uncommitted changes as a diff string."""
        if not self._repo:
            return ""

        try:
            diff = await asyncio.to_thread(
                lambda: self._repo.git.diff() + "\n" + self._repo.git.diff(staged=True)
            )
            return diff
        except Exception as e:
            logger.error(f"Git diff failed: {e}")
            return ""

    async def get_log(self, max_count: int = 10) -> list[dict]:
        """Get recent commit log."""
        if not self._repo:
            return []

        try:
            def _get_log():
                commits = []
                for commit in self._repo.iter_commits(max_count=max_count):
                    commits.append({
                        "sha": str(commit.hexsha)[:8],
                        "message": commit.message.strip(),
                        "timestamp": commit.committed_date,
                        "files_changed": len(commit.stats.files),
                    })
                return commits

            return await asyncio.to_thread(_get_log)
        except Exception as e:
            logger.error(f"Git log failed: {e}")
            return []

    async def rollback(self, commit_sha: str) -> bool:
        """Rollback to a specific commit."""
        if not self._repo:
            return False

        try:
            await asyncio.to_thread(
                lambda: self._repo.git.reset("--hard", commit_sha)
            )
            logger.info(f"Rolled back to {commit_sha}")
            return True
        except Exception as e:
            logger.error(f"Git rollback failed: {e}")
            return False

    async def sync(self, message: str = "") -> dict:
        """
        Stage all changes, commit, and push to remote origin.
        Returns a status dict with results.
        """
        if not self._repo:
            return {"ok": False, "error": "No git repo initialized"}

        try:
            def _do_sync():
                result = {"ok": True, "committed": False, "pushed": False}

                # Stage all changes
                self._repo.git.add(A=True)

                # Check for changes to commit
                has_staged = bool(self._repo.index.diff("HEAD"))
                has_untracked = bool(self._repo.untracked_files)

                # Handle initial commit case (no HEAD yet)
                try:
                    self._repo.head.commit
                    head_exists = True
                except ValueError:
                    head_exists = False

                if has_staged or has_untracked or not head_exists:
                    commit_msg = message or "Agent Swarm sync"
                    commit = self._repo.index.commit(commit_msg)
                    result["committed"] = True
                    result["sha"] = str(commit.hexsha)[:8]
                    result["message"] = commit_msg
                else:
                    result["message"] = "Nothing to commit — working tree clean"

                # Push to remote if one exists
                if self._repo.remotes:
                    remote = self._repo.remotes.origin
                    branch = self._repo.active_branch.name
                    push_info = remote.push(branch)
                    result["pushed"] = True
                    result["remote"] = remote.url
                    result["branch"] = branch

                    # Check for push errors
                    for info in push_info:
                        if info.flags & info.ERROR:
                            result["ok"] = False
                            result["error"] = f"Push failed: {info.summary}"
                            result["pushed"] = False
                else:
                    result["pushed"] = False
                    result["remote_note"] = "No remote configured — committed locally only"

                return result

            return await asyncio.to_thread(_do_sync)

        except Exception as e:
            logger.error(f"Git sync failed: {e}")
            return {"ok": False, "error": str(e)}

    async def get_status(self) -> dict:
        """Get current git status for the UI."""
        if not self._repo:
            return {"initialized": False}

        try:
            def _status():
                status = {
                    "initialized": True,
                    "dirty": self._repo.is_dirty(untracked_files=True),
                    "untracked": len(self._repo.untracked_files),
                    "branch": "unknown",
                    "has_remote": bool(self._repo.remotes),
                }
                try:
                    status["branch"] = self._repo.active_branch.name
                except TypeError:
                    status["branch"] = "HEAD (detached)"

                if self._repo.remotes:
                    status["remote_url"] = self._repo.remotes.origin.url

                # Count modified files
                status["modified"] = len(self._repo.index.diff(None))
                status["staged"] = len(self._repo.index.diff("HEAD"))

                return status

            return await asyncio.to_thread(_status)
        except Exception as e:
            logger.error(f"Git status failed: {e}")
            return {"initialized": True, "error": str(e)}

