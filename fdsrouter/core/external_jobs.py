"""Read-only discovery of FDS processes running outside FDSRouter's own management.

This exists because a control-plane restart -- or someone starting FDS by hand, or from a
different tool -- shouldn't leave a real, currently-running simulation invisible to the UI
just because FDSRouter's own database has no row for it. It ONLY inspects processes via
psutil (name/cwd/cmdline/create_time) and reads their existing .out/_hrr.csv files; it never
sends a signal to a process it didn't start, so it cannot affect a run it doesn't own.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

import psutil

from fdsrouter.config import Config
from fdsrouter.core import out_parser
from fdsrouter.core.job_runner import extract_chid
from fdsrouter.core.queue_manager import QueueManager

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 5.0

Broadcast = Callable[[dict], Awaitable[None]]


@dataclass
class ExternalJob:
    pid: int
    chid: str
    case_dir: str
    started_at: str
    simulation_time_s: float | None
    total_hrr_kw: float | None

    def to_dict(self) -> dict:
        return {
            "pid": self.pid,
            "chid": self.chid,
            "case_dir": self.case_dir,
            "started_at": self.started_at,
            "simulation_time_s": self.simulation_time_s,
            "total_hrr_kw": self.total_hrr_kw,
        }


def _looks_like_fds(name: str, exe: str | None, fds_binary: str | None) -> bool:
    if fds_binary and exe:
        try:
            if Path(exe).resolve() == Path(fds_binary).resolve():
                return True
        except OSError:
            pass
    return name.lower() == "fds"


def discover_external_jobs(fds_binary: str | None, exclude_pids: set[int]) -> list[ExternalJob]:
    """Read-only scan; never touches (signals/kills/pauses) anything it finds."""
    results: list[ExternalJob] = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cwd", "cmdline", "create_time"]):
        try:
            info = proc.info
            if info["pid"] in exclude_pids:
                continue
            if not _looks_like_fds(info.get("name") or "", info.get("exe"), fds_binary):
                continue
            cwd = info.get("cwd")
            cmdline = info.get("cmdline") or []
            if not cwd:
                continue

            fds_arg = next((a for a in cmdline if a.lower().endswith(".fds")), None)
            if not fds_arg:
                continue
            fds_path = Path(cwd) / fds_arg
            if not fds_path.is_file():
                continue

            chid = extract_chid(fds_path)
            case_dir = Path(cwd)
            out_status = out_parser.parse_out_file(case_dir / f"{chid}.out")
            hrr = out_parser.parse_latest_hrr_kw(case_dir / f"{chid}_hrr.csv")

            results.append(
                ExternalJob(
                    pid=info["pid"],
                    chid=chid,
                    case_dir=str(case_dir),
                    started_at=datetime.fromtimestamp(info["create_time"], tz=timezone.utc).isoformat(),
                    simulation_time_s=out_status.simulation_time_s if out_status else None,
                    total_hrr_kw=hrr,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return results


async def poll_loop(config: Config, queue_manager: QueueManager, broadcast: Broadcast) -> None:
    try:
        while True:
            await asyncio.sleep(POLL_INTERVAL_S)
            try:
                owned = queue_manager.owned_pids()
                jobs = discover_external_jobs(config.fds_binary, owned)
                await broadcast({"type": "external_jobs", "jobs": [j.to_dict() for j in jobs]})
            except Exception:
                logger.exception("external job discovery failed")
    except asyncio.CancelledError:
        raise
