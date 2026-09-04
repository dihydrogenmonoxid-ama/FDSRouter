"""Upload a case from the operator's machine to the machine FDS runs on.

The file browser (routes_browse) picks files that already exist on the server; this is the other
direction, needed as soon as FDSRouter runs on a compute server and is operated from a
workstation over the network. The uploaded files land in their own directory, whose path is
returned so the client can enqueue the case through the normal job endpoint.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile

from fdsrouter.core.case_files import (
    CaseDirExistsError,
    create_case_dir,
    create_named_case_dir,
    safe_filename,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/upload", tags=["upload"])

CHUNK_SIZE = 1024 * 1024


@router.get("/target")
def upload_target(request: Request) -> dict:
    """Where uploads land by default -- shown in the dialog so the operator sees the full path."""
    return {"upload_dir": str(request.app.state.config.resolved_upload_dir)}


def _resolve_parent_dir(raw: str | None, default: Path) -> Path:
    """The directory the new case folder is created in: the browsed one, or the configured root.

    Any existing directory is allowed, exactly as in the server file browser -- FDSRouter runs
    on a trusted machine and cases legitimately live in project directories outside upload_dir.
    """
    if not raw or not raw.strip():
        default.mkdir(parents=True, exist_ok=True)
        return default

    parent = Path(raw).expanduser()
    try:
        parent = parent.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not parent.is_dir():
        raise HTTPException(status_code=400, detail=f"Verzeichnis nicht gefunden: {parent}")
    return parent


def _create_case_dir(parent: Path, folder_name: str | None, fds_filename: str) -> Path:
    if folder_name and folder_name.strip():
        try:
            return create_named_case_dir(parent, folder_name)
        except CaseDirExistsError as exc:
            raise HTTPException(status_code=409, detail=f"Ordner existiert bereits: {exc}") from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Ordner konnte nicht angelegt werden: {exc}") from exc
    try:
        return create_case_dir(parent, fds_filename)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Ordner konnte nicht angelegt werden: {exc}") from exc


@router.post("")
async def upload_case(
    request: Request,
    files: list[UploadFile],
    folder_name: str | None = Form(default=None),
    parent_dir: str | None = Form(default=None),
) -> dict:
    """Store an uploaded case and report where its .fds file ended up.

    A case is more than its .fds file -- ramps, geometry and include files are referenced by
    relative name -- so several files may be uploaded together into one directory. Exactly one
    of them must be the .fds input, since that is what gets enqueued.

    The directory created here is the working directory FDS runs in and writes its .out/.csv
    output to, and it is what "Ergebnisse" packages later. folder_name names it (otherwise a
    timestamped name is generated) and parent_dir says where it is created (otherwise the
    configured upload_dir).
    """
    config = request.app.state.config
    fds_names = [f for f in files if (f.filename or "").lower().endswith(".fds")]
    if len(fds_names) != 1:
        raise HTTPException(
            status_code=400,
            detail="Es muss genau eine .fds-Datei hochgeladen werden (weitere Falldateien optional).",
        )

    parent = _resolve_parent_dir(parent_dir, config.resolved_upload_dir)
    case_dir = _create_case_dir(parent, folder_name, fds_names[0].filename or "case.fds")

    max_bytes = config.max_upload_mb * 1024 * 1024
    written = 0
    fds_path: Path | None = None
    try:
        for upload in files:
            target = case_dir / safe_filename(upload.filename or "")
            with target.open("wb") as sink:
                while chunk := await upload.read(CHUNK_SIZE):
                    written += len(chunk)
                    if written > max_bytes:
                        # Checked while streaming so an oversized upload never lands on disk.
                        raise HTTPException(
                            status_code=413,
                            detail=f"Upload überschreitet das Limit von {config.max_upload_mb} MB.",
                        )
                    sink.write(chunk)
            if target.suffix.lower() == ".fds":
                fds_path = target
    except Exception:
        shutil.rmtree(case_dir, ignore_errors=True)  # never leave a half-written case behind
        raise

    logger.info("uploaded case to %s (%s files, %s bytes)", case_dir, len(files), written)
    return {"case_dir": str(case_dir), "fds_file_path": str(fds_path), "bytes": written}
