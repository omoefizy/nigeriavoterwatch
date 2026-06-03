"""WebSocket endpoint — clients subscribe here to receive live result updates."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from app.ws.manager import manager

router = APIRouter()


@router.websocket("/results")
async def ws_results(websocket: WebSocket):
    # Browsers always send an Origin header for WebSocket upgrades; reject any
    # origin that is not in the configured allow-list (same set as CORS).
    origin = websocket.headers.get("origin", "")
    if origin and origin not in settings.cors_origins:
        await websocket.close(code=1008)
        return

    await manager.connect(websocket)
    try:
        while True:
            # Keep the connection alive; all updates are server-initiated pushes.
            # Discard any client pings without processing them.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
