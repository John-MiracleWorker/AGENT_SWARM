"""
WebSocket Handler — Streams message bus events AND interactive terminal I/O.
Supports two WebSocket endpoints:
  /ws — Event stream for agent messages, status, etc.
  /ws/terminal — Interactive terminal I/O
"""

import asyncio
import json
import logging
import uuid
import os

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# Connected WebSocket clients for event stream
_clients: set[WebSocket] = set()
_broadcast_registered = False


def _is_websocket_authorized(websocket: WebSocket, state) -> bool:
    """Validate WebSocket X-API-Key header when authentication is enabled."""
    token = websocket.headers.get("x-api-key", "")
    return state.is_authorized(token)


async def broadcast_to_clients(message: dict):
    """Broadcast a message to all connected WebSocket clients."""
    global _clients
    if not _clients:
        return

    data = json.dumps(message)
    disconnected = set()

    for ws in _clients.copy():
        try:
            await ws.send_text(data)
        except Exception:
            disconnected.add(ws)

    for ws in disconnected:
        _clients.discard(ws)


async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time event streaming."""
    global _broadcast_registered
    await websocket.accept()
    _clients.add(websocket)
    logger.info(f"WebSocket client connected ({len(_clients)} total)")

    # Register the broadcast callback ONCE, not per-connection
    from server.main import state
    if not _is_websocket_authorized(websocket, state):
        await websocket.close(code=1008)
        return

    if not _broadcast_registered:
        state.message_bus.register_ws_callback(broadcast_to_clients)
        _broadcast_registered = True

    try:
        # Send initial state
        await websocket.send_text(json.dumps({
            "type": "connection",
            "data": {
                "status": "connected",
                "mission_active": state.mission_active,
                "agents": [a.get_status_dict() for a in state.agents.values()] if state.agents else [],
            },
        }))

        # Keep connection alive and handle incoming messages
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                # Handle ping/pong for keepalive
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                # Send keepalive ping
                try:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break
            except json.JSONDecodeError:
                pass

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        _clients.discard(websocket)
        logger.info(f"WebSocket client disconnected ({len(_clients)} total)")


# ──────────────────────────────────────────────
#  Interactive Terminal WebSocket
# ──────────────────────────────────────────────

async def terminal_websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for interactive terminal sessions.
    
    Protocol:
      Client → Server:
        {"type": "input", "data": "<keystrokes>"}
        {"type": "resize", "cols": 120, "rows": 30}
        {"type": "ping"}
      
      Server → Client:
        {"type": "output", "data": "<terminal output>"}
        {"type": "session", "session_id": "...", "status": "created"}
        {"type": "exit", "code": 0}
        {"type": "pong"}
    """
    await websocket.accept()

    from server.main import state
    if not _is_websocket_authorized(websocket, state):
        await websocket.close(code=1008)
        return

    session_id = str(uuid.uuid4())[:8]
    logger.info(f"Terminal WebSocket connected, creating session {session_id}")

    # Determine initial working directory
    cwd = str(state.workspace.root) if state.workspace._root else os.path.expanduser("~")

    try:
        # Create terminal session with output callback
        async def on_output(sid: str, data: str):
            """Stream terminal output to WebSocket."""
            try:
                await websocket.send_text(json.dumps({
                    "type": "output",
                    "data": data,
                }))
            except Exception:
                pass

        session = await state.interactive_terminal.create_session(
            session_id=session_id,
            cwd=cwd,
            output_callback=on_output,
        )

        # Notify client of session creation
        await websocket.send_text(json.dumps({
            "type": "session",
            "session_id": session_id,
            "status": "created",
            "cwd": cwd,
            "shell": session.shell,
        }))

        # Main I/O loop — read input from WebSocket, write to PTY
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                msg = json.loads(raw)
                msg_type = msg.get("type", "")

                if msg_type == "input":
                    # Forward keystrokes to terminal
                    data = msg.get("data", "")
                    if data:
                        await state.interactive_terminal.write_input(session_id, data)

                elif msg_type == "resize":
                    cols = msg.get("cols", 120)
                    rows = msg.get("rows", 30)
                    await state.interactive_terminal.resize(session_id, cols, rows)

                elif msg_type == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))

            except asyncio.TimeoutError:
                # Send keepalive
                try:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    break

                # Check if session is still alive
                session_obj = state.interactive_terminal._sessions.get(session_id)
                if session_obj and not session_obj.active:
                    await websocket.send_text(json.dumps({
                        "type": "exit",
                        "code": 0,
                    }))
                    break

            except json.JSONDecodeError:
                pass

    except WebSocketDisconnect:
        logger.info(f"Terminal WebSocket disconnected: {session_id}")
    except Exception as e:
        logger.error(f"Terminal WebSocket error: {e}")
    finally:
        # Clean up the terminal session
        await state.interactive_terminal.kill_session(session_id)
        logger.info(f"Terminal session cleaned up: {session_id}")

