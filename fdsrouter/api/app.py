from __future__ import annotations

import asyncio
import hmac
import logging
import platform
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fdsrouter.api import (
    routes_agent,
    routes_auth,
    routes_browse,
    routes_jobs,
    routes_nodes,
    routes_queue,
    routes_service,
    routes_settings,
    routes_upload,
)
from fdsrouter.api.routes_auth import SESSION_COOKIE
from fdsrouter.api.ws import ConnectionManager
from fdsrouter.config import Config
from fdsrouter.core import auth, external_jobs, system_monitor
from fdsrouter.core.queue_manager import QueueManager
from fdsrouter.db.database import Database

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "static"
# Paths that stay reachable without a session: logging in at all requires calling these first,
# and the frontend shell/assets have to load before it can even show a login screen.
_AUTH_EXEMPT_PREFIXES = ("/api/auth/",)


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

        if config.trusted_proxy_header and config.host not in ("127.0.0.1", "localhost", "::1"):
            logger.warning(
                "trusted_proxy_header is set to %r while the service listens on %s (not "
                "loopback-only) -- any client that can reach this port can impersonate any "
                "username by sending that header. Only enable trusted_proxy_header behind a "
                "reverse proxy that authenticates the request and strips client-supplied copies "
                "of the header.",
                config.trusted_proxy_header,
                config.host,
            )

        # A restart of the service kills the FDS child process; close out the row it left
        # behind before the dispatch loop starts looking at the queue.
        queue_manager.recover_stale_running_job()
        queue_manager.start()
        system_task = asyncio.create_task(
            system_monitor.poll_loop(config, system_state, ws_manager.broadcast, node_id)
        )
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
    app.include_router(routes_agent.router)
    app.include_router(routes_auth.router)
    app.include_router(routes_jobs.router)
    app.include_router(routes_nodes.router)
    app.include_router(routes_browse.router)
    app.include_router(routes_queue.router)
    app.include_router(routes_service.router)
    app.include_router(routes_settings.router)
    app.include_router(routes_upload.router)

    @app.middleware("http")
    async def auth_gate(request: Request, call_next):
        """Resolve request.state.user for every /api/* request; 401 on protected routes once
        accounts exist.

        One call-site for the whole app rather than a per-router `Depends` -- new routers pick
        this up automatically instead of needing to remember to opt in. /api/auth/* itself still
        gets request.state.user resolved (register() needs to know "am I already logged in?"
        even in non-bootstrap mode) -- it is exempt only from the 401 block below, not from
        session resolution.
        """
        path = request.url.path
        request.state.user = None

        if path.startswith("/api/agent/"):
            # A remote fdsrouter-agent process authenticates with the shared cluster_token, not
            # a browser session -- entirely separate from the account/session logic below, and
            # checked regardless of bootstrap mode (an agent's token requirement doesn't relax
            # just because no human account exists yet).
            token = request.app.state.config.cluster_token
            authorization = request.headers.get("authorization", "")
            presented = authorization[7:] if authorization.lower().startswith("bearer ") else ""
            if not token or not presented or not hmac.compare_digest(presented, token):
                return JSONResponse({"detail": "Ungueltiges oder fehlendes Cluster-Token"}, status_code=401)
            return await call_next(request)

        if not path.startswith("/api/"):
            return await call_next(request)

        db = request.app.state.db
        bootstrap = auth.is_bootstrap_mode(db)
        if not bootstrap:
            proxy_header = request.app.state.config.trusted_proxy_header
            if proxy_header and request.headers.get(proxy_header):
                request.state.user = auth.resolve_or_create_proxy_user(db, request.headers[proxy_header])
            else:
                token = request.cookies.get(SESSION_COOKIE)
                if token:
                    request.state.user = auth.resolve_session(db, token)

        if bootstrap or path.startswith(_AUTH_EXEMPT_PREFIXES) or request.state.user is not None:
            return await call_next(request)
        return JSONResponse({"detail": "Anmeldung erforderlich"}, status_code=401)

    @app.get("/job/{job_id}")
    def job_deep_link(job_id: str) -> FileResponse:
        # StaticFiles(html=True) only serves real files or a directory's own index.html -- it
        # cannot resolve an arbitrary client-side route like this one. Registered ahead of the
        # StaticFiles mount below so this explicit route wins for this exact path shape.
        return FileResponse(STATIC_DIR / "index.html")

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
