from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from fdsrouter.core.case_check import check_case_file
from fdsrouter.core.fds_parser import (
    parse_mesh_cell_count_from_file,
    parse_mesh_count_from_file,
    parse_sim_end_time_s_from_file,
)
from fdsrouter.core.case_files import result_files, safe_filename, write_results_zip
from fdsrouter.core.job_runner import extract_chid
from fdsrouter.core.out_parser import parse_devc_devices, parse_devc_series

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    fds_file_path: str
    name: str | None = None
    mpi_processes: int | None = None
    project: str | None = None


class ReorderRequest(BaseModel):
    ordered_job_ids: list[str]


class JobUpdate(BaseModel):
    project: str | None = None
    notes: str | None = None


def _actor(request: Request) -> str | None:
    user = getattr(request.state, "user", None)
    return user["username"] if user else None


def _require_fds_file(path_str: str) -> Path:
    fds_file = Path(path_str).expanduser()
    if not fds_file.is_file() or fds_file.suffix.lower() != ".fds":
        raise HTTPException(status_code=400, detail="Pfad muss auf eine bestehende .fds-Datei zeigen")
    return fds_file


@router.get("")
def list_jobs(request: Request) -> list[dict]:
    return request.app.state.db.get_jobs()


@router.get("/inspect")
def inspect_fds_file(path: str, request: Request) -> dict:
    """Mesh/T_END preview plus a pre-flight check, before the job is actually enqueued.

    The findings are the point: they turn the FDS errors that would otherwise surface seconds
    into the run -- and hours later in the history -- into something correctable right here.
    """
    fds_file = _require_fds_file(path)
    config = request.app.state.config
    mesh_count = parse_mesh_count_from_file(fds_file)
    return {
        "mesh_count": mesh_count,
        "mesh_cell_count": parse_mesh_cell_count_from_file(fds_file) or None,
        "sim_end_time_s": parse_sim_end_time_s_from_file(fds_file),
        "default_mpi_processes": mesh_count or config.default_mpi_processes,
        "findings": [f.as_dict() for f in check_case_file(fds_file)],
    }


@router.get("/archived")
def list_archived_jobs(request: Request) -> list[dict]:
    return request.app.state.db.get_archived_jobs()


@router.post("/archive")
async def archive_jobs(request: Request) -> dict:
    """Archive every finished run at once -- the "Archivieren" action in the history panel."""
    return {"archived": await request.app.state.queue_manager.archive_finished(actor=_actor(request))}


