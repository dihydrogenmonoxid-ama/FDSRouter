"""Runtime estimation heuristic (CLAUDE.md section 7.3).

Compares the new case's mesh cell count against completed jobs on the same node whose cell
count is the same order of magnitude, using core-seconds-per-cell as the scaling quantity so
the estimate accounts for a different MPI process count. Falls back to a flat cells/core rate
when no comparable history exists yet -- an arbitrary starting constant, expected to be a poor
estimate until enough runs have completed to calibrate from history.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable

FALLBACK_SECONDS_PER_1000_CELLS_PER_CORE = 3.0
SIMILARITY_MIN_RATIO = 0.2
SIMILARITY_MAX_RATIO = 5.0


@dataclass
class Estimate:
    seconds: float | None
    basis: str  # "history" | "fallback" | "unknown"
    sample_size: int


def estimate_duration_s(
    cell_count: int | None,
    mpi_processes: int,
    history: Iterable[dict[str, Any]],
) -> Estimate:
    if not cell_count:
        return Estimate(seconds=None, basis="unknown", sample_size=0)

    mpi_processes = max(mpi_processes, 1)
    core_seconds_per_cell_samples: list[float] = []
    for job in history:
        hist_cells = job.get("mesh_cell_count")
        hist_duration = job.get("actual_duration_s")
        hist_processes = job.get("mpi_process_count") or 1
        if not hist_cells or not hist_duration:
            continue
        ratio = cell_count / hist_cells
        if not (SIMILARITY_MIN_RATIO <= ratio <= SIMILARITY_MAX_RATIO):
            continue
        core_seconds_per_cell_samples.append(hist_duration * hist_processes / hist_cells)

    if core_seconds_per_cell_samples:
        rate = median(core_seconds_per_cell_samples)
        seconds = rate * cell_count / mpi_processes
        return Estimate(seconds=seconds, basis="history", sample_size=len(core_seconds_per_cell_samples))

    seconds = FALLBACK_SECONDS_PER_1000_CELLS_PER_CORE * cell_count / 1000 / mpi_processes
    return Estimate(seconds=seconds, basis="fallback", sample_size=0)
