"""Parse .fds input files for the metadata FDSRouter needs before/while running a case.

Reads &MESH IJK=... namelists for the mesh count/cell count used by the MPI-process default
and the time estimator, and &TIME T_END for progress display. MULT_ID-replicated meshes are
counted once (the replication factor lives in a separate &MULT namelist) -- a deliberate
simplification, not a full FDS input parser.

Namelists may span several lines, which is how most real case files are written. A namelist is
read from an ampersand at the start of a line up to the closing slash, ignoring slashes inside
quoted strings (file paths contain them). Requiring the ampersand to start the line is what
keeps a commented-out "! &MESH ..." from counting.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

_NAMELIST_START_RE = re.compile(r"^[ \t]*&([A-Za-z]+)\b", re.MULTILINE)
_IJK_RE = re.compile(r"\bIJK\s*=\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", re.IGNORECASE)
_T_END_RE = re.compile(r"\bT_END\s*=\s*([0-9.Ee+-]+)", re.IGNORECASE)


def iter_namelists(fds_text: str) -> Iterator[tuple[str, str]]:
    """Every namelist in the file as (NAME, body).

    Anything outside a namelist group is a comment as far as FDS is concerned, and that is
    exactly what this yields nothing for.
    """
    position = 0
    length = len(fds_text)
    while True:
        match = _NAMELIST_START_RE.search(fds_text, position)
        if match is None:
            return
        cursor = match.end()
        quote = None
        while cursor < length:
            char = fds_text[cursor]
            if quote is not None:
                if char == quote:
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == "/":
                break
            cursor += 1
        yield match.group(1).upper(), fds_text[match.end():cursor]
        position = cursor + 1


def _iter_mesh_cell_counts(fds_text: str) -> Iterator[int]:
    for name, body in iter_namelists(fds_text):
        if name != "MESH":
            continue
        ijk = _IJK_RE.search(body)
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
    for name, body in iter_namelists(fds_text):
        if name != "TIME":
            continue
        match = _T_END_RE.search(body)
        if match:
            return float(match.group(1))
    return None


def parse_sim_end_time_s_from_file(fds_path: Path) -> float | None:
    return parse_sim_end_time_s(fds_path.read_text(encoding="utf-8", errors="replace"))
