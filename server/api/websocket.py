"""
WebSocket Handler — Streams all message bus events to frontend clients.
"""

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# Connected WebSocket clients
_clients: set[WebSocket] = set()


async def broadcast_to_clients(message: dict):
    """Broadcast a message to all connected WebSocket clients."""
    if not _clients:
        return

    data = json.dumps(message)
    disconnected = set()

    for ws in _clients:
        try:
            await ws.send_text(data)
        except Exception:
            disconnected.add(ws)

    _clients -= disconnected


async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time event streaming."""
    await websocket.accept()
    _clients.add(websocket)
    logger.info(f"WebSocket client connected ({len(_clients)} total)")

    # Register the broadcast callback with the message bus
    from server.main import state
    state.message_bus.register_ws_callback(broadcast_to_clients)

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
