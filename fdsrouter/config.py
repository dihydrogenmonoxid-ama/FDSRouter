"""Load or bootstrap config.yaml for a FDSRouter project directory."""

from __future__ import annotations

import secrets
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_MPI_COMMAND_TEMPLATE = ["{mpi_exec}", "-n", "{n_processes}", "{fds_binary}", "{fds_file}"]

CONFIG_FILENAME = "config.yaml"
# Hostnames that mean "this machine only" -- used wherever a feature (trusted_proxy_header,
# discovery) must behave differently once the Controller is actually reachable from the network.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


@dataclass
class Config:
    project_dir: Path
    host: str = "127.0.0.1"
    port: int = 8000
    open_browser: bool = True
    data_dir: Path = field(default_factory=lambda: Path("./data"))
    fds_binary: str | None = None
    mpi_executable: str | None = None
    mpi_command_template: list[str] = field(default_factory=lambda: list(DEFAULT_MPI_COMMAND_TEMPLATE))
    default_mpi_processes: int = 1
    temperature_enabled: bool = True
    upload_dir: Path = field(default_factory=lambda: Path("./data/cases"))
    max_upload_mb: int = 512
    # Off by default. When set to a header name (e.g. "X-Remote-User"), FDSRouter trusts that
    # header's value as an already-authenticated username instead of requiring a password login
    # -- only safe behind a reverse proxy that authenticates the request itself and strips any
    # such header a client might try to forge. Deliberately install-time-only: this is a
    # security decision the installer must make consciously, so it is never exposed in the
    # runtime Settings UI (see core/auth.py, api/app.py).
    trusted_proxy_header: str | None = None
    # Shared secret a remote fdsrouter-agent process presents (Authorization: Bearer <token>) to
    # prove it belongs to this cluster -- copied once from this file into each agent's own
    # agent-config.yaml. Auto-generated so a fresh install already has a working value; there is
    # no per-node token/pairing flow, matching the project's "vertrauenswürdiges Netz" model
    # (see api/app.py's auth_gate for the /api/agent/* check).
    cluster_token: str | None = None
    # Answers LAN broadcast pings so `fdsrouter agent`'s interactive setup can find this
    # Controller without the operator typing an IP -- reveals only hostname/port, never
    # cluster_token (see core/discovery.py). On by default: unlike trusted_proxy_header, there
    # is nothing here an attacker could actually use.
    discovery_enabled: bool = True

    @property
    def db_path(self) -> Path:
        return self.resolved_data_dir / "fdsrouter.db"

    @property
    def resolved_data_dir(self) -> Path:
        return self.data_dir if self.data_dir.is_absolute() else self.project_dir / self.data_dir

    @property
    def resolved_upload_dir(self) -> Path:
        return self.upload_dir if self.upload_dir.is_absolute() else self.project_dir / self.upload_dir


def auto_detect_binaries() -> tuple[str | None, str | None]:
    fds_binary = shutil.which("fds")
    mpi_executable = shutil.which("mpirun") or shutil.which("mpiexec")
    return fds_binary, mpi_executable


def _write_default_config(path: Path) -> dict:
    fds_binary, mpi_executable = auto_detect_binaries()
    data = {
        "host": "127.0.0.1",
        "port": 8000,
        "open_browser": True,
        "data_dir": "./data",
        "fds_binary": fds_binary,
        "mpi_executable": mpi_executable,
        "mpi_command_template": list(DEFAULT_MPI_COMMAND_TEMPLATE),
        "default_mpi_processes": 1,
        "temperature_enabled": True,
        "upload_dir": "./data/cases",
        "max_upload_mb": 512,
        "trusted_proxy_header": None,
        "cluster_token": secrets.token_urlsafe(32),
        "discovery_enabled": True,
    }
    with path.open("w", encoding="utf-8") as f:
        f.write(
            "# Auto-generated on FDSRouter's first start.\n"
            "# fds_binary/mpi_executable were detected via PATH -- please check/adjust.\n"
        )
        yaml.safe_dump(data, f, sort_keys=False)
    return data


def load_config(project_dir: Path) -> Config:
    project_dir = project_dir.resolve()
    config_path = project_dir / CONFIG_FILENAME

    if config_path.exists():
        with config_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not raw.get("cluster_token"):
            # An installation from before the cluster feature existed has no token yet -- mint
            # one now and persist it, the same "self-heals on next start" spirit as the
            # database's own additive migrations, so agents have something to copy.
            raw["cluster_token"] = secrets.token_urlsafe(32)
            with config_path.open("a", encoding="utf-8") as f:
                f.write("\n# Added when a fdsrouter-agent cluster feature was first used:\n")
                yaml.safe_dump({"cluster_token": raw["cluster_token"]}, f, sort_keys=False)
    else:
        raw = _write_default_config(config_path)

    cfg = Config(project_dir=project_dir)
    cfg.host = raw.get("host", cfg.host)
    cfg.port = int(raw.get("port", cfg.port))
    cfg.open_browser = bool(raw.get("open_browser", cfg.open_browser))
    cfg.data_dir = Path(raw.get("data_dir", "./data"))
    cfg.fds_binary = raw.get("fds_binary")
    cfg.mpi_executable = raw.get("mpi_executable")
    cfg.mpi_command_template = raw.get("mpi_command_template") or list(DEFAULT_MPI_COMMAND_TEMPLATE)
    cfg.default_mpi_processes = int(raw.get("default_mpi_processes", 1))
    cfg.temperature_enabled = bool(raw.get("temperature_enabled", True))
    cfg.upload_dir = Path(raw.get("upload_dir", "./data/cases"))
    cfg.max_upload_mb = int(raw.get("max_upload_mb", 512))
    cfg.trusted_proxy_header = raw.get("trusted_proxy_header") or None
    cfg.cluster_token = raw.get("cluster_token") or None
    cfg.discovery_enabled = bool(raw.get("discovery_enabled", True))

    cfg.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    return cfg
