"""The remote compute-node counterpart to the Controller.

Registers with a Controller over HTTP, polls it for an assigned job (see core/scheduler.py's
design rationale for why polling rather than the Controller pushing), downloads that job's case
files, runs FDS locally by reusing the exact same core/job_runner.py the Controller itself uses,
reports live metrics/log lines back, uploads the results, and reports the outcome.

A separate config/CLI entrypoint from the Controller (`fdsrouter start`) on purpose, not a mode
flag on the same one -- CLAUDE.md's "einfache Installation": one command per role, nothing to get
wrong about which one a given config.yaml belongs to.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psutil
import yaml

from fdsrouter.config import DEFAULT_MPI_COMMAND_TEMPLATE, auto_detect_binaries
from fdsrouter.core import case_files, job_runner, out_parser
from fdsrouter.core.job_runner import RunningJob

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "agent-config.yaml"
HEARTBEAT_INTERVAL_S = 10.0
ASSIGNMENT_POLL_INTERVAL_S = 2.0
METRICS_INTERVAL_S = 2.0  # matches core/monitor.py's local POLL_INTERVAL_S
LOG_DRAIN_TIMEOUT_S = 5.0
LOG_TAIL_LINES = 200


@dataclass
class AgentConfig:
    project_dir: Path
    controller_url: str = "http://127.0.0.1:8000"
    cluster_token: str = ""
    fds_binary: str | None = None
    mpi_executable: str | None = None
    mpi_command_template: list[str] = field(default_factory=lambda: list(DEFAULT_MPI_COMMAND_TEMPLATE))
    default_mpi_processes: int = 1
    data_dir: Path = field(default_factory=lambda: Path("./agent-data"))

    @property
    def resolved_data_dir(self) -> Path:
        return self.data_dir if self.data_dir.is_absolute() else self.project_dir / self.data_dir


def _write_default_config(path: Path) -> dict:
    fds_binary, mpi_executable = auto_detect_binaries()
    data = {
        "controller_url": "http://127.0.0.1:8000",
        "cluster_token": "",
        "fds_binary": fds_binary,
        "mpi_executable": mpi_executable,
        "mpi_command_template": list(DEFAULT_MPI_COMMAND_TEMPLATE),
        "default_mpi_processes": 1,
        "data_dir": "./agent-data",
    }
    with path.open("w", encoding="utf-8") as f:
        f.write(
            "# Auto-generated on this agent's first start.\n"
            "# Set controller_url to the Controller's address, and copy cluster_token from the\n"
            "# Controller's own config.yaml (same file, same key) so this agent is trusted.\n"
            "# fds_binary/mpi_executable were detected via PATH -- please check/adjust.\n"
        )
        yaml.safe_dump(data, f, sort_keys=False)
    return data


def load_agent_config(project_dir: Path) -> AgentConfig:
    project_dir = project_dir.resolve()
    config_path = project_dir / CONFIG_FILENAME

    if config_path.exists():
        with config_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = _write_default_config(config_path)

    cfg = AgentConfig(project_dir=project_dir)
    cfg.controller_url = raw.get("controller_url") or cfg.controller_url
    cfg.cluster_token = raw.get("cluster_token") or ""
    cfg.fds_binary = raw.get("fds_binary")
    cfg.mpi_executable = raw.get("mpi_executable")
    cfg.mpi_command_template = raw.get("mpi_command_template") or list(DEFAULT_MPI_COMMAND_TEMPLATE)
    cfg.default_mpi_processes = int(raw.get("default_mpi_processes", 1))
    cfg.data_dir = Path(raw.get("data_dir", "./agent-data"))

    cfg.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def _local_node_id(config: AgentConfig) -> str:
    """Same stable-id-persisted-to-a-file convention as the Controller's own _local_node_id."""
    id_file = config.resolved_data_dir / "node_id.txt"
    if id_file.exists():
        return id_file.read_text(encoding="utf-8").strip()
    node_id = uuid.uuid4().hex
    id_file.write_text(node_id, encoding="utf-8")
    return node_id


