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
    # "controller" (serve the queue, own the database), "agent" (join another machine's
    # Controller as a compute node), or "auto" -- decided interactively on first start (see
    # cli.py's start()) so `fdsrouter start` is the only command anyone has to remember; the
    # decision is then persisted here and never asked again. A config.yaml from before this
    # field existed defaults to "controller" (see load_config), not "auto" -- an install that
    # has been running as a Controller for months must never suddenly prompt on its next start.
    role: str = "auto"
    # Only meaningful when role == "agent": which Controller to join. Filled in by the pairing
    # step (LAN discovery or manual entry), not hand-edited in the common case.
    controller_url: str | None = None

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
        "role": "auto",
        "controller_url": None,
    }
    with path.open("w", encoding="utf-8") as f:
        f.write(
            "# Auto-generated on FDSRouter's first start.\n"
            "# fds_binary/mpi_executable were detected via PATH -- please check/adjust.\n"
        )
        yaml.safe_dump(data, f, sort_keys=False)
    return data


def _config_to_dict(config: Config) -> dict:
    return {
        "host": config.host,
        "port": config.port,
        "open_browser": config.open_browser,
        "data_dir": str(config.data_dir),
        "fds_binary": config.fds_binary,
        "mpi_executable": config.mpi_executable,
        "mpi_command_template": config.mpi_command_template,
        "default_mpi_processes": config.default_mpi_processes,
        "temperature_enabled": config.temperature_enabled,
        "upload_dir": str(config.upload_dir),
        "max_upload_mb": config.max_upload_mb,
        "trusted_proxy_header": config.trusted_proxy_header,
        "cluster_token": config.cluster_token,
        "discovery_enabled": config.discovery_enabled,
        "role": config.role,
        "controller_url": config.controller_url,
    }


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
        if not raw.get("role"):
            # An installation from before "role" existed has necessarily already been running
            # as a Controller (that was the only thing `fdsrouter start` could ever be) -- default
            # it explicitly to "controller", not "auto", so a service that has run unattended for
            # months never suddenly hits an interactive discovery prompt on its next restart.
            raw["role"] = "controller"
            with config_path.open("a", encoding="utf-8") as f:
                f.write("\n# Added when the multi-command-free cluster setup was first used:\n")
                yaml.safe_dump({"role": "controller"}, f, sort_keys=False)
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
    cfg.role = raw.get("role") or "controller"  # matches the self-heal default just above
    cfg.controller_url = raw.get("controller_url") or None

    cfg.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def save_config(config: Config) -> None:
    """Full rewrite of config.yaml from the in-memory Config -- used when the UI edits settings
    (routes_service.py's PUT /api/service/config). Unlike the append-only self-heals above,
    which must never clobber comments or manual edits in a file the UI hasn't touched, a save
    triggered from the UI editor is exactly the case where a clean, deterministic rewrite is
    what the operator actually wants: what they see in the form is what ends up on disk.
    """
    config_path = config.project_dir / CONFIG_FILENAME
    with config_path.open("w", encoding="utf-8") as f:
        f.write("# Edited via the FDSRouter web UI (Betrieb -> Konfiguration).\n")
        yaml.safe_dump(_config_to_dict(config), f, sort_keys=False)


def persist_role(project_dir: Path, role: str, controller_url: str | None = None, cluster_token: str | None = None) -> None:
    """Write role (and, once decided, controller_url/cluster_token) back into config.yaml so
    the interactive first-run decision in cli.py's start() is never asked again -- same
    append-an-override-block approach as load_config's own self-heals, which preserves whatever
    else is already in the file (comments, manual edits) instead of rewriting it wholesale."""
    config_path = project_dir / CONFIG_FILENAME
    values: dict[str, str | None] = {"role": role}
    if controller_url is not None:
        values["controller_url"] = controller_url
    if cluster_token is not None:
        values["cluster_token"] = cluster_token
    with config_path.open("a", encoding="utf-8") as f:
        f.write("\n# Written by fdsrouter start's interactive role setup:\n")
        yaml.safe_dump(values, f, sort_keys=False)
