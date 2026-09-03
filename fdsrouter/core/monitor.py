"""Per-process + .out-file polling for the currently running job.

Runs as an asyncio background task for the lifetime of a job, sampling every POLL_INTERVAL_S
and broadcasting each sample over the WebSocket manager as well as persisting it, so a client
that connects mid-run immediately sees history via the REST job-metrics endpoints.

System-wide CPU/RAM/temperature are NOT sampled here -- that happens once, continuously, in
system_monitor.py (independent of any job); this reads its latest snapshot (via SystemState)
for the run_metric_sample row instead of taking a second, conflicting psutil.cpu_percent() call.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import psutil

from fdsrouter.config import Config
from fdsrouter.core import out_parser
from fdsrouter.core.job_runner import RunningJob
from fdsrouter.core.system_monitor import SystemState
from fdsrouter.db.database import Database

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 2.0

Broadcast = Callable[[dict], Awaitable[None]]


async def poll_loop(
    config: Config,
    db: Database,
    job_id: str,
    running: RunningJob,
    broadcast: Broadcast,
    system_state: SystemState,
) -> None:
    process_handles: dict[int, psutil.Process] = {}

    try:
        while True:
            await asyncio.sleep(POLL_INTERVAL_S)

            system = system_state.latest or {}
            process_stats = []
            for pid in running.mpi_child_pids():
                # An MPI worker can exit between mpi_child_pids() and the sampling below, so
                # both the handle creation and every read have to tolerate a vanished process
                # -- otherwise one exiting worker would end live monitoring for the whole run.
                try:
                    handle = process_handles.get(pid)
                    if handle is None:
                        handle = psutil.Process(pid)
                        handle.cpu_percent()  # prime
                        process_handles[pid] = handle
                    # cpu_num() only exists on Linux (verified: not present on macOS) -- the
                    # field is simply omitted there rather than faking a value.
                    core = handle.cpu_num() if hasattr(handle, "cpu_num") else None
                    process_stats.append(
                        {
                            "pid": pid,
                            "cpu_percent": handle.cpu_percent(),
                            "ram_percent": handle.memory_percent(),
                            "core": core,
                        }
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    process_handles.pop(pid, None)

            db.insert_run_metric_sample(
                job_id=job_id,
                cpu_percent_total=system.get("cpu_percent_total"),
                cpu_percent_per_core=system.get("cpu_percent_per_core"),
                ram_percent=system.get("ram_percent"),
                temperature_c=system.get("cpu_temperature_c"),
                per_process_stats=process_stats,
            )

            out_status = out_parser.parse_out_file(running.out_path)
            hrr_kw = out_parser.parse_latest_hrr_kw(running.hrr_csv_path)
            devices = out_parser.parse_devc_latest(running.devc_csv_path)
            out_payload = None
            if out_status is not None:
                db.insert_out_file_metric(
                    job_id=job_id,
                    simulation_time_s=out_status.simulation_time_s,
                    walltime_s=None,
                    step_size_s=out_status.step_size_s,
                    total_hrr_kw=hrr_kw,
                    step_number=out_status.step_number,
                    warnings_count=out_status.warnings_count,
                )
                out_payload = {
                    "step_number": out_status.step_number,
                    "step_size_s": out_status.step_size_s,
                    "simulation_time_s": out_status.simulation_time_s,
                    "total_hrr_kw": hrr_kw,
                    "warnings_count": out_status.warnings_count,
                    "limiting_mesh": out_status.limiting_mesh,
                }

            await broadcast(
                {
                    "type": "job_metrics",
                    "job_id": job_id,
                    "processes": process_stats,
                    "devices": devices,
                    "out": out_payload,
                }
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("monitor poll loop failed for job %s", job_id)
