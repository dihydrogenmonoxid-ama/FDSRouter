"""Start and track a single FDS run as an OS process tree.

The MPI launcher (mpirun/mpiexec) is spawned as an asyncio subprocess so it never blocks the
FastAPI event loop; its actual FDS worker processes are discovered afterwards via psutil, since
mpirun forks them as children rather than exec-ing into fds directly.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import psutil

from fdsrouter.config import Config

logger = logging.getLogger(__name__)


@dataclass
class RunningJob:
    process: asyncio.subprocess.Process
    fds_file: Path
    case_dir: Path
    chid: str

    @property
    def out_path(self) -> Path:
        return self.case_dir / f"{self.chid}.out"

    @property
    def hrr_csv_path(self) -> Path:
        return self.case_dir / f"{self.chid}_hrr.csv"

    @property
    def devc_csv_path(self) -> Path:
        return self.case_dir / f"{self.chid}_devc.csv"

    def mpi_child_pids(self) -> list[int]:
        """Return PIDs of FDS worker processes spawned under the mpirun launcher."""
        try:
            root = psutil.Process(self.process.pid)
        except psutil.NoSuchProcess:
            return []
        try:
            return [p.pid for p in root.children(recursive=True)]
        except psutil.Error:
            return []


def extract_chid(fds_file: Path) -> str:
    text = fds_file.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("&HEAD"):
            for token in stripped.replace(",", " ").split():
                if token.upper().startswith("CHID="):
                    return token.split("=", 1)[1].strip("'\"")
    return fds_file.stem


async def start_job(config: Config, fds_file: Path, mpi_processes: int) -> RunningJob:
    if not config.fds_binary:
        raise RuntimeError("fds_binary ist in config.yaml nicht gesetzt")
    if not config.mpi_executable:
        raise RuntimeError("mpi_executable ist in config.yaml nicht gesetzt")

    chid = extract_chid(fds_file)
    case_dir = fds_file.parent

    args = [
        part.format(
            mpi_exec=config.mpi_executable,
            n_processes=mpi_processes,
            fds_binary=config.fds_binary,
            fds_file=fds_file.name,
        )
        for part in config.mpi_command_template
    ]

    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(case_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    logger.info("Started job process pid=%s chid=%s args=%s", process.pid, chid, args)
    return RunningJob(process=process, fds_file=fds_file, case_dir=case_dir, chid=chid)


async def stream_output(process: asyncio.subprocess.Process, on_line: Callable[[str], Awaitable[None]]) -> None:
    """Read the merged stdout/stderr of the mpirun/fds process line by line as it runs --
    this is the only place FDS's own startup/fatal errors show up when it fails before it
    can even open its CHID.out file (a malformed input, for instance)."""
    assert process.stdout is not None
    async for raw_line in process.stdout:
        await on_line(raw_line.decode("utf-8", errors="replace").rstrip("\n"))


async def terminate(process: asyncio.subprocess.Process, grace_period_s: float = 15.0) -> None:
    """Stop a running job's process tree: SIGTERM first (mpirun forwards it to the FDS
    workers and tears the MPI job down cleanly), escalating to SIGKILL if it hasn't exited
    within grace_period_s. Used by the "Job beenden" action -- unlike pause, this is not
    resumable."""
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_period_s)
    except asyncio.TimeoutError:
        logger.warning("pid=%s did not exit within %ss of SIGTERM, sending SIGKILL", process.pid, grace_period_s)
        process.kill()
        await process.wait()
