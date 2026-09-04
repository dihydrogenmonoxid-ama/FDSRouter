"""Everything a remote fdsrouter-agent process calls.

Authenticated by the shared cluster_token (see api/app.py's auth_gate), not a browser session --
a headless agent has no cookie. Kept under its own /api/agent prefix so that check stays a single
clean branch in the middleware rather than mixed into the browser-facing routes.

The agent polls (see core/scheduler.py's design rationale): it asks for an assignment, downloads
the case files, runs FDS locally, and reports back -- the Controller never calls out to it.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from fdsrouter.core.case_files import extract_zip, write_directory_zip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])


class AgentRegistration(BaseModel):
    id: str
    hostname: str
    os: str
    cpu_cores: int
    ram_total_mb: int


class StartPayload(BaseModel):
    pid: int | None = None


class MetricsPayload(BaseModel):
    processes: list[dict] = []
    devices: list[dict] = []
    out: dict | None = None
    cpu_percent_total: float | None = None
    cpu_percent_per_core: list[float] | None = None
    ram_percent: float | None = None
    temperature_c: float | None = None


class LogPayload(BaseModel):
    lines: list[str]


class FinishPayload(BaseModel):
    status: str
    exit_message: str | None = None
    energy_kwh: float | None = None
    energy_cost_eur: float | None = None


def _require_job(job_id: str, request: Request) -> dict:
    job = request.app.state.db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    return job


@router.post("/register")
def register(payload: AgentRegistration, request: Request) -> dict:
    request.app.state.db.upsert_node(
        payload.id, payload.hostname, payload.os, payload.cpu_cores, payload.ram_total_mb
    )
    return {"ok": True}


@router.post("/{node_id}/heartbeat")
def heartbeat(node_id: str, request: Request) -> dict:
    request.app.state.db.heartbeat_node(node_id)
    return {"ok": True}


@router.get("/{node_id}/assignment")
def assignment(node_id: str, request: Request) -> dict:
    """The job (if any) this node should start now.

    Mirrors the local dispatch loop's own gate: a job the scheduler has assigned here still
    doesn't start on its own unless auto_advance is on, or an operator explicitly clicked
    "Start" on it (QueueManager.start_job_manually sets start_requested_at for a job that isn't
    local). Without this check a remote node would start every assigned job immediately,
    silently bypassing the same "nothing runs without permission" guarantee local jobs have.
    """
    job = request.app.state.db.get_next_queued_job(node_id)
    if job is None:
        return {"job": None}
    queue_manager = request.app.state.queue_manager
    if not queue_manager.auto_advance and not job.get("start_requested_at"):
        return {"job": None}
    return {"job": job}


@router.get("/jobs/{job_id}/case-files")
def case_files_zip(job_id: str, request: Request):
    """The job's whole working directory, zipped -- everything the agent needs to actually run
    the case locally. See case_files.write_directory_zip for why this isn't filtered to
    CHID-prefixed files the way a post-run results download is."""
    job = _require_job(job_id, request)
    fds_file = Path(job["fds_file_path"])
    if not fds_file.is_dir() and not fds_file.parent.is_dir():
        raise HTTPException(status_code=404, detail="Arbeitsverzeichnis nicht gefunden")

    export_dir = request.app.state.config.resolved_data_dir / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    archive = export_dir / f"{job_id}-case.zip"
    write_directory_zip(fds_file.parent, archive)
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=f"{job_id}-case.zip",
        background=BackgroundTask(archive.unlink, missing_ok=True),
    )


@router.post("/jobs/{job_id}/start")
async def start(job_id: str, payload: StartPayload, request: Request) -> dict:
    _require_job(job_id, request)
    request.app.state.db.start_job(job_id, payload.pid or 0)
    await request.app.state.queue_manager.broadcast_queue()
    return {"ok": True}


@router.post("/jobs/{job_id}/metrics")
async def metrics(job_id: str, payload: MetricsPayload, request: Request) -> dict:
    job = _require_job(job_id, request)
    db = request.app.state.db

    db.insert_run_metric_sample(
        job_id=job_id,
        cpu_percent_total=payload.cpu_percent_total,
        cpu_percent_per_core=payload.cpu_percent_per_core,
        ram_percent=payload.ram_percent,
        temperature_c=payload.temperature_c,
        per_process_stats=payload.processes,
    )
    if payload.out is not None:
        out = payload.out
        db.insert_out_file_metric(
            job_id=job_id,
            simulation_time_s=out.get("simulation_time_s"),
            walltime_s=None,
            step_size_s=out.get("step_size_s"),
            total_hrr_kw=out.get("total_hrr_kw"),
            step_number=out.get("step_number"),
            warnings_count=out.get("warnings_count") or 0,
        )

    await request.app.state.ws_manager.broadcast(
        {
            "type": "job_metrics",
            "job_id": job_id,
            "node_id": job["node_id"],
            "processes": payload.processes,
            "devices": payload.devices,
            "out": payload.out,
        }
    )
    return {"stop": job.get("stop_requested_at") is not None}


@router.post("/jobs/{job_id}/log")
async def log(job_id: str, payload: LogPayload, request: Request) -> dict:
    _require_job(job_id, request)
    log_dir = request.app.state.config.resolved_data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{job_id}.log"
    with log_path.open("a", encoding="utf-8") as f:
        for line in payload.lines:
            f.write(line + "\n")
    for line in payload.lines:
        await request.app.state.ws_manager.broadcast({"type": "log_line", "job_id": job_id, "line": line})
    return {"ok": True}


@router.post("/jobs/{job_id}/results")
async def upload_results(job_id: str, request: Request, archive: UploadFile) -> dict:
    """Receive the finished case's files back from the agent, extracted into the same directory
    the job's fds_file_path already points at -- so every existing browser-facing endpoint
    (download, manifest, devices, metrics) keeps working unmodified regardless of where the job
    actually ran."""
    job = _require_job(job_id, request)
    target_dir = Path(job["fds_file_path"]).parent
    target_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        try:
            shutil.copyfileobj(archive.file, tmp)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
    try:
        written = extract_zip(tmp_path, target_dir)
    finally:
        tmp_path.unlink(missing_ok=True)
    return {"files": written}


@router.post("/jobs/{job_id}/finish")
async def finish(job_id: str, payload: FinishPayload, request: Request) -> dict:
    _require_job(job_id, request)
    await request.app.state.queue_manager.finalize_job(
        job_id, payload.status, payload.exit_message, payload.energy_kwh, payload.energy_cost_eur
    )
    return {"ok": True}
