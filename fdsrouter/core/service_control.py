"""Control the systemd unit FDSRouter itself runs under -- the buttons in the settings dialog.

install.sh registers FDSRouter either as a user unit (~/.config/systemd/user/fdsrouter.service)
or as a system unit (/etc/systemd/system/fdsrouter.service), so restart and stop go through
systemctl. The update pulls the repository and reinstalls it into the virtual environment
first, and only then restarts -- that way the dialog can report what the update did before this
process is replaced by the new code.

Every unavailability is reported as a short reason key the frontend translates, so an
installation without systemd (macOS, a manual `fdsrouter start`) simply shows a hint instead of
buttons that cannot work.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

UNIT_NAME = "fdsrouter.service"

# .../FDSRouter/fdsrouter/core/service_control.py -> .../FDSRouter. For an editable install
# (what install.sh sets up) this is the git checkout; a plain wheel install lands in
# site-packages instead, where there is no .git and updating is therefore unavailable.
REPO_DIR = Path(__file__).resolve().parent.parent.parent

USER_UNIT = Path.home() / ".config" / "systemd" / "user" / UNIT_NAME
SYSTEM_UNIT = Path("/etc/systemd/system") / UNIT_NAME

logger = logging.getLogger(__name__)

SYSTEMCTL_TIMEOUT_S = 30
UPDATE_TIMEOUT_S = 600
MAX_OUTPUT_CHARS = 4000


class ServiceControlError(RuntimeError):
    """A systemctl/git/pip call failed; the message carries the command output for the UI."""


def _scope() -> str | None:
    if USER_UNIT.exists():
        return "user"
    if SYSTEM_UNIT.exists():
        return "system"
    return None


def _sudo_without_password() -> bool:
    try:
        return subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _systemctl(scope: str) -> list[str]:
    if scope == "user":
        return ["systemctl", "--user"]
    if os.geteuid() == 0:
        return ["systemctl"]
    return ["sudo", "-n", "systemctl"]


def _unavailable_reason(scope: str | None) -> str | None:
    """None means restart/stop are usable; otherwise a key the frontend turns into a hint."""
    if shutil.which("systemctl") is None:
        return "no_systemd"
    if scope is None:
        return "no_unit"
    if scope == "system" and os.geteuid() != 0 and not _sudo_without_password():
        return "needs_root"
    return None


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_DIR), *args], capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT_S
    )


def _revision() -> tuple[str | None, str | None]:
    if not _can_update():
        return None, None
    try:
        commit = _git("rev-parse", "--short", "HEAD")
        date = _git("log", "-1", "--format=%cs")
    except (OSError, subprocess.SubprocessError):
        return None, None
    if commit.returncode != 0:
        return None, None
    return commit.stdout.strip(), date.stdout.strip() or None


def _can_update() -> bool:
    return shutil.which("git") is not None and (REPO_DIR / ".git").exists() and (REPO_DIR / "pyproject.toml").exists()


def _is_active(scope: str) -> bool | None:
    try:
        result = subprocess.run(
            [*_systemctl(scope), "is-active", UNIT_NAME], capture_output=True, text=True, timeout=SYSTEMCTL_TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() == "active"


def status() -> dict:
    scope = _scope()
    reason = _unavailable_reason(scope)
    revision, revision_date = _revision()
    return {
        "controllable": reason is None,
        "reason": reason,
        "scope": scope,
        "unit": UNIT_NAME,
        "active": _is_active(scope) if reason is None and scope else None,
        "can_update": _can_update(),
        "repo_dir": str(REPO_DIR),
        "revision": revision,
        "revision_date": revision_date,
    }


def _run(command: list[str], *, timeout: int, tolerate_signal: bool = False) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ServiceControlError(f"{' '.join(command)}: Zeitlimit überschritten") from exc
    except OSError as exc:
        raise ServiceControlError(f"{' '.join(command)}: {exc}") from exc

    output = (result.stdout + result.stderr).strip()
    if result.returncode == 0:
        return output

    # A negative return code means the process was killed by a signal. When the command asked
    # systemd to restart or stop *this* unit, that is the expected ending rather than a failure:
    # systemd tears down the unit's control group, and the systemctl client that requested it
    # lives in that very group, so it is killed (SIGTERM, code -15) before it can exit on its
    # own. The restart itself was already queued at that point.
    if tolerate_signal and result.returncode < 0:
        logger.info("%s was terminated by signal %s while restarting our own unit", command[0], -result.returncode)
        return output

    raise ServiceControlError(output or f"{' '.join(command)}: Exit-Code {result.returncode}")


def _systemctl_verb(verb: str) -> None:
    scope = _scope()
    reason = _unavailable_reason(scope)
    if reason is not None or scope is None:
        raise ServiceControlError(reason or "no_unit")
    # --no-block: systemd only needs to accept the job; waiting for it to finish is pointless
    # when finishing it means killing us. tolerate_signal covers the race in the other
    # direction, where the teardown reaches the systemctl client before it manages to exit.
    _run([*_systemctl(scope), verb, "--no-block", UNIT_NAME], timeout=SYSTEMCTL_TIMEOUT_S, tolerate_signal=True)


def restart() -> None:
    _systemctl_verb("restart")


def stop() -> None:
    _systemctl_verb("stop")


def update() -> dict:
    """Pull the repository, reinstall it, then restart if systemd manages this instance.

    Pull and install run synchronously so their output can be reported; the restart is the last
    step because it ends this process.
    """
    if not _can_update():
        raise ServiceControlError("no_git_checkout")

    revision_before, _ = _revision()
    output = [_run(["git", "-C", str(REPO_DIR), "pull", "--ff-only"], timeout=UPDATE_TIMEOUT_S)]
    # sys.executable is the interpreter of the virtual environment the service runs in, so the
    # reinstall lands exactly where the running code comes from.
    output.append(
        _run([sys.executable, "-m", "pip", "install", "-e", str(REPO_DIR)], timeout=UPDATE_TIMEOUT_S)
    )
    revision_after, _ = _revision()

    restarted = False
    if _unavailable_reason(_scope()) is None:
        restart()
        restarted = True

    return {
        "ok": True,
        "changed": revision_before != revision_after,
        "revision_before": revision_before,
        "revision_after": revision_after,
        "restarted": restarted,
        "output": "\n".join(part for part in output if part)[-MAX_OUTPUT_CHARS:],
    }
