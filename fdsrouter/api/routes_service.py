"""Restart, update and stop the FDSRouter service itself (settings dialog, section "Dienst").

These endpoints act on the systemd unit this process runs under. Stopping or restarting also
ends a running FDS simulation -- it is a child of the service -- so both refuse while a job is
running unless the caller explicitly confirms.
"""

from __future__ import annotations

import platform

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from fdsrouter.config import LOOPBACK_HOSTS, save_config
from fdsrouter.core import service_control

router = APIRouter(prefix="/api/service", tags=["service"])


class ServiceAction(BaseModel):
    # The UI asks for confirmation and repeats the call with force=True; the guard is what
    # keeps a stale browser tab from ending a twelve-hour run by accident.
    force: bool = False


class ConfigUpdate(BaseModel):
    host: str | None = None
    port: int | None = None
    open_browser: bool | None = None
    fds_binary: str | None = None
    mpi_executable: str | None = None
    default_mpi_processes: int | None = None
    temperature_enabled: bool | None = None
    discovery_enabled: bool | None = None
    max_upload_mb: int | None = None


# host/port need a real process restart (uvicorn can't rebind); discovery_enabled needs one too,
# since the responder thread is only started once at lifespan startup. Everything else in
# ConfigUpdate is read fresh from app.state.config on each use (job start, upload, temperature
# poll), so mutating it here takes effect immediately with no restart at all.
_RESTART_REQUIRED_FIELDS = {"host", "port", "discovery_enabled"}
# role, controller_url, data_dir, upload_dir, mpi_command_template, trusted_proxy_header and
# cluster_token are deliberately not editable here: the first two would let a running Controller
# turn itself into an agent (or vice versa) via a form field, the next two would relocate
# existing data/results out from under the running install, and the rest are either rare enough
# to not warrant a form (mpi_command_template) or security-sensitive install-time decisions that
# must stay a conscious file edit (trusted_proxy_header) or have their own dedicated flow
# (cluster_token, via Betrieb -> Cluster).


def _guard_running_job(request: Request, payload: ServiceAction | None) -> None:
    if payload is not None and payload.force:
        return
    if request.app.state.queue_manager.current_job_id() is not None:
        raise HTTPException(status_code=409, detail="running_job")


def _actor(request: Request) -> str | None:
    user = getattr(request.state, "user", None)
    return user["username"] if user else None


@router.get("")
def get_service_status() -> dict:
    return service_control.status()


@router.get("/cluster-info")
def get_cluster_info(request: Request) -> dict:
    """What another machine's `fdsrouter agent` setup needs to pair with this Controller --
    shown in the Operations dialog so the operator doesn't have to grep config.yaml by hand.
    Session-authenticated like the rest of /api/service, since cluster_token is a secret."""
    config = request.app.state.config
    lan_reachable = config.host not in LOOPBACK_HOSTS
    return {
        "hostname": platform.node(),
        "port": config.port,
        "cluster_token": config.cluster_token,
        "lan_reachable": lan_reachable,
        # What actually determines whether an agent's discovery broadcast gets an answer --
        # discovery_enabled alone isn't enough, see app.py's lifespan.
        "discovery_active": config.discovery_enabled and lan_reachable,
    }


@router.get("/config")
def get_editable_config(request: Request) -> dict:
    """The subset of config.yaml the UI is allowed to edit -- see ConfigUpdate's comment for
    why the rest (role, controller_url, data_dir, ...) stays file-only."""
    config = request.app.state.config
    return {
        "host": config.host,
        "port": config.port,
        "open_browser": config.open_browser,
        "fds_binary": config.fds_binary,
        "mpi_executable": config.mpi_executable,
        "default_mpi_processes": config.default_mpi_processes,
        "temperature_enabled": config.temperature_enabled,
        "discovery_enabled": config.discovery_enabled,
        "max_upload_mb": config.max_upload_mb,
    }


@router.put("/config")
def put_editable_config(payload: ConfigUpdate, request: Request) -> dict:
    config = request.app.state.config
    changed = set()
    for field_name in payload.model_fields_set:
        value = getattr(payload, field_name)
        if value != getattr(config, field_name):
            setattr(config, field_name, value)
            changed.add(field_name)

    if changed:
        save_config(config)
        request.app.state.db.insert_audit_entry(_actor(request), "config_update", detail=",".join(sorted(changed)))

    return {"ok": True, "restart_required": bool(changed & _RESTART_REQUIRED_FIELDS)}


@router.post("/restart")
def post_restart(request: Request, payload: ServiceAction | None = None) -> dict:
    _guard_running_job(request, payload)
    try:
        service_control.restart()
    except service_control.ServiceControlError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    request.app.state.db.insert_audit_entry(_actor(request), "service_restart")
    return {"ok": True}


@router.post("/stop")
def post_stop(request: Request, payload: ServiceAction | None = None) -> dict:
    _guard_running_job(request, payload)
    try:
        service_control.stop()
    except service_control.ServiceControlError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    request.app.state.db.insert_audit_entry(_actor(request), "service_stop")
    return {"ok": True}


@router.post("/update")
def post_update(request: Request, payload: ServiceAction | None = None) -> dict:
    _guard_running_job(request, payload)
    try:
        result = service_control.update()
    except service_control.ServiceControlError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    request.app.state.db.insert_audit_entry(_actor(request), "service_update")
    return result
