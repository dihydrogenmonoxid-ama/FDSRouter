"""Restart, update and stop the FDSRouter service itself (settings dialog, section "Dienst").

These endpoints act on the systemd unit this process runs under. Stopping or restarting also
ends a running FDS simulation -- it is a child of the service -- so both refuse while a job is
running unless the caller explicitly confirms.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from fdsrouter.core import service_control

router = APIRouter(prefix="/api/service", tags=["service"])


class ServiceAction(BaseModel):
    # The UI asks for confirmation and repeats the call with force=True; the guard is what
    # keeps a stale browser tab from ending a twelve-hour run by accident.
    force: bool = False


def _guard_running_job(request: Request, payload: ServiceAction | None) -> None:
    if payload is not None and payload.force:
        return
    if request.app.state.queue_manager.current_job_id() is not None:
        raise HTTPException(status_code=409, detail="running_job")


@router.get("")
def get_service_status() -> dict:
    return service_control.status()


@router.post("/restart")
def post_restart(request: Request, payload: ServiceAction | None = None) -> dict:
    _guard_running_job(request, payload)
    try:
        service_control.restart()
    except service_control.ServiceControlError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/stop")
def post_stop(request: Request, payload: ServiceAction | None = None) -> dict:
    _guard_running_job(request, payload)
    try:
        service_control.stop()
    except service_control.ServiceControlError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/update")
def post_update(request: Request, payload: ServiceAction | None = None) -> dict:
    _guard_running_job(request, payload)
    try:
        return service_control.update()
    except service_control.ServiceControlError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
