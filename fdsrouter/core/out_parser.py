"""Parse FDS run-time output: the .out log, CHID_hrr.csv, and CHID_devc.csv.

Format verified against real FDS 6.11.1 runs (tests/fixtures/simple_test.out /
simple_test_hrr.csv for a single mesh, mm_test.out for two meshes), not guessed from memory.
Each poll re-reads the whole file rather than tracking a byte offset -- FDS .out/csv files for
typical engineering cases stay small (KB-low MB range), so this is simpler and avoids
partial-block bugs at chunk boundaries, at the cost of re-parsing text we've already seen every
~2s. That's a fine trade for a local single-job tool.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path

MAX_SERIES_POINTS = 1000  # plot resolution cap for a device history (see parse_devc_series)

# "       Time Step        1   September  3, 2026  15:16:59"
# "       Step Size:  0.103E+00 s, Total Time:    0.10306 s"
_STEP_BLOCK_RE = re.compile(
    r"Time Step\s+(\d+)\s+\S.*?\n"
    r"\s*Step Size:\s*([0-9.Ee+-]+)\s*s,\s*Total Time:\s*([0-9.Ee+-]+)\s*s",
)

# FDS convention for run-time diagnostics, e.g. " *** Warning: ..." / " *** Error: ...".
_WARNING_OR_ERROR_RE = re.compile(r"^\s*\**\s*(warning|error)\b", re.IGNORECASE | re.MULTILINE)

_COMPLETED_RE = re.compile(r"STOP:\s*FDS completed successfully", re.IGNORECASE)

# "Maximum CFL Number    :  0.37E+00 on Mesh 1 at (3,6,6)" -- present for both single- and
# multi-mesh runs (verified against both fixtures), so this one line is the whole feature:
# whichever mesh has the highest CFL number for a step is, by convention, the one currently
# constraining FDS's adaptive timestep the most.
_LIMITING_MESH_RE = re.compile(r"Maximum CFL Number\s*:\s*[0-9.Ee+-]+\s+on Mesh\s+(\d+)", re.IGNORECASE)


@dataclass
class DevcSeries:
    device: str
    unit: str
    samples: list[tuple[float, float]]  # (simulation time in s, value in `unit`)


@dataclass
class OutStatus:
    step_number: int | None
    step_size_s: float | None
    simulation_time_s: float | None
    warnings_count: int
    completed_successfully: bool
    limiting_mesh: int | None


def parse_out_text(out_text: str) -> OutStatus:
    step_number = step_size_s = simulation_time_s = None
    last_match = None
    for last_match in _STEP_BLOCK_RE.finditer(out_text):
        pass
    if last_match is not None:
        step_number = int(last_match.group(1))
        step_size_s = float(last_match.group(2))
        simulation_time_s = float(last_match.group(3))

    limiting_mesh = None
    last_mesh_match = None
    for last_mesh_match in _LIMITING_MESH_RE.finditer(out_text):
        pass
    if last_mesh_match is not None:
        limiting_mesh = int(last_mesh_match.group(1))

    warnings_count = len(_WARNING_OR_ERROR_RE.findall(out_text))
    completed_successfully = bool(_COMPLETED_RE.search(out_text))

    return OutStatus(
        step_number=step_number,
        step_size_s=step_size_s,
        simulation_time_s=simulation_time_s,
        warnings_count=warnings_count,
        completed_successfully=completed_successfully,
        limiting_mesh=limiting_mesh,
    )


def parse_out_file(out_path: Path) -> OutStatus | None:
    if not out_path.exists():
        return None
    return parse_out_text(out_path.read_text(encoding="utf-8", errors="replace"))


def _read_latest_csv_row(csv_path: Path) -> tuple[list[str], list[str]] | None:
    """FDS's DUMP-driven CSVs (hrr, devc, ...) share a format: a units row, a names row, then
    numeric data rows. Returns (column names, last complete data row) or None."""
    if not csv_path.exists():
        return None
    with csv_path.open(newline="", encoding="utf-8", errors="replace") as f:
        rows = list(csv.reader(f))
    if len(rows) < 3:
        return None
    header = [c.strip() for c in rows[1]]
    for row in reversed(rows[2:]):
        if len(row) >= len(header) and row:
            return header, row
    return None


def parse_latest_hrr_kw(hrr_csv_path: Path) -> float | None:
    """Return the most recent total HRR (kW) from CHID_hrr.csv, or None if unavailable."""
    result = _read_latest_csv_row(hrr_csv_path)
    if result is None:
        return None
    header, row = result
    try:
        hrr_index = header.index("HRR")
        return float(row[hrr_index])
    except (ValueError, IndexError):
        return None


def _read_devc_table(devc_csv_path: Path) -> tuple[list[str], list[str], list[list[str]]] | None:
    """Split CHID_devc.csv into (units row, names row, data rows), or None if unusable."""
    if not devc_csv_path.exists():
        return None
    with devc_csv_path.open(newline="", encoding="utf-8", errors="replace") as f:
        rows = list(csv.reader(f))
    if len(rows) < 3:
        return None
    return [c.strip() for c in rows[0]], [c.strip() for c in rows[1]], rows[2:]


def parse_devc_devices(devc_csv_path: Path) -> list[dict[str, str]]:
    """Every device in the file as {"name", "unit"}, in column order. Column 0 is always Time
    and is not a device."""
    table = _read_devc_table(devc_csv_path)
    if table is None:
        return []
    units, names, _ = table
    return [
        {"name": name, "unit": units[i] if i < len(units) else ""}
        for i, name in enumerate(names)
        if i > 0 and name
    ]


def parse_devc_series(
    devc_csv_path: Path, device: str, max_points: int = MAX_SERIES_POINTS
) -> DevcSeries | None:
    """The full (simulation time, value) history of one device, thinned to at most max_points.

    FDS writes one row per DT_DEVC output step, so a long case accumulates far more points than
    a ~600px wide plot can show; striding keeps the payload small, and the last sample is always
    kept so the curve ends at the most recent reading rather than short of it.
    """
    table = _read_devc_table(devc_csv_path)
    if table is None:
        return None
    units, names, data_rows = table
    if device not in names:
        return None
    column = names.index(device)
    if column == 0:  # the time column itself is not a device
        return None

    samples: list[tuple[float, float]] = []
    for row in data_rows:
        if len(row) <= column:
            continue
        try:
            samples.append((float(row[0]), float(row[column])))
        except ValueError:
            continue

    if len(samples) > max_points:
        stride = math.ceil(len(samples) / max_points)
        thinned = samples[::stride]
        if thinned[-1] != samples[-1]:
            thinned.append(samples[-1])
        samples = thinned

    return DevcSeries(
        device=device, unit=units[column] if column < len(units) else "", samples=samples
    )


def parse_devc_latest(devc_csv_path: Path) -> dict[str, float]:
    """Return the most recent reading of every device (thermocouple etc.) in CHID_devc.csv,
    keyed by device ID, or {} if unavailable -- devices are case-specific, so there's no
    fixed schema to fall back on."""
    result = _read_latest_csv_row(devc_csv_path)
    if result is None:
        return {}
    header, row = result
    values: dict[str, float] = {}
    for name, raw in zip(header[1:], row[1:]):  # column 0 is always Time
        try:
            values[name] = float(raw)
        except ValueError:
            continue
    return values
