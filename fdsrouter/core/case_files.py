"""Cases uploaded through the web interface, and packaging a finished case's results.

Uploading exists because the browser cannot hand the backend a path into the operator's own
machine: to run a case that lives on a workstation, its files have to be copied to the machine
FDS actually runs on. Each upload therefore gets its own directory under the configured
upload_dir, which then serves as the working directory FDS writes into.
"""

from __future__ import annotations

import re
import zipfile
from datetime import datetime
from pathlib import Path

from fdsrouter.core.job_runner import extract_chid

# Anything outside this set is replaced -- a client-supplied name must not be able to introduce
# path separators, shell metacharacters or leading dots.
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_FALLBACK_NAME = "upload"
MAX_FILENAME_LENGTH = 120


def safe_filename(name: str) -> str:
    """Reduce a client-supplied filename to a harmless basename.

    Browsers normally send a bare filename, but nothing stops a handcrafted request from sending
    "../../etc/cron.d/x" or a Windows-style "..\\..\\x", so both separator styles are stripped
    before the name is used to build a path.
    """
    base = name.replace("\\", "/").split("/")[-1]
    base = _UNSAFE_CHARS.sub("_", base).lstrip(".")
    return (base or _FALLBACK_NAME)[:MAX_FILENAME_LENGTH]


class CaseDirExistsError(FileExistsError):
    """The operator asked for a folder name that is already taken in the target directory."""


def create_named_case_dir(parent: Path, folder_name: str) -> Path:
    """Create exactly the working directory the operator named, below parent.

    Named rather than timestamped, because this directory is what FDS writes its results into
    and what comes back through "Ergebnisse" -- a name the operator chose ("Atrium_v3") is worth
    much more there than "20260904-101530_atrium". It must not exist yet: uploading a second
    case into a directory that already holds one would mix two result sets into one download.
    """
    if not folder_name.strip():
        raise ValueError("Ordnername fehlt")
    target = parent / safe_filename(folder_name)
    try:
        target.mkdir(parents=True)
    except FileExistsError as exc:
        raise CaseDirExistsError(str(target)) from exc
    return target


def create_case_dir(upload_root: Path, case_name: str) -> Path:
    """A fresh, timestamped directory for one uploaded case."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = Path(safe_filename(case_name)).stem or _FALLBACK_NAME
    candidate = upload_root / f"{stamp}_{stem}"
    suffix = 2
    while candidate.exists():  # two uploads within the same second
        candidate = upload_root / f"{stamp}_{stem}-{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def result_files(fds_file: Path) -> list[Path]:
    """The case's input file plus every output FDS wrote for it.

    FDS prefixes all of its output with the case's CHID, so selecting by that prefix packages
    exactly this case -- important because a browsed (as opposed to uploaded) .fds file often
    sits in a directory holding several cases, which must not end up in someone else's download.
    Subdirectories are not descended into; FDS writes its results flat next to the input file.
    """
    case_dir = fds_file.parent
    if not case_dir.is_dir():
        return []
    chid = extract_chid(fds_file)
    return [
        child
        for child in sorted(case_dir.iterdir())
        if child.is_file() and (child.name == fds_file.name or child.name.startswith(chid))
    ]


def write_results_zip(fds_file: Path, target_zip: Path) -> int:
    """Zip the case's files into target_zip, returning how many were written.

    Compression level 1: FDS output (.csv, .out, Smokeview files) still shrinks a lot, while a
    multi-gigabyte result set would take far longer at the default level for little extra gain.
    """
    files = result_files(fds_file)
    with zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for file in files:
            archive.write(file, arcname=file.name)
    return len(files)


def write_directory_zip(source_dir: Path, target_zip: Path) -> int:
    """Zip every file directly inside source_dir (flat, not recursive -- FDS never writes into
    subdirectories of its own working directory).

    Used for cluster case-file transfer, in both directions: unlike write_results_zip, this is
    not filtered to CHID-prefixed/exact-name files, because an uploaded auxiliary input (a ramp
    .csv, an included .inc with an arbitrary name) is not necessarily CHID-prefixed before FDS
    has even run -- only its own *output* reliably is. Safe to ship the whole directory because
    an uploaded case already gets an exclusive directory (create_case_dir/create_named_case_dir);
    a browsed directory shared with unrelated cases is a pre-existing, documented simplification
    (see result_files' own docstring for the same caveat on downloads).
    """
    files = [f for f in sorted(source_dir.iterdir()) if f.is_file()]
    with zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for file in files:
            archive.write(file, arcname=file.name)
    return len(files)


def extract_zip(zip_path: Path, target_dir: Path) -> int:
    """Inverse of write_directory_zip -- extract every member flat into target_dir, returning how
    many files were written. Rejects any member whose name would resolve outside target_dir (a
    zip-slip guard), since this is used to receive an archive built by another machine."""
    target_dir.mkdir(parents=True, exist_ok=True)
    resolved_target = target_dir.resolve()
    written = 0
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            name = safe_filename(member.filename)
            destination = (target_dir / name).resolve()
            if destination != resolved_target and resolved_target not in destination.parents:
                raise ValueError(f"unsafe path in archive: {member.filename!r}")
            with archive.open(member) as source, destination.open("wb") as sink:
                sink.write(source.read())
            written += 1
    return written
