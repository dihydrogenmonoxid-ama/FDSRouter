"""Server-side directory browser.

The browser cannot hand the backend a real filesystem path for security reasons, but FDSRouter
needs one (to pass to mpirun/fds as the case's working directory). So the UI browses the
server's filesystem through this endpoint instead of a native <input type=file>. This is a
local single-user tool (CLAUDE.md section 9) bound to 127.0.0.1 by default -- there is no
narrower "allowed root" to enforce without contradicting "pick any .fds file on disk".
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/browse", tags=["browse"])


@router.get("")
def browse(path: str | None = Query(default=None)) -> dict:
    target = Path(path).expanduser() if path else Path.home()
    try:
        target = target.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="Verzeichnis nicht gefunden")

    entries = []
    try:
        children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        children = []

    for child in children:
        if child.name.startswith("."):
            continue
        is_dir = child.is_dir()
        if not is_dir and child.suffix.lower() != ".fds":
            continue
        entries.append({"name": child.name, "path": str(child), "is_dir": is_dir})

    return {
        "path": str(target),
        "parent": str(target.parent) if target.parent != target else None,
        "entries": entries,
    }