@router.post("")
async def create_job(payload: JobCreate, request: Request) -> dict:
    fds_file = _require_fds_file(payload.fds_file_path)
    name = payload.name or fds_file.stem

    queue_manager = request.app.state.queue_manager
    try:
        job = await queue_manager.enqueue(
            name=name,
            fds_file_path=fds_file,
            mpi_processes=payload.mpi_processes,
            actor=_actor(request),
            project=payload.project,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return job


@router.patch("/reorder")
async def reorder_jobs(payload: ReorderRequest, request: Request) -> dict:
    try:
        await request.app.state.queue_manager.reorder(payload.ordered_job_ids, actor=_actor(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/{job_id}/start")
async def start_job(job_id: str, request: Request) -> dict:
    ok = await request.app.state.queue_manager.start_job_manually(job_id)
    if not ok:
        raise HTTPException(status_code=409, detail="Job ist nicht wartend oder ein anderer Job laeuft bereits")
    return {"ok": True}


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request) -> dict:
    ok = await request.app.state.queue_manager.cancel(job_id, actor=_actor(request))
    if not ok:
        raise HTTPException(status_code=409, detail="Job ist nicht wartend oder pausiert (laeuft bereits oder abgeschlossen)")
    return {"ok": True}


@router.post("/{job_id}/stop")
async def stop_job(job_id: str, request: Request) -> dict:
    ok = await request.app.state.queue_manager.stop_running_job(job_id, actor=_actor(request))
    if not ok:
        raise HTTPException(status_code=409, detail="Job laeuft aktuell nicht")
    return {"ok": True}


@router.get("/{job_id}")
def get_job(job_id: str, request: Request) -> dict:
    return _require_job(job_id, request)


@router.patch("/{job_id}")
def update_job(job_id: str, payload: JobUpdate, request: Request) -> dict:
    job = _require_job(job_id, request)
    db = request.app.state.db
    project = payload.project if payload.project is not None else job.get("project")
    notes = payload.notes if payload.notes is not None else job.get("notes")
    db.update_job(job_id, project=project, notes=notes)
    db.insert_audit_entry(_actor(request), "job_edit", job_id=job_id)
    return _require_job(job_id, request)


@router.get("/{job_id}/audit")
def get_job_audit(job_id: str, request: Request) -> dict:
    _require_job(job_id, request)
    return {"entries": request.app.state.db.get_audit_entries(job_id=job_id)}


def _devc_path_for_job(job: dict) -> Path | None:
    """Locate a job's CHID_devc.csv the same way the running job does: next to its .fds file,
    named after the case's CHID. Returns None if the input file is gone (moved/deleted)."""
    fds_file = Path(job["fds_file_path"])
    if not fds_file.is_file():
        return None
    return fds_file.parent / f"{extract_chid(fds_file)}_devc.csv"


def _require_job(job_id: str, request: Request) -> dict:
    job = request.app.state.db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    return job


@router.get("/{job_id}/devices")
def list_job_devices(job_id: str, request: Request) -> dict:
    """Devices FDS has written for this case so far. Empty until the first DEVC output step --
    that is a normal state, not an error."""
    devc_path = _devc_path_for_job(_require_job(job_id, request))
    return {"devices": parse_devc_devices(devc_path) if devc_path else []}


@router.get("/{job_id}/devices/series")
def get_job_device_series(job_id: str, device: str, request: Request) -> dict:
    """One device's full history, for plotting it in place of the HRR curve."""
    devc_path = _devc_path_for_job(_require_job(job_id, request))
    series = parse_devc_series(devc_path, device) if devc_path else None
    if series is None:
        raise HTTPException(status_code=404, detail="Messstelle nicht gefunden")
    return {"device": series.device, "unit": series.unit, "samples": series.samples}


@router.get("/{job_id}/results")
def download_job_results(job_id: str, request: Request):
    """Download the case's input and output files as one zip.

    This is the counterpart to uploading: with FDSRouter running on a compute server, the
    results have to come back to the workstation somehow. The archive is built in the data
    directory and deleted again once the response has been sent.
    """
    job = _require_job(job_id, request)
    fds_file = Path(job["fds_file_path"])
    if not fds_file.is_file():
        raise HTTPException(status_code=404, detail="Falldatei nicht mehr vorhanden")
    if not result_files(fds_file):
        raise HTTPException(status_code=404, detail="Keine Ergebnisdateien gefunden")

    export_dir = request.app.state.config.resolved_data_dir / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    archive = export_dir / f"{job_id}.zip"
    write_results_zip(fds_file, archive)

    # Job name and CHID are often the same word -- don't ship a "pan_check_pan_check.zip".
    stem = safe_filename(job["name"]) or "fdsrouter"
    chid = extract_chid(fds_file)
    download_name = f"{stem}.zip" if stem == chid else f"{stem}_{chid}.zip"
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=download_name,
        background=BackgroundTask(archive.unlink, missing_ok=True),
    )


@router.get("/{job_id}/results/manifest")
def list_job_results(job_id: str, request: Request) -> dict:
    """What the zip would contain -- lets the UI hide the download when there is nothing yet."""
    job = _require_job(job_id, request)
    files = result_files(Path(job["fds_file_path"]))
    return {
        "files": [{"name": f.name, "bytes": f.stat().st_size} for f in files],
        "total_bytes": sum(f.stat().st_size for f in files),
    }


@router.get("/{job_id}/metrics")
def get_job_metrics(job_id: str, request: Request) -> dict:
    _require_job(job_id, request)
    db = request.app.state.db
    return {
        "run_metric_samples": db.get_run_metric_samples(job_id),
        "out_file_metrics": db.get_out_file_metrics(job_id),
    }


MAX_LOG_CHARS = 200_000  # tail-cap so a very long run's console output stays cheap to serve


@router.get("/{job_id}/log")
def get_job_log(job_id: str, request: Request) -> dict:
    _require_job(job_id, request)
    log_path = request.app.state.config.resolved_data_dir / "logs" / f"{job_id}.log"
    if not log_path.exists():
        return {"log": ""}
    # Seek to the tail rather than reading the file: a long FDS run's console output can grow
    # to hundreds of MB, and only the last MAX_LOG_CHARS are ever returned anyway.
    with log_path.open("rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - MAX_LOG_CHARS))
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    return {"log": text}
