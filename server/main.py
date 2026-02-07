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

from server.core.gemini_client import GeminiClient
from server.core.message_bus import MessageBus
from server.core.workspace import WorkspaceManager
from server.core.task_manager import TaskManager
from server.core.terminal import TerminalExecutor
from server.core.git_manager import GitManager
from server.core.context_manager import ContextManager
from server.api.routes import create_router
from server.api.websocket import websocket_endpoint

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
        if not api_key:
            logger.warning("GEMINI_API_KEY not set! Agents will fail to think.")

        self.gemini = GeminiClient(api_key=api_key, max_rpm=10)
        self.message_bus = MessageBus()
        self.workspace = WorkspaceManager()
        self.task_manager = TaskManager()
        self.terminal = TerminalExecutor()
        self.git_manager = GitManager()
        self.context_manager = ContextManager()
        self.agents: dict = {}
        self.mission_id: str | None = None
        self.mission_goal: str | None = None
        self.mission_active: bool = False


# Global state
state = SwarmState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Agent Swarm starting up...")
    yield
    # Shutdown: stop all agents
    logger.info("🛑 Shutting down agents...")
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

# WebSocket endpoint
app.add_api_websocket_route("/ws", websocket_endpoint)

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
        log_level="info",
    )
