# Agent Swarm — AI Coding Agent Platform

## Project Overview

Agent Swarm is a **multi-agent collaborative coding platform** where multiple AI agents work together to complete software development missions. The system uses Google Gemini models to power autonomous agents that can write code, review each other's work, run tests, and manage tasks.

The platform consists of:
- **Backend**: Python FastAPI server with WebSocket support
- **Frontend**: Vanilla JavaScript SPA with real-time updates
- **AI Engine**: Google Gemini API with intelligent model fallback
- **Agent Swarm**: 4 specialized agent types working in coordination

## Technology Stack

| Component | Technology |
|-----------|------------|
| Backend Framework | FastAPI 0.115.6 |
| WebSocket | native websockets 14.2 |
| AI Models | Google Gemini (gemini-3-flash, gemini-2.5-pro, etc.) |
| HTTP Server | uvicorn 0.34.0 |
| Git Integration | GitPython 3.1.44 |
| Async File I/O | aiofiles 24.1.0 |
| Frontend | Vanilla JavaScript (ES6+), CSS3 |
| Fonts | Inter, JetBrains Mono (Google Fonts) |

## Project Structure

```
AGENT_SWARM/
├── server/                    # Python backend
│   ├── main.py               # FastAPI entry point, SwarmState
│   ├── requirements.txt      # Python dependencies
│   ├── api/
│   │   ├── routes.py         # REST API endpoints
│   │   └── websocket.py      # WebSocket handler
│   ├── core/                 # Core services
│   │   ├── message_bus.py    # Pub/sub inter-agent messaging
│   │   ├── task_manager.py   # Task tracking (todo→in_progress→in_review→done)
│   │   ├── gemini_client.py  # AI client with rate limiting & fallback
│   │   ├── workspace.py      # File operations with locking
│   │   ├── terminal.py       # Sandboxed command execution
│   │   ├── git_manager.py    # Auto-commit, sync, rollback
│   │   └── context_manager.py# Token window management
│   └── agents/               # Agent implementations
│       ├── base_agent.py     # Abstract base with observe→think→act loop
│       ├── orchestrator.py   # PM agent - decomposes goals
│       ├── developer.py      # Code writer
│       ├── reviewer.py       # Code reviewer
│       └── tester.py         # Test writer/runner
├── frontend/                 # Web UI
│   ├── index.html           # Main application shell
│   ├── css/
│   │   └── styles.css       # Dark theme design system
│   └── js/                  # Modular frontend
│       ├── app.js           # Main controller
│       ├── websocket.js     # WebSocket client with reconnection
│       ├── chat-panel.js    # Communication feed
│       ├── agents-panel.js  # Agent status display
│       ├── code-panel.js    # File browser & code viewer
│       ├── task-panel.js    # Kanban board
│       ├── terminal-panel.js# Terminal output
│       └── folder-picker.js # Directory browser modal
├── tests/                   # Unit tests
│   ├── test_task_manager.py
│   ├── test_hello_world.py
│   └── test_hello_swarm.py
├── .env                     # Environment variables (API keys)
└── .env.example             # Template for .env
```

## Build and Run Commands

### Setup

```bash
# 1. Create virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# or: .venv\Scripts\activate  # Windows

# 2. Install dependencies
pip install -r server/requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY
```

### Run the Server

```bash
# Development mode (with auto-reload)
python -m server.main

# Or using uvicorn directly
uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload
```

The server will start at `http://localhost:8000`

### Run Tests

```bash
# Run all tests
python -m unittest discover -s tests -v

# Run specific test file
python -m unittest tests.test_task_manager -v

# Run with pytest (if installed)
pytest tests/
```

## Architecture Details

### Agent Lifecycle (observe → think → act)

All agents inherit from `BaseAgent` and follow this loop:

1. **OBSERVE**: Collect messages from the message bus inbox
2. **THINK**: Send context to Gemini, get structured JSON action
3. **ACT**: Execute the action (write_file, run_command, etc.)

Agent status transitions: `idle` → `thinking` → `acting` → `idle`

### Message Types

Defined in `server/core/message_bus.py`:
- `chat` - General communication
- `code_update` / `file_update` - File changes
- `task_assigned` - Task lifecycle
- `review_request` / `review_result` - Code review flow
- `terminal_output` - Command execution results
- `approval_request` - User intervention needed
- `system` / `agent_status` - System events
- `mission_complete` - Mission finished