class Agent:
    def __init__(self, config: AgentConfig, transport: httpx.AsyncBaseTransport | None = None):
        # transport is exposed purely for tests -- an in-process httpx.ASGITransport driving a
        # real Controller app without a real socket. Real runs always use the default (None).
        self.config = config
        self.node_id = _local_node_id(config)
        self.client = httpx.AsyncClient(
            base_url=config.controller_url,
            headers={"Authorization": f"Bearer {config.cluster_token}"},
            timeout=30.0,
            transport=transport,
        )
        self._busy = False
        self._stop_requested = False

    async def aclose(self) -> None:
        await self.client.aclose()

    async def register(self) -> None:
        await self.client.post(
            "/api/agent/register",
            json={
                "id": self.node_id,
                "hostname": platform.node(),
                "os": platform.system(),
                # Physical cores, matching the Controller's own registration -- FDS's
                # MPI-per-mesh model doesn't benefit from hyperthreading.
                "cpu_cores": psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True) or 1,
                "ram_total_mb": int(psutil.virtual_memory().total / (1024 * 1024)),
            },
        )

    async def heartbeat_loop(self) -> None:
        while True:
            try:
                await self.client.post(f"/api/agent/{self.node_id}/heartbeat")
            except httpx.HTTPError:
                logger.warning("heartbeat to controller failed", exc_info=True)
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)

    async def assignment_poll_loop(self) -> None:
        while True:
            if not self._busy:
                await self._check_for_assignment()
            await asyncio.sleep(ASSIGNMENT_POLL_INTERVAL_S)

    async def _check_for_assignment(self) -> None:
        try:
            resp = await self.client.get(f"/api/agent/{self.node_id}/assignment")
            resp.raise_for_status()
            job = resp.json().get("job")
        except httpx.HTTPError:
            logger.warning("assignment poll failed", exc_info=True)
            return
        if job is None:
            return
        self._busy = True
        self._stop_requested = False
        try:
            await self._run_job(job)
        finally:
            self._busy = False

    def _work_dir(self, job_id: str) -> Path:
        return self.config.resolved_data_dir / "cases" / job_id

    async def _run_job(self, job: dict) -> None:
        job_id = job["id"]
        work_dir = self._work_dir(job_id)
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            await self._download_case_files(job_id, work_dir)
            fds_file = work_dir / Path(job["fds_file_path"]).name
            running = await job_runner.start_job(self.config, fds_file, job["mpi_process_count"])
            await self.client.post(f"/api/agent/jobs/{job_id}/start", json={"pid": running.process.pid})

            status, message = await self._monitor_until_done(job_id, running)

            await self._upload_results(job_id, work_dir)
            await self.client.post(
                f"/api/agent/jobs/{job_id}/finish",
                json={"status": status, "exit_message": message},
            )
        except Exception:
            logger.exception("job %s failed on this agent", job_id)
            try:
                await self.client.post(
                    f"/api/agent/jobs/{job_id}/finish",
                    json={"status": "failed", "exit_message": "Fehler im Agent -- siehe dessen Log"},
                )
            except httpx.HTTPError:
                pass
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    async def _download_case_files(self, job_id: str, work_dir: Path) -> None:
        case_zip = work_dir.with_name(work_dir.name + ".zip")
        async with self.client.stream("GET", f"/api/agent/jobs/{job_id}/case-files") as resp:
            resp.raise_for_status()
            with case_zip.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
        try:
            case_files.extract_zip(case_zip, work_dir)
        finally:
            case_zip.unlink(missing_ok=True)

    async def _upload_results(self, job_id: str, work_dir: Path) -> None:
        results_zip = work_dir.with_name(work_dir.name + "-results.zip")
        case_files.write_directory_zip(work_dir, results_zip)
        try:
            with results_zip.open("rb") as f:
                await self.client.post(
                    f"/api/agent/jobs/{job_id}/results",
                    files={"archive": (f"{job_id}.zip", f, "application/zip")},
                )
        finally:
            results_zip.unlink(missing_ok=True)

    async def _monitor_until_done(self, job_id: str, running: RunningJob) -> tuple[str, str | None]:
        recent_lines: list[str] = []

        async def on_line(line: str) -> None:
            recent_lines.append(line)
            if len(recent_lines) > LOG_TAIL_LINES:
                recent_lines.pop(0)
            try:
                await self.client.post(f"/api/agent/jobs/{job_id}/log", json={"lines": [line]})
            except httpx.HTTPError:
                logger.warning("log report failed for job %s", job_id, exc_info=True)

        log_task = asyncio.create_task(job_runner.stream_output(running.process, on_line))
        metrics_task = asyncio.create_task(self._metrics_loop(job_id, running))
        try:
            return_code = await running.process.wait()
        finally:
            metrics_task.cancel()
            try:
                await metrics_task
            except asyncio.CancelledError:
                pass
            try:
                await asyncio.wait_for(log_task, timeout=LOG_DRAIN_TIMEOUT_S)
            except asyncio.TimeoutError:
                log_task.cancel()

        return job_runner.determine_run_status(running.out_path, recent_lines, self._stop_requested, return_code)

    async def _metrics_loop(self, job_id: str, running: RunningJob) -> None:
        """Adapted from core/monitor.py's local poll_loop -- same sampling, reported over HTTP
        instead of written to a local db/broadcast. The response doubles as the stop-signal
        channel (see routes_agent.py's /metrics endpoint), so no separate polling is needed."""
        process_handles: dict[int, psutil.Process] = {}
        try:
            while True:
                await asyncio.sleep(METRICS_INTERVAL_S)

                process_stats = []
                for pid in running.mpi_child_pids():
                    try:
                        handle = process_handles.get(pid)
                        if handle is None:
                            handle = psutil.Process(pid)
                            handle.cpu_percent()
                            process_handles[pid] = handle
                        core = handle.cpu_num() if hasattr(handle, "cpu_num") else None
                        process_stats.append(
                            {"pid": pid, "cpu_percent": handle.cpu_percent(), "ram_percent": handle.memory_percent(), "core": core}
                        )
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        process_handles.pop(pid, None)

                out_status = out_parser.parse_out_file(running.out_path)
                out_payload = None
                devices: list[dict] = []
                if out_status is not None:
                    hrr_kw = out_parser.parse_latest_hrr_kw(running.hrr_csv_path)
                    devices = out_parser.parse_devc_latest(running.devc_csv_path)
                    out_payload = {
                        "step_number": out_status.step_number,
                        "step_size_s": out_status.step_size_s,
                        "simulation_time_s": out_status.simulation_time_s,
                        "total_hrr_kw": hrr_kw,
                        "warnings_count": out_status.warnings_count,
                        "limiting_mesh": out_status.limiting_mesh,
                    }

                ram = psutil.virtual_memory()
                try:
                    resp = await self.client.post(
                        f"/api/agent/jobs/{job_id}/metrics",
                        json={
                            "processes": process_stats,
                            "devices": devices,
                            "out": out_payload,
                            "cpu_percent_total": psutil.cpu_percent(),
                            "ram_percent": ram.percent,
                        },
                    )
                    if resp.json().get("stop") and not self._stop_requested:
                        self._stop_requested = True
                        await job_runner.terminate(running.process)
                except httpx.HTTPError:
                    logger.warning("metrics report failed for job %s", job_id, exc_info=True)
        except asyncio.CancelledError:
            raise


async def run(agent_config: AgentConfig) -> None:
    agent = Agent(agent_config)
    await agent.register()
    logger.info("agent registered as node %s against controller %s", agent.node_id, agent_config.controller_url)
    try:
        await asyncio.gather(agent.heartbeat_loop(), agent.assignment_poll_loop())
    finally:
        await agent.aclose()
