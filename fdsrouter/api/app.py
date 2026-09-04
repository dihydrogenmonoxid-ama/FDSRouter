from __future__ import annotations

import asyncio
import platform
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from fdsrouter.api import (
    routes_browse,
    routes_jobs,
    routes_nodes,
    routes_queue,
    routes_service,
    routes_settings,
    routes_upload,
)
from fdsrouter.api.ws import ConnectionManager
from fdsrouter.config import Config
from fdsrouter.core import external_jobs, system_monitor
from fdsrouter.core.queue_manager import QueueManager
from fdsrouter.db.database import Database

STATIC_DIR = Path(__file__).parent.parent / "static"


def _local_node_id(config: Config) -> str:
    """A stable id for the local node, persisted across restarts (data_dir/node_id.txt)."""
    id_file = config.resolved_data_dir / "node_id.txt"
    if id_file.exists():
        return id_file.read_text(encoding="utf-8").strip()
    node_id = uuid.uuid4().hex
    id_file.write_text(node_id, encoding="utf-8")
    return node_id


def create_app(config: Config) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(config.db_path)
        node_id = _local_node_id(config)
        db.upsert_node(
            node_id=node_id,
            hostname=platform.node(),
            os_name=platform.system(),
            # Physical cores, not logical/hyperthreaded ones: FDS's MPI-per-mesh model doesn't
            # benefit from SMT, so guidance (MPI process defaults, node status) should reflect
            # the core count that actually helps.
            cpu_cores=psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True) or 1,
            ram_total_mb=int(psutil.virtual_memory().total / (1024 * 1024)),
        )

        ws_manager = ConnectionManager()
        system_state = system_monitor.SystemState()
        queue_manager = QueueManager(config, db, node_id, ws_manager.broadcast, system_state)

        app.state.config = config
        app.state.db = db
        app.state.node_id = node_id
        app.state.ws_manager = ws_manager
        app.state.queue_manager = queue_manager
        app.state.system_state = system_state

        # A restart of the service kills the FDS child process; close out the row it left
        # behind before the dispatch loop starts looking at the queue.
        queue_manager.recover_stale_running_job()
        queue_manager.start()
        system_task = asyncio.create_task(system_monitor.poll_loop(config, system_state, ws_manager.broadcast))
        external_task = asyncio.create_task(
            external_jobs.poll_loop(config, queue_manager, ws_manager.broadcast)
        )
        try:
            yield
        finally:
            system_task.cancel()
            external_task.cancel()
            await queue_manager.stop()

    app = FastAPI(title="FDSRouter", lifespan=lifespan)
    app.include_router(routes_jobs.router)
    app.include_router(routes_nodes.router)
    app.include_router(routes_browse.router)
    app.include_router(routes_queue.router)
    app.include_router(routes_service.router)
    app.include_router(routes_settings.router)
    app.include_router(routes_upload.router)

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        manager: ConnectionManager = websocket.app.state.ws_manager
        await manager.connect(websocket)
        try:
            while True:
                await websocket.receive_text()  # client sends nothing meaningful; just keep-alive
        except WebSocketDisconnect:
            pass
        finally:
            await manager.disconnect(websocket)

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app
