"""Sequential single-node job dispatcher and queue reordering.

v1 keeps one global queue for the one local node (see CLAUDE.md sections 9/11); the dispatcher
just watches for an idle node and starts the lowest queue_position job. Reordering only ever
touches jobs still in status='queued' -- the running job is never included, which is how "the
active run stays pinned while everything after it can be reordered" (CLAUDE.md 7.2) is enforced.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import psutil

from fdsrouter.config import Config
from fdsrouter.core import energy, job_runner, notifications, out_parser
from fdsrouter.core.energy import EnergySettings, SETTINGS_KEYS as ENERGY_SETTINGS_KEYS
from fdsrouter.core.estimator import estimate_duration_s
from fdsrouter.core.fds_parser import (
    parse_mesh_cell_count_from_file,
    parse_mesh_count_from_file,
    parse_sim_end_time_s_from_file,
)
from fdsrouter.core.job_runner import RunningJob
from fdsrouter.core.monitor import poll_loop
from fdsrouter.core.system_monitor import SystemState
from fdsrouter.db.database import Database

logger = logging.getLogger(__name__)

DISPATCH_POLL_INTERVAL_S = 1.0

# Shown in the history for a run that a service restart cut short (UI texts are German).
STALE_RUNNING_MESSAGE = "Durch einen Neustart des Dienstes beendet."
LOG_TAIL_LINES = 200  # kept in memory per run, for a short excerpt in a failure message
LOG_DRAIN_TIMEOUT_S = 5.0  # grace period to flush the last stdout lines after process exit
ENERGY_POLL_INTERVAL_S = 10.0  # coarser than the 2s job poll -- energy doesn't need that resolution

Broadcast = Callable[[dict], Awaitable[None]]


class QueueManager:
    def __init__(self, config: Config, db: Database, node_id: str, broadcast: Broadcast, system_state: SystemState):
        self.config = config
        self.db = db
        self.node_id = node_id
        self.broadcast = broadcast
        self.system_state = system_state
        self._running: RunningJob | None = None
        self._running_job_id: str | None = None
        # _start_job awaits the subprocess spawn before _running is set, so a plain
        # "is _running None?" check is not enough to keep the dispatch loop and a manual
        # start from launching two jobs at once -- this flag closes that window.
        self._starting = False
        self._dispatch_task: asyncio.Task | None = None
        self._monitor_task: asyncio.Task | None = None
        self._stopping = False
        self._stop_requested = False
        # Off by default: the first job -- and, unless this is switched on, every job after
        # it -- waits for an explicit "Start" rather than the queue advancing on its own.
        self._auto_advance = False

    def start(self) -> None:
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())

    def recover_stale_running_job(self) -> None:
        """Close out a job left in status='running' by a restart of the service.

        FDS runs as a child of the service, so stopping or restarting it takes the simulation
        down while the row stays 'running' and blocks the history and the queue view. If the
        recorded process is really gone, the run is recorded as failed; if it somehow survived,
        the row is left alone and stays truthful.
        """
        job = self.db.get_running_job(self.node_id)
        if job is None:
            return
        pid = job.get("pid")
        if pid and psutil.pid_exists(int(pid)):
            return
        logger.info("job %s was still marked running at startup -- recording it as failed", job["id"])
        self.db.finish_job(job["id"], "failed", exit_message=STALE_RUNNING_MESSAGE)

    @property
    def auto_advance(self) -> bool:
        return self._auto_advance

    async def set_auto_advance(self, enabled: bool) -> None:
        self._auto_advance = enabled
        await self._broadcast_queue()

    def owned_pids(self) -> set[int]:
        """PIDs FDSRouter itself started -- used to exclude our own managed job from the
        external-process discovery scan (external_jobs.py)."""
        if self._running is None:
            return set()
        return {self._running.process.pid, *self._running.mpi_child_pids()}

    async def stop(self) -> None:
        self._stopping = True
        if self._dispatch_task:
            self._dispatch_task.cancel()
        if self._monitor_task:
            self._monitor_task.cancel()
        # A running FDS process is intentionally left alive on shutdown -- killing a
        # long simulation just because the UI/service restarted would be worse than
        # briefly losing live monitoring of it.

    def current_job_id(self) -> str | None:
        return self._running_job_id

    @property
    def _busy(self) -> bool:
        return self._running is not None or self._starting

    async def enqueue(
        self,
        *,
        name: str,
        fds_file_path: Path,
        mpi_processes: int | None = None,
        actor: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        cell_count = parse_mesh_cell_count_from_file(fds_file_path)
        mesh_count = parse_mesh_count_from_file(fds_file_path)
        sim_end_time_s = parse_sim_end_time_s_from_file(fds_file_path)

        # FDS maps one MPI process per &MESH by convention and hard-errors (#112) if the
        # process count exceeds the mesh count -- so default to it, and reject an explicit
        # request that would exceed it rather than letting the job fail inside FDS.
        resolved_processes = mpi_processes or mesh_count or self.config.default_mpi_processes
        if mesh_count and resolved_processes > mesh_count:
            raise ValueError(
                f"Die Anzahl der MPI-Prozesse ({resolved_processes}) darf die Anzahl der "
                f"Meshes in der Datei ({mesh_count}) nicht überschreiten."
            )

        history = self.db.get_completed_jobs_for_node(self.node_id)
        estimate = estimate_duration_s(cell_count, resolved_processes, history)
        job = self.db.create_job(
            name=name,
            fds_file_path=str(fds_file_path),
            node_id=self.node_id,
            mesh_cell_count=cell_count or None,
            sim_end_time_s=sim_end_time_s,
            mpi_process_count=resolved_processes,
            estimated_duration_s=estimate.seconds,
            created_by=actor,
            project=project,
        )
        self.db.insert_audit_entry(actor, "job_create", job_id=job["id"], detail=name)
        await self._broadcast_queue()
        return job

    async def archive_finished(self, actor: str | None = None) -> int:
        """Move finished runs out of the history view and tell every client. Queued and
        running jobs are untouched, so this is safe to do while a simulation is going."""
        count = self.db.archive_finished_jobs()
        if count:
            self.db.insert_audit_entry(actor, "job_archive", detail=f"{count} Laeufe")
            await self._broadcast_queue()
        return count

    async def reorder(self, ordered_job_ids: list[str], actor: str | None = None) -> None:
        self.db.reorder_queue(self.node_id, ordered_job_ids)
        self.db.insert_audit_entry(actor, "queue_reorder")
        await self._broadcast_queue()

    async def cancel(self, job_id: str, actor: str | None = None) -> bool:
        ok = self.db.cancel_queued_job(job_id)
        if ok:
            self.db.insert_audit_entry(actor, "job_cancel", job_id=job_id)
            await self._broadcast_queue()
        return ok

    async def stop_running_job(self, job_id: str, actor: str | None = None) -> bool:
        """Permanently end the running job (SIGTERM/SIGKILL)."""
        if self._running is None or self._running_job_id != job_id:
            return False
        self._stop_requested = True
        self.db.insert_audit_entry(actor, "job_stop", job_id=job_id)
        asyncio.create_task(job_runner.terminate(self._running.process))
        return True

    async def start_job_manually(self, job_id: str) -> bool:
        """Explicit "Start" action -- required for the first job, and for every job after it
        unless auto_advance is on."""
        if self._busy:
            return False
        job = self.db.get_job(job_id)
        if job is None or job["status"] != "queued":
            return False
        await self._start_job(job)
        return True

    def _log_path(self, job_id: str) -> Path:
        log_dir = self.config.resolved_data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / f"{job_id}.log"

    async def _dispatch_loop(self) -> None:
        while not self._stopping:
            if not self._busy and self._auto_advance:
                next_job = self.db.get_next_queued_job(self.node_id)
                if next_job is not None:
                    await self._start_job(next_job)
            await asyncio.sleep(DISPATCH_POLL_INTERVAL_S)

    async def _start_job(self, job: dict[str, Any]) -> None:
        fds_file = Path(job["fds_file_path"])
        self._starting = True
        try:
            running = await job_runner.start_job(self.config, fds_file, job["mpi_process_count"])
        except Exception as exc:  # bad config/binary/path -- fail the job, keep the queue moving
            logger.exception("failed to start job %s", job["id"])
            self.db.finish_job(job["id"], "failed", exit_message=str(exc))
            await self._broadcast_queue()
            return
        finally:
            self._starting = False

        self._running = running
        self._running_job_id = job["id"]
        self._stop_requested = False
        self.db.start_job(job["id"], running.process.pid)
        await self._broadcast_queue()
        self._monitor_task = asyncio.create_task(self._run_and_await(job["id"], running))

    def _energy_settings(self) -> EnergySettings:
        return EnergySettings.from_settings_dict(self.db.get_settings(ENERGY_SETTINGS_KEYS))

    async def _energy_poll_loop(self, job_id: str, accumulator: list[float]) -> None:
        """Integrates power (W) from Home Assistant into energy (kWh) for this job's whole
        run. Re-reads settings each tick so a mid-run config change takes effect immediately.
        A silently unconfigured/unreachable HA just means no energy figure -- never fatal."""
        last_sample_at: datetime | None = None
        try:
            while True:
                await asyncio.sleep(ENERGY_POLL_INTERVAL_S)
                settings = self._energy_settings()
                watts = await energy.read_power_watts(settings)
                now = datetime.now(timezone.utc)
                if watts is not None and last_sample_at is not None:
                    hours = (now - last_sample_at).total_seconds() / 3600
                    accumulator[0] += (watts / 1000) * hours
                    cost = energy.energy_cost(accumulator[0], settings)
                    await self.broadcast(
                        {
                            "type": "energy_update",
                            "job_id": job_id,
                            "energy_kwh": accumulator[0],
                            "energy_cost_eur": cost,
                            "solar_powered": settings.solar_powered,
                        }
                    )
                if watts is not None:
                    last_sample_at = now
        except asyncio.CancelledError:
            raise

    async def _run_and_await(self, job_id: str, running: RunningJob) -> None:
        log_path = self._log_path(job_id)
        recent_lines: list[str] = []
        energy_kwh_accumulator = [0.0]

        async def on_line(line: str) -> None:
            recent_lines.append(line)
            if len(recent_lines) > LOG_TAIL_LINES:
                recent_lines.pop(0)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            await self.broadcast({"type": "log_line", "job_id": job_id, "line": line})

        poll_task = asyncio.create_task(
            poll_loop(self.config, self.db, job_id, running, self.broadcast, self.system_state)
        )
        log_task = asyncio.create_task(job_runner.stream_output(running.process, on_line))
        energy_task = asyncio.create_task(self._energy_poll_loop(job_id, energy_kwh_accumulator))
        try:
            return_code = await running.process.wait()
        finally:
            poll_task.cancel()
            energy_task.cancel()
            for task in (poll_task, energy_task):
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            # The process has exited, but its stdout pipe may still have a little buffered
            # output to drain -- often exactly where FDS prints a fatal startup error -- so
            # let stream_output finish on its own EOF rather than cutting it off immediately.
            try:
                await asyncio.wait_for(log_task, timeout=LOG_DRAIN_TIMEOUT_S)
            except asyncio.TimeoutError:
                log_task.cancel()

        if self._stop_requested:
            status, message = "cancelled", "durch Nutzer beendet"
        else:
            # A 0 exit code alone is not trustworthy: mpirun/prterun can still return 0 even
            # when FDS itself reports "improperly set-up" (verified against a real bad-input
            # run) -- the .out file's own completion marker plus a console-log ERROR check are
            # the actual signal. No .out file at all (FDS never got that far) also means failure.
            out_status = out_parser.parse_out_file(running.out_path)
            log_has_error = any("error" in line.lower() for line in recent_lines)
            if out_status is not None and out_status.completed_successfully and not log_has_error:
                status, message = "done", None
            else:
                status = "failed"
                tail = "\n".join(recent_lines[-5:]).strip()
                message = tail if tail else f"exit code {return_code}"

        energy_kwh = energy_kwh_accumulator[0] or None
        cost = energy.energy_cost(energy_kwh, self._energy_settings()) if energy_kwh else None
        self.db.finish_job(job_id, status, exit_message=message, energy_kwh=energy_kwh, energy_cost_eur=cost)
        # actor=None: a run reaching a terminal state on its own isn't a user action, unlike the
        # explicit job_cancel/job_stop entries above.
        self.db.insert_audit_entry(None, "job_finish", job_id=job_id, detail=status)
        self._running = None
        self._running_job_id = None
        await self._broadcast_queue()
        # Fire-and-forget: a slow webhook or unreachable SMTP server must never delay the queue
        # from advancing to the next job.
        asyncio.create_task(self._notify(job_id, status))

    async def _notify(self, job_id: str, status: str) -> None:
        settings = notifications.NotificationSettings.from_settings_dict(
            self.db.get_settings(notifications.SETTINGS_KEYS)
        )
        if status not in settings.events:
            return
        job = self.db.get_job(job_id)
        if job is None:
            return
        await notifications.send_webhook(settings, job)
        await asyncio.to_thread(notifications.send_email, settings, job)

    async def _broadcast_queue(self) -> None:
        await self.broadcast(
            {"type": "queue_update", "jobs": self.db.get_jobs(), "auto_advance": self._auto_advance}
        )
