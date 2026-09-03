"""Queue-wide (not job-specific) controls: the auto-advance toggle."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/queue", tags=["queue"])


class AutoAdvanceRequest(BaseModel):
    enabled: bool


@router.get("/state")
def get_queue_state(request: Request) -> dict:
    return {"auto_advance": request.app.state.queue_manager.auto_advance}


@router.post("/auto-advance")
async def set_auto_advance(payload: AutoAdvanceRequest, request: Request) -> dict:
    await request.app.state.queue_manager.set_auto_advance(payload.enabled)
    return {"auto_advance": payload.enabled}
