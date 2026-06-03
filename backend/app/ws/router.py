"""WebSocket endpoint — clients subscribe here to receive live result updates."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.ws.manager import manager

router = APIRouter()


@router.websocket("/results")
async def ws_results(websocket: WebSocket):
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
