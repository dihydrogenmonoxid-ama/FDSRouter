"""Node registration/heartbeat. v1 only ever has the local node calling this itself, but the
endpoint shape is what a future remote agent process would call too (CLAUDE.md section 4)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel

from fdsrouter.core import scheduler

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


class NodeRegistration(BaseModel):
    id: str
    hostname: str
    os: str
    cpu_cores: int
    ram_total_mb: int
    # Whether this node's own fds_binary/mpi_executable are both configured -- a node can be
    # online but not ready to actually run anything (e.g. a Controller with no local FDS).
    fds_ready: bool = False


@router.get("")
def list_nodes(request: Request) -> list[dict]:
    # node.status in the DB is write-only-to-'online' (see database.py) -- whether a node is
    # actually still alive is a read-time freshness check against last_heartbeat instead, the
    # same one the scheduler itself uses to decide eligibility.
    now = datetime.now(timezone.utc)
    nodes = request.app.state.db.get_nodes()
    for node in nodes:
        node["online"] = scheduler.is_node_online(node, now)
        node["fds_ready"] = bool(node["fds_ready"])  # SQLite gives back 0/1, not a JSON bool
    return nodes


@router.post("/register")
def register_node(payload: NodeRegistration, request: Request) -> dict:
    db = request.app.state.db
    db.upsert_node(
        payload.id, payload.hostname, payload.os, payload.cpu_cores, payload.ram_total_mb, payload.fds_ready
    )
    return {"ok": True}


@router.post("/{node_id}/heartbeat")
def heartbeat(node_id: str, request: Request) -> dict:
    request.app.state.db.heartbeat_node(node_id)
    return {"ok": True}
