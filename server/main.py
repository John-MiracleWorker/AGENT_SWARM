"""
Agent Swarm — FastAPI Entry Point
Serves the API, WebSocket, and static frontend files.
"""

import asyncio
import logging
import os
import uuid
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from server.core.model_router import ModelRouter
from server.core.message_bus import MessageBus
from server.core.workspace import WorkspaceManager
from server.core.task_manager import TaskManager
from server.core.terminal import TerminalExecutor
from server.core.git_manager import GitManager
from server.core.context_manager import ContextManager
from server.core.mission_store import MissionStore
from server.core.checkpoints import CheckpointManager
from server.core.agent_memory import AgentMemory
from server.core.plugin_registry import PluginRegistry
from server.core.file_context import FileContextManager
from server.core.pty_terminal import InteractiveTerminal
from server.core.agent_spawner import AgentSpawner
from server.api.routes import create_router
from server.api.websocket import websocket_endpoint, terminal_websocket_endpoint

# Load environment
load_dotenv()

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agent_swarm")


class SwarmState:
    """Global state container for the swarm."""

    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY", "")
        groq_key = os.getenv("GROQ_API_KEY", "")
        if not api_key:
            logger.warning("GEMINI_API_KEY not set! Agents will fail to think.")

        self.gemini = ModelRouter(gemini_api_key=api_key, groq_api_key=groq_key)
        self.message_bus = MessageBus()
        self.workspace = WorkspaceManager()
        self.task_manager = TaskManager()
        self.terminal = TerminalExecutor()
        self.git_manager = GitManager()
        self.context_manager = ContextManager()
        self.mission_store = MissionStore()
        self.checkpoints = CheckpointManager()
        self.agent_memory = AgentMemory()
        self.plugin_registry = PluginRegistry()
        self.file_context = FileContextManager(client=self.gemini.client)
        self.interactive_terminal = InteractiveTerminal()
        self.agent_spawner = AgentSpawner()

        # Connect file context to Gemini client for auto-injection
        self.gemini.set_file_context(self.file_context)
        self.agents: dict = {}
        self.mission_id: str | None = None
        self.mission_goal: str | None = None
        self.mission_active: bool = False
        self.mission_start_time: float = 0

        # Multi-workspace support
        self._workspaces: dict[str, dict] = {}  # id -> {path, name, active}
        self._active_workspace_id: str | None = None

    def list_workspaces(self) -> list[dict]:
        """List all registered workspaces."""
        return [
            {**ws, "id": wid, "active": wid == self._active_workspace_id}
            for wid, ws in self._workspaces.items()
        ]

    def add_workspace(self, path: str, name: str = "") -> dict:
        """Register a new workspace."""
        import hashlib
        ws_id = hashlib.md5(path.encode()).hexdigest()[:8]
        self._workspaces[ws_id] = {
            "path": path,
            "name": name or os.path.basename(path),
        }
        if not self._active_workspace_id:
            self._active_workspace_id = ws_id
        return {"id": ws_id, **self._workspaces[ws_id]}

    def switch_workspace(self, workspace_id: str):
        """Switch the active workspace."""
        if workspace_id in self._workspaces:
            self._active_workspace_id = workspace_id
            ws = self._workspaces[workspace_id]
            logger.info(f"Switched to workspace: {ws['name']} ({ws['path']})")


# Global state
state = SwarmState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Agent Swarm starting up...")
    yield
    # Shutdown: stop all agents
    logger.info("🛑 Shutting down...")
    await state.interactive_terminal.kill_all()
    for agent in state.agents.values():
        await agent.stop()
    await state.terminal.kill_all()


app = FastAPI(
    title="Agent Swarm",
    description="Multi-agent collaborative coding platform",
    lifespan=lifespan,
)

# Mount API routes
router = create_router(state)
app.include_router(router, prefix="/api")

# WebSocket endpoints
app.add_api_websocket_route("/ws", websocket_endpoint)
app.add_api_websocket_route("/ws/terminal", terminal_websocket_endpoint)

# Mount frontend static files
frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

    @app.get("/")
    async def serve_frontend():
        return FileResponse(str(frontend_dir / "index.html"))

    @app.get("/{path:path}")
    async def serve_static_fallback(path: str):
        file_path = frontend_dir / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(frontend_dir / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_excludes=["frontend/*", "*.css", "*.js", "*.html"],
        log_level="info",
    )
