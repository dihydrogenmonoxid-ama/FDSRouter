"""Parse .fds input files for the metadata FDSRouter needs before/while running a case.

Reads &MESH IJK=... namelists for the mesh count/cell count used by the MPI-process default
and the time estimator, and &TIME T_END for progress display. MULT_ID-replicated meshes are
counted once (the replication factor lives in a separate &MULT namelist) -- a deliberate
simplification, not a full FDS input parser. Namelists are assumed single-line, matching every
case file this has been tested against.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

_MESH_LINE_RE = re.compile(r"^\s*&MESH\b(?P<body>.*?)/", re.IGNORECASE)
_IJK_RE = re.compile(r"\bIJK\s*=\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", re.IGNORECASE)
_T_END_RE = re.compile(r"\bT_END\s*=\s*([0-9.Ee+-]+)", re.IGNORECASE)


def _iter_mesh_cell_counts(fds_text: str) -> Iterator[int]:
    for line in fds_text.splitlines():
        match = _MESH_LINE_RE.match(line)
        if not match:
            continue
        ijk = _IJK_RE.search(match.group("body"))
        if not ijk:
            continue
        i, j, k = (int(v) for v in ijk.groups())
        yield i * j * k


def parse_mesh_cell_count(fds_text: str) -> int:
    """Sum I*J*K over every (non-commented) &MESH namelist in the given .fds source."""
    return sum(_iter_mesh_cell_counts(fds_text))


def parse_mesh_cell_count_from_file(fds_path: Path) -> int:
    return parse_mesh_cell_count(fds_path.read_text(encoding="utf-8", errors="replace"))


def parse_mesh_count(fds_text: str) -> int:
    """Number of &MESH namelists -- FDS maps one MPI process per mesh by convention and hard
    errors (#112) if the process count exceeds this, so it drives the MPI-process default."""
    return sum(1 for _ in _iter_mesh_cell_counts(fds_text))


def parse_mesh_count_from_file(fds_path: Path) -> int:
    return parse_mesh_count(fds_path.read_text(encoding="utf-8", errors="replace"))


def parse_sim_end_time_s(fds_text: str) -> float | None:
    """Read &TIME T_END=... -- used to show simulation progress (CLAUDE.md 7.5)."""
    match = _T_END_RE.search(fds_text)
    return float(match.group(1)) if match else None


def parse_sim_end_time_s_from_file(fds_path: Path) -> float | None:
    return parse_sim_end_time_s(fds_path.read_text(encoding="utf-8", errors="replace"))
