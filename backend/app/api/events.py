"""Server-Sent Events stream for panel realtime updates."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.core.audit import Actor, current_actor
from app.core.events import event_hub

router = APIRouter(prefix="/events", tags=["events"])

_HEARTBEAT_SECONDS = 15.0


@router.get("/stream")
async def event_stream(actor: Actor = Depends(current_actor)):
    """Authenticated SSE stream for portfolio balance notifications.

    Emits ``balance.updated`` (re-GET holdings) and ``balance.refresh_failed``
    (show the error; do not treat as a successful capture).
    """
    queue = event_hub.subscribe()

    async def generate():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                payload = json.dumps(event.as_sse_dict(), default=str)
                yield f"event: {event.type}\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            event_hub.unsubscribe(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
