#!/usr/bin/env python3
"""Create or update config.yaml for this installation.

install.sh runs this through the freshly created virtual environment. It is also useful on its
own, for instance after moving the FDS installation or when switching to network operation:

    .venv/bin/python scripts/configure.py --host 0.0.0.0 --port 8000

Existing values are kept; only entries that are missing (or explicitly overridden on the
command line) are touched.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

import yaml

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

from fdsrouter.config import CONFIG_FILENAME, DEFAULT_MPI_COMMAND_TEMPLATE  # noqa: E402

# Directories FDS is commonly unpacked into, plus the macOS bundle location.
INSTALL_ROOTS = ("/opt", "/usr/local", "/Applications", str(Path.home()))

# Relative to a root; kept shallow on purpose so the search stays fast.
FDS_PATTERNS = ("FDS*/FDS6/bin/fds", "fds*/FDS6/bin/fds", "FDS*/bin/fds", "FDS6/bin/fds")

CONFIG_KEY_ORDER = (
    "host",
    "port",
    "open_browser",
    "data_dir",
    "fds_binary",
    "mpi_executable",
    "mpi_command_template",
    "default_mpi_processes",
    "upload_dir",
    "max_upload_mb",
    "temperature_enabled",
)

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8000,
    "open_browser": True,
    "data_dir": "./data",
    "fds_binary": None,
    "mpi_executable": None,
    "mpi_command_template": list(DEFAULT_MPI_COMMAND_TEMPLATE),
    "default_mpi_processes": 1,
    "upload_dir": "./data/cases",
    "max_upload_mb": 512,
    "temperature_enabled": True,
}

HEADER = (
    "# FDSRouter configuration for this installation -- written by scripts/configure.py.\n"
    "# Edit freely; re-running the script keeps every value that is already set.\n"
)


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def find_fds_binary() -> Path | None:
    """Locate the fds executable: PATH first, then the usual installation directories."""
    on_path = shutil.which("fds")
    if on_path:
        return Path(on_path)

    for root in INSTALL_ROOTS:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for pattern in FDS_PATTERNS:
            for match in sorted(root_path.glob(pattern), reverse=True):
                if _executable(match):
                    return match
    return None


def find_mpi_executable(fds_binary: Path | None) -> Path | None:
    """Prefer the MPI runtime shipped with FDS -- FDS is built against exactly that one."""
    if fds_binary is not None:
        fds_root = fds_binary.parent.parent  # .../FDS6
        for name in ("mpiexec", "mpirun"):
            for match in sorted(fds_root.rglob(name)):
                if _executable(match):
                    return match

    for name in ("mpirun", "mpiexec"):
        on_path = shutil.which(name)
        if on_path:
            return Path(on_path)
    return None


def find_fds_vars(fds_binary: Path | None) -> Path | None:
    """Find FDS6VARS.sh, the environment script the official FDS bundle ships."""
    if fds_binary is None:
        return None
    for candidate in (fds_binary.parent / "FDS6VARS.sh", fds_binary.parent.parent / "bin" / "FDS6VARS.sh"):
        if candidate.is_file():
            return candidate
    return None


def write_service_env(path: Path, fds_binary: Path | None, mpi_executable: Path | None) -> bool:
    """Write the environment the systemd service sources before starting FDSRouter.

    A systemd unit starts with a minimal PATH and none of the shell profile, so anything the
    FDS installer added to ~/.bashrc is missing. Never overwrites an edited file.
    """
    if path.exists():
        return False

    lines = [
        "# Environment for the FDSRouter systemd service, sourced by scripts/fdsrouter-service.sh.",
        "# A service does not read ~/.bashrc, so FDS and MPI are set up here instead.",
        "# Written by scripts/configure.py; edit by hand if your installation differs.",
        "",
    ]

    fds_vars = find_fds_vars(fds_binary)
    if fds_vars is not None:
        lines.append(f'. "{fds_vars}"')

    bin_dirs = []
    for binary in (fds_binary, mpi_executable):
        if binary is not None and str(binary.parent) not in bin_dirs:
            bin_dirs.append(str(binary.parent))
    if bin_dirs:
        lines.append('export PATH="' + ":".join(bin_dirs) + ':$PATH"')

    if fds_vars is None and not bin_dirs:
        lines.append("# No FDS installation was found -- add the necessary exports here.")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="config.yaml fuer diese Installation schreiben")
    parser.add_argument("--host", help='Adresse, z. B. 0.0.0.0 fuer den Zugriff aus dem Netz')
    parser.add_argument("--port", type=int)
    parser.add_argument("--service-env", action="store_true", help="service.env fuer den systemd-Dienst anlegen")
    args = parser.parse_args()

    config_path = REPO_DIR / CONFIG_FILENAME
    raw = {}
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    merged = {key: raw.get(key, value) for key, value in DEFAULTS.items()}
    merged.update({key: value for key, value in raw.items() if key not in merged})

    fds_binary = Path(merged["fds_binary"]) if merged.get("fds_binary") else None
    if fds_binary is None or not fds_binary.exists():
        fds_binary = find_fds_binary()
        merged["fds_binary"] = str(fds_binary) if fds_binary else None

    mpi_executable = Path(merged["mpi_executable"]) if merged.get("mpi_executable") else None
    if mpi_executable is None or not mpi_executable.exists():
        mpi_executable = find_mpi_executable(fds_binary)
        merged["mpi_executable"] = str(mpi_executable) if mpi_executable else None

    if args.host:
        merged["host"] = args.host
    if args.port:
        merged["port"] = args.port

    ordered = {key: merged[key] for key in CONFIG_KEY_ORDER if key in merged}
    ordered.update({key: value for key, value in merged.items() if key not in ordered})

    config_path.write_text(HEADER + yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"    ok  {config_path}")

    data_dir = Path(merged["data_dir"])
    if not data_dir.is_absolute():
        data_dir = REPO_DIR / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    if fds_binary:
        print(f"    ok  fds:    {fds_binary}")
    else:
        print("    !!  fds wurde nicht gefunden -- Pfad in config.yaml unter 'fds_binary' eintragen")
    if mpi_executable:
        print(f"    ok  mpi:    {mpi_executable}")
    else:
        print("    !!  mpirun/mpiexec wurde nicht gefunden -- Pfad in config.yaml unter 'mpi_executable' eintragen")
    print(f"    ok  Zugriff: http://{merged['host'] if merged['host'] != '0.0.0.0' else '<server-ip>'}:{merged['port']}/")

    if args.service_env and write_service_env(REPO_DIR / "service.env", fds_binary, mpi_executable):
        print("    ok  service.env angelegt (Umgebung fuer den systemd-Dienst)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
