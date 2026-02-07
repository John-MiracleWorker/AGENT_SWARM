"""
REST API Routes — Mission management, file browsing, user intervention.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
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


def _authorize_request(state, x_api_key: str = Header(default="", alias="X-API-Key")):
    """Authorize API requests when AGENT_SWARM_API_KEY is configured."""
    if state.is_authorized(x_api_key):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


def create_router(state) -> APIRouter:
    def authorize(x_api_key: str = Header(default="", alias="X-API-Key")):
        _authorize_request(state, x_api_key)

    router = APIRouter(dependencies=[Depends(authorize)])

    async def reset_mission_runtime_state():
        """Clear mission-scoped runtime state before starting a new mission."""
        state.task_manager.clear()
        await state.message_bus.clear_runtime_state()

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
        await reset_mission_runtime_state()
        ws = state.add_workspace(str(workspace), workspace.name)
        state.switch_workspace(ws["id"])
        state.mission_goal = req.goal
        state.mission_active = True
        state.mission_start_time = time.time()

        # Initialize git
        await state.git_manager.init_repo(str(workspace))

        # Scan existing codebase
        codebase_summary = await state.context_manager.scan_codebase(state.workspace)

        # Upload workspace files to Gemini Files API for full context
        file_upload_result = await state.file_context.upload_workspace(str(workspace))

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
            "file_context": file_upload_result,
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
        state.agents = {}
        state.mission_goal = None

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

    # ── Agent Management ──────────────────────────────────

    @router.get("/agents")
    async def list_agents():
        """List all active agents and spawn capacity."""
        return {
            "agents": [a.get_status_dict() for a in state.agents.values()],
            "spawn_info": state.agent_spawner.get_spawn_info(state.agents),
        }

    @router.post("/agents/spawn")
    async def spawn_agent(role: str, reason: str = "Manual spawn"):
        """Manually spawn a new agent of a given role."""
        if not state.mission_active:
            raise HTTPException(400, "No active mission — cannot spawn agents")

        result = await state.agent_spawner.spawn_agent(
            role=role,
            reason=reason,
            state=state,
        )
        if not result:
            raise HTTPException(400, f"Cannot spawn {role} — at max capacity")

        return {"status": "spawned", "agent": result}

    @router.post("/agents/{agent_id}/kill")
    async def kill_agent(agent_id: str):
        """Remove a dynamically spawned agent."""
        success = await state.agent_spawner.kill_agent(
            agent_id=agent_id,
            state=state,
        )
        if not success:
            raise HTTPException(400, f"Cannot kill agent '{agent_id}' — not found or is a core agent")

        return {"status": "killed", "agent_id": agent_id}

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

    # --- Budget ---

    @router.get("/budget")
    async def get_budget():
        """Get current budget status."""
        return state.gemini.get_budget_status()

    @router.post("/budget")
    async def set_budget(limit_usd: float = 1.0):
        """Set budget limit in USD. Set to 0 for unlimited."""
        state.gemini.set_budget(limit_usd)
        return state.gemini.get_budget_status()

    # --- Message History ---

    @router.get("/messages")
    async def get_messages(limit: int = 50):
        """Get recent message history."""
        return {"messages": state.message_bus.get_history(limit=limit)}

    # --- Mission History ---

    @router.get("/missions/history")
    async def get_mission_history():
        """Get past mission history."""
        return {"missions": state.mission_store.list_missions()}

    @router.get("/missions/history/{mission_id}")
    async def get_mission_detail(mission_id: str):
        """Get details for a specific past mission."""
        detail = state.mission_store.get_mission(mission_id)
        if not detail:
            raise HTTPException(404, "Mission not found")
        return detail

    # --- Git ---

    @router.get("/git/log")
    async def get_git_log():
        """Get git commit log."""
        return {"commits": await state.git_manager.get_log()}

    @router.get("/git/diff")
    async def get_git_diff(path: str = "", sha: str = ""):
        """Get diff — either for a file path or a specific commit."""
        if sha:
            diff = await state.git_manager.get_commit_diff(sha)
        elif path:
            diff = await state.git_manager.get_file_diff(path)
        else:
            diff = await state.git_manager.get_diff()
        return {"diff": diff}

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

    # --- Checkpoints ---

    @router.get("/checkpoints")
    async def get_checkpoints():
        """Get all checkpoint rules."""
        return {"rules": state.checkpoints.get_rules()}

    @router.post("/checkpoints")
    async def add_checkpoint(trigger: str = "custom", pattern: str = "", action: str = "pause"):
        """Add a checkpoint rule."""
        rule = state.checkpoints.add_rule(trigger, pattern, action)
        return rule

    @router.delete("/checkpoints/{rule_id}")
    async def remove_checkpoint(rule_id: str):
        """Remove a checkpoint rule."""
        state.checkpoints.remove_rule(rule_id)
        return {"status": "removed"}

    # --- Agent Memory ---

    @router.get("/memory")
    async def get_memories():
        """Get all stored agent memories/lessons."""
        return {"memories": state.agent_memory.list_memories()}

    @router.delete("/memory/{memory_id}")
    async def delete_memory(memory_id: str):
        """Delete a specific memory."""
        state.agent_memory.delete_memory(memory_id)
        return {"status": "deleted"}

    # --- Plugins ---

    @router.get("/plugins")
    async def get_plugins():
        """Get all available plugins/tools."""
        return {"plugins": state.plugin_registry.list_tools()}

    # --- Workspaces ---

    @router.get("/workspaces")
    async def list_workspaces():
        """List all registered workspaces."""
        return {"workspaces": state.list_workspaces()}

    @router.post("/workspaces")
    async def add_workspace(path: str, name: str = ""):
        """Add a new workspace."""
        ws = state.add_workspace(path, name)
        return ws

    @router.post("/workspaces/{workspace_id}/activate")
    async def activate_workspace(workspace_id: str):
        """Switch active workspace."""
        state.switch_workspace(workspace_id)
        # Re-upload files for new workspace
        ws = state._workspaces.get(workspace_id)
        if ws:
            asyncio.create_task(state.file_context.upload_workspace(ws["path"]))
        return {"status": "switched", "active": workspace_id}

    # --- File Context ---

    @router.get("/file-context")
    async def get_file_context():
        """Get file upload status for Gemini Files API."""
        return state.file_context.upload_status

    @router.post("/file-context/upload")
    async def upload_file_context(path: str = ""):
        """Manually trigger file upload for a workspace path."""
        workspace_path = path or (str(state.workspace.root) if state.workspace._root else "")
        if not workspace_path:
            raise HTTPException(400, "No workspace path provided")
        result = await state.file_context.upload_workspace(workspace_path)
        return result

    @router.post("/file-context/refresh")
    async def refresh_file(path: str):
        """Re-upload a single changed file."""
        await state.file_context.refresh_file(path)
        return {"status": "refreshed", "path": path}

    @router.delete("/file-context")
    async def cleanup_file_context():
        """Delete all uploaded files."""
        await state.file_context.cleanup()
        return {"status": "cleaned"}

    # --- Terminal Sessions ---

    @router.get("/terminal/sessions")
    async def list_terminal_sessions():
        """List active terminal sessions."""
        return {"sessions": state.interactive_terminal.list_sessions()}

    @router.delete("/terminal/sessions/{session_id}")
    async def kill_terminal_session(session_id: str):
        """Kill a specific terminal session."""
        await state.interactive_terminal.kill_session(session_id)
        return {"status": "killed", "session_id": session_id}

    @router.delete("/terminal/sessions")
    async def kill_all_terminal_sessions():
        """Kill all terminal sessions."""
        await state.interactive_terminal.kill_all()
        return {"status": "all_killed"}

    # --- Tools ---

    @router.get("/tools")
    async def list_tools():
        """List all available tools."""
        return {"tools": state.plugin_registry.list_tools()}

    @router.get("/tools/categories")
    async def list_tool_categories():
        """List tool categories."""
        return {"categories": state.plugin_registry.get_categories()}

    @router.get("/tools/suggest")
    async def suggest_tools(context: str = ""):
        """Get intelligent tool suggestions for a context."""
        return {"suggestions": state.plugin_registry.suggest_tools(context)}

    return router

