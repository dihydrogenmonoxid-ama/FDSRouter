"""Check a .fds case before it is queued.

Every finding here corresponds to a way FDS aborts (or silently disappoints) seconds after the
job starts, which is the worst moment to find out: the queue has moved on, the operator has
left, and the only trace is a one-line error in the history. Checking at enqueue time turns
those into something correctable while the file is still in front of you.

Deliberately not a full FDS input parser. It reads namelists as FDS itself delimits them --
from `&NAME` to the next `/` -- and only asks questions whose answer is unambiguous from the
input file alone.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from fdsrouter.core.fds_parser import iter_namelists, parse_mesh_count

# Quoted string values, single or double quoted.
_QUOTED = r"'([^']*)'|\"([^\"]*)\""

_ID_RE = re.compile(r"\bID\s*=\s*(?:" + _QUOTED + r")", re.IGNORECASE)
_CHID_RE = re.compile(r"\bCHID\s*=\s*(?:" + _QUOTED + r")", re.IGNORECASE)
_SURF_REF_RE = re.compile(r"\bSURF_ID[0-9A-Z_()]*\s*=\s*(?:" + _QUOTED + r")", re.IGNORECASE)
_RAMP_REF_RE = re.compile(r"\bRAMP_[A-Z_]+\s*=\s*(?:" + _QUOTED + r")", re.IGNORECASE)
_FILE_REF_RE = re.compile(r"\b[A-Z_]*FILE[A-Z_()0-9]*\s*=\s*(?:" + _QUOTED + r")", re.IGNORECASE)
_HRRPUA_RE = re.compile(r"\b(HRRPUA|MLRPUA)\s*=", re.IGNORECASE)

# SURF_IDs FDS defines itself; referencing them is legal without a &SURF namelist.
_BUILTIN_SURFS = {"INERT", "OPEN", "MIRROR", "PERIODIC", "HVAC"}


@dataclass
class Finding:
    """level: error (FDS will refuse or the run is pointless), warning, info."""

    level: str
    code: str
    detail: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _values(pattern: re.Pattern, body: str) -> list[str]:
    return [(a or b) for a, b in pattern.findall(body)]


def _first_value(pattern: re.Pattern, body: str) -> str | None:
    values = _values(pattern, body)
    return values[0] if values else None


def check_case_text(text: str, case_dir: Path | None = None) -> list[Finding]:
    findings: list[Finding] = []
    namelists = list(iter_namelists(text))
    by_name: dict[str, list[str]] = {}
    for name, body in namelists:
        by_name.setdefault(name, []).append(body)

    # --- the case must have something to compute on --------------------------------------
    if parse_mesh_count(text) == 0:
        findings.append(Finding("error", "no_mesh"))

    # --- a fire needs a reaction: the single most common hard stop (ERROR 314) ------------
    if any(_HRRPUA_RE.search(body) for body in by_name.get("SURF", [])) and "REAC" not in by_name:
        findings.append(Finding("error", "hrrpua_without_reac"))

    # --- referenced surfaces and ramps must be defined in the file ------------------------
    defined_surfs = {v.upper() for body in by_name.get("SURF", []) for v in _values(_ID_RE, body)}
    referenced_surfs = {
        v.upper() for name, body in namelists if name != "SURF" for v in _values(_SURF_REF_RE, body)
    }
    for missing in sorted(referenced_surfs - defined_surfs - _BUILTIN_SURFS):
        findings.append(Finding("error", "missing_surf", missing))

    defined_ramps = {v.upper() for body in by_name.get("RAMP", []) for v in _values(_ID_RE, body)}
    referenced_ramps = {
        v.upper() for name, body in namelists if name != "RAMP" for v in _values(_RAMP_REF_RE, body)
    }
    for missing in sorted(referenced_ramps - defined_ramps):
        findings.append(Finding("error", "missing_ramp", missing))

    # --- external files are resolved relative to the working directory --------------------
    if case_dir is not None:
        for name, body in namelists:
            for referenced in _values(_FILE_REF_RE, body):
                if referenced and not (case_dir / referenced).exists():
                    findings.append(Finding("error", "missing_file", referenced))

    # --- things that do not stop FDS but make the run less useful -------------------------
    chid = None
    for body in by_name.get("HEAD", []):
        chid = _first_value(_CHID_RE, body) or chid
    if not chid:
        # Without a CHID, FDS writes "output_*" files and the CHID-prefixed result packaging
        # (and therefore the "Results" download) cannot tell this case's output apart.
        findings.append(Finding("warning", "no_chid"))

    if "TIME" not in by_name or not any("T_END" in body.upper() for body in by_name.get("TIME", [])):
        findings.append(Finding("warning", "no_t_end"))

    if "DUMP" not in by_name:
        findings.append(Finding("info", "no_dump"))

    return findings


def check_case_file(fds_path: Path) -> list[Finding]:
    text = fds_path.read_text(encoding="utf-8", errors="replace")
    return check_case_text(text, case_dir=fds_path.parent)
