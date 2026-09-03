"""Node registration/heartbeat. v1 only ever has the local node calling this itself, but the
endpoint shape is what a future remote agent process would call too (CLAUDE.md section 4)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel


router = APIRouter(prefix="/api/nodes", tags=["nodes"])


class NodeRegistration(BaseModel):
    id: str
    hostname: str
    os: str
    cpu_cores: int
    ram_total_mb: int


@router.get("")
def list_nodes(request: Request) -> list[dict]:
    return request.app.state.db.get_nodes()


@router.post("/register")
def register_node(payload: NodeRegistration, request: Request) -> dict:
    db = request.app.state.db
    db.upsert_node(payload.id, payload.hostname, payload.os, payload.cpu_cores, payload.ram_total_mb)
    return {"ok": True}


@router.post("/{node_id}/heartbeat")
def heartbeat(node_id: str, request: Request) -> dict:
    request.app.state.db.heartbeat_node(node_id)
    return {"ok": True}
