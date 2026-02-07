"""
REST API Routes — Mission management, file browsing, user intervention.
"""

import os
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server.core.message_bus import MessageType
from server.agents.orchestrator import OrchestratorAgent
from server.agents.developer import DeveloperAgent
from server.agents.reviewer import ReviewerAgent
from server.agents.tester import TesterAgent

logger = logging.getLogger(__name__)


class MissionCreate(BaseModel):
    goal: str
    workspace_path: str


class UserMessage(BaseModel):
    content: str
    target_agent: Optional[str] = None


class ApprovalAction(BaseModel):
    approved: bool


def create_router(state) -> APIRouter:
    router = APIRouter()

    @router.post("/missions")
    async def create_mission(req: MissionCreate):
        """Start a new mission with a goal and workspace path."""
        if state.mission_active:
            raise HTTPException(400, "A mission is already active. Stop it first.")

        # Validate workspace path
        workspace = Path(req.workspace_path).resolve()
        if not workspace.exists():
            workspace.mkdir(parents=True, exist_ok=True)
        if not workspace.is_dir():
            raise HTTPException(400, f"Invalid workspace path: {req.workspace_path}")

        # Initialize workspace
        state.workspace.set_root(str(workspace))
        state.mission_goal = req.goal
        state.mission_active = True

        # Initialize git
        await state.git_manager.init_repo(str(workspace))

        # Scan existing codebase
        codebase_summary = await state.context_manager.scan_codebase(state.workspace)

        # Create shared agent kwargs
        agent_kwargs = dict(
            gemini=state.gemini,
            message_bus=state.message_bus,
            workspace=state.workspace,
            task_manager=state.task_manager,
            terminal=state.terminal,
            context_manager=state.context_manager,
        )

        # Spawn agents
        orchestrator = OrchestratorAgent(**agent_kwargs)
        developer = DeveloperAgent(**agent_kwargs)
        reviewer = ReviewerAgent(**agent_kwargs)
        tester = TesterAgent(**agent_kwargs)

        state.agents = {
            "orchestrator": orchestrator,
            "developer": developer,
            "reviewer": reviewer,
            "tester": tester,
        }

        # Set the goal on the orchestrator
        await orchestrator.set_goal(req.goal)

        # Start all agents
        for agent in state.agents.values():
            await agent.start()

        # Broadcast mission start
        await state.message_bus.publish(
            sender="system",
            sender_role="System",
            msg_type=MessageType.SYSTEM,
            content=f"🚀 Mission started: {req.goal}\n📁 Workspace: {workspace}",
        )

        logger.info(f"Mission started: {req.goal} in {workspace}")

        return {
            "status": "started",
            "goal": req.goal,
            "workspace": str(workspace),
            "agents": [a.get_status_dict() for a in state.agents.values()],
            "codebase_files": len(codebase_summary),
        }

    @router.get("/missions/current")
    async def get_current_mission():
        """Get current mission status."""
        if not state.mission_active:
            return {"status": "no_active_mission"}

        return {
            "status": "active",
            "goal": state.mission_goal,
            "workspace": str(state.workspace.root) if state.workspace._root else None,
            "agents": [a.get_status_dict() for a in state.agents.values()],
            "tasks": state.task_manager.list_tasks(),
            "task_summary": state.task_manager.get_summary(),
            "token_usage": state.gemini.get_all_usage(),
        }

    @router.post("/missions/stop")
    async def stop_mission():
        """Stop the current mission."""
        if not state.mission_active:
            raise HTTPException(400, "No active mission")

        for agent in state.agents.values():
            await agent.stop()
        await state.terminal.kill_all()

        # Final git commit
        await state.git_manager.auto_commit("Mission stopped — final state")

        state.mission_active = False

        await state.message_bus.publish(
            sender="system",
            sender_role="System",
            msg_type=MessageType.SYSTEM,
            content="🛑 Mission stopped by user.",
        )

        return {"status": "stopped"}

    @router.post("/missions/pause")
    async def pause_mission():
        """Pause all agents."""
        for agent in state.agents.values():
            agent.pause()
        await state.message_bus.publish(
            sender="system",
            sender_role="System",
            msg_type=MessageType.SYSTEM,
            content="⏸️ All agents paused.",
        )
        return {"status": "paused"}

    @router.post("/missions/resume")
    async def resume_mission():
        """Resume all agents."""
        for agent in state.agents.values():
            agent.resume()
        await state.message_bus.publish(
            sender="system",
            sender_role="System",
            msg_type=MessageType.SYSTEM,
            content="▶️ All agents resumed.",
        )
        return {"status": "resumed"}

    @router.post("/missions/message")
    async def send_user_message(req: UserMessage):
        """User sends a message to agents."""
        if req.target_agent and req.target_agent in state.agents:
            await state.agents[req.target_agent].inject_message(req.content)
        else:
            # Broadcast to all agents
            for agent in state.agents.values():
                await agent.inject_message(req.content)

        await state.message_bus.publish(
            sender="user",
            sender_role="User",
            msg_type=MessageType.CHAT,
            content=req.content,
            mentions=[req.target_agent] if req.target_agent else [],
        )

        return {"status": "sent"}

    @router.post("/missions/approve/{approval_id}")
    async def approve_action(approval_id: str, req: ApprovalAction):
        """Approve or reject a pending action."""
        for agent in state.agents.values():
            await agent.resolve_approval(approval_id, req.approved)

        await state.message_bus.publish(
            sender="user",
            sender_role="User",
            msg_type=MessageType.APPROVAL_RESPONSE,
            content=f"{'✅ Approved' if req.approved else '❌ Rejected'}: {approval_id}",
        )

        return {"status": "approved" if req.approved else "rejected"}

    # --- File Browsing ---

    @router.get("/files")
    async def list_workspace_files(path: str = ""):
        """List files in the workspace."""
        if not state.workspace._root:
            raise HTTPException(400, "No workspace set")
        files = await state.workspace.list_files(path)
        return {"files": files, "root": str(state.workspace.root)}

    @router.get("/files/content")
    async def read_file(path: str):
        """Read a file from the workspace."""
        if not state.workspace._root:
            raise HTTPException(400, "No workspace set")
        try:
            content = await state.workspace.read_file(path)
            return {"path": path, "content": content}
        except FileNotFoundError:
            raise HTTPException(404, f"File not found: {path}")

    # --- Local Directory Browser (for folder picker) ---

    @router.get("/browse")
    async def browse_directory(path: str = ""):
        """Browse local directories for the folder picker."""
        if not path:
            path = str(Path.home())

        target = Path(path).resolve()
        if not target.exists() or not target.is_dir():
            raise HTTPException(400, f"Invalid path: {path}")

        entries = []
        try:
            for entry in sorted(target.iterdir()):
                if entry.name.startswith('.'):
                    continue
                try:
                    if entry.is_dir():
                        entries.append({
                            "name": entry.name,
                            "path": str(entry),
                            "type": "directory",
                        })
                    elif entry.is_file():
                        entries.append({
                            "name": entry.name,
                            "path": str(entry),
                            "type": "file",
                            "size": entry.stat().st_size,
                        })
                except (OSError, FileNotFoundError):
                    # Skip broken symlinks and inaccessible entries
                    continue
        except PermissionError:
            raise HTTPException(403, f"Permission denied: {path}")

        return {
            "current": str(target),
            "parent": str(target.parent) if target != target.parent else None,
            "entries": entries,
        }

    # --- Token Usage ---

    @router.get("/usage")
    async def get_token_usage():
        """Get token usage statistics."""
        return state.gemini.get_all_usage()

    # --- Message History ---

    @router.get("/messages")
    async def get_messages(limit: int = 50):
        """Get recent message history."""
        return {"messages": state.message_bus.get_history(limit=limit)}

    # --- Git ---

    @router.get("/git/log")
    async def get_git_log():
        """Get git commit log."""
        return {"commits": await state.git_manager.get_log()}

    @router.post("/git/rollback")
    async def rollback_git(sha: str):
        """Rollback to a specific commit."""
        success = await state.git_manager.rollback(sha)
        if not success:
            raise HTTPException(400, "Rollback failed")
        return {"status": "rolled_back", "sha": sha}

    @router.post("/git/sync")
    async def sync_git(message: str = ""):
        """Commit all changes and push to remote."""
        result = await state.git_manager.sync(message)
        return result

    @router.get("/git/status")
    async def get_git_status():
        """Get current git status."""
        return await state.git_manager.get_status()

    return router