### Agent Types

| Agent | Role | Emoji | Responsibilities |
|-------|------|-------|------------------|
| Orchestrator | Project Manager | 🎯 | Decompose goals, assign tasks, coordinate flow |
| Developer | Senior Developer | 💻 | Write code, run commands, iterate on errors |
| Reviewer | Code Reviewer | 🔍 | Review code, provide feedback, debate |
| Tester | QA Engineer | 🧪 | Write tests, run them, report results |

### API Endpoints

**Mission Management** (`/api/missions`):
- `POST /api/missions` - Start new mission (goal + workspace_path)
- `GET /api/missions/current` - Get mission status
- `POST /api/missions/stop` - Stop mission
- `POST /api/missions/pause` - Pause all agents
- `POST /api/missions/resume` - Resume all agents
- `POST /api/missions/message` - Send message to agents
- `POST /api/missions/approve/{id}` - Approve/reject action

**Files** (`/api/files`):
- `GET /api/files?path=` - List workspace files
- `GET /api/files/content?path=` - Read file content
- `GET /api/browse?path=` - Browse local directories (for folder picker)

**Git** (`/api/git`):
- `GET /api/git/status` - Git status
- `GET /api/git/log` - Commit history
- `POST /api/git/sync?message=` - Commit and push
- `POST /api/git/rollback?sha=` - Rollback to commit

**Other**:
- `GET /api/usage` - Token usage statistics
- `GET /api/messages` - Message history
- `WS /ws` - WebSocket for real-time events

## Code Style Guidelines

### Python
- Use type hints where practical
- Docstrings for modules, classes, and public methods
- Use `logging` module, not print statements
- Async/await for I/O operations
- Constants in UPPER_CASE, classes in PascalCase, functions in snake_case

### JavaScript
- ES6+ syntax (const/let, arrow functions, async/await)
- Modular pattern: one component per file
- Event-driven architecture with `SwarmWebSocket`
- CSS custom properties for theming

## Testing Strategy

Tests are written using Python's `unittest` framework:

- `test_task_manager.py` - Unit tests for task lifecycle
- `test_hello_world.py` - File existence and content validation
- `test_hello_swarm.py` - Basic smoke tests

To add new tests:
1. Create file in `tests/` directory
2. Import from `server.core` or `server.agents`
3. Run with `python -m unittest tests.your_test_file`

## Security Considerations

### Command Execution
The `TerminalExecutor` blocks dangerous commands by default. These require user approval:
- `rm -rf`, `rm -r`, `rmdir`
- `sudo`, `chmod`, `chown`
- `pip install`, `npm install`, `brew install`
- `curl`, `wget`, `kill`, `pkill`

### Path Validation
`WorkspaceManager` validates all paths are within the workspace root to prevent directory traversal attacks.

### API Keys
- Store `GEMINI_API_KEY` in `.env` file only
- `.env` is in `.gitignore` and should never be committed
- The `.env.example` shows the expected format without real values

## Configuration

### Environment Variables (`.env`)
```
GEMINI_API_KEY=your-gemini-api-key-here
```

### Model Cascade
The `GeminiClient` automatically falls back through models on rate limits:
1. gemini-3-flash-preview (RPM: 10)
2. gemini-2.5-flash-preview-05-20 (RPM: 10)
3. gemini-2.5-pro-preview-05-06 (RPM: 5)
4. gemini-2.0-flash (RPM: 15)

## Development Workflow

1. **Start the server**: `python -m server.main`
2. **Open browser**: Navigate to `http://localhost:8000`
3. **Select workspace**: Use folder picker to choose a directory
4. **Enter goal**: Describe the coding mission
5. **Launch**: Agents will start working automatically
6. **Monitor**: Watch real-time progress in the UI
7. **Intervene**: Approve dangerous actions, send messages to agents
8. **Sync**: Use Git Sync button to commit and push changes

## Frontend State Management

The frontend uses a simple event-driven architecture:
- `swarmWS` - WebSocket client that emits events
- `app` - Main controller coordinates panels
- Individual panels (`chatPanel`, `agentsPanel`, etc.) subscribe to events

No external state management library is used — vanilla JS with event emitters.
