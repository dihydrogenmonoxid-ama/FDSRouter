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
