"""Best-effort fan speed readout.

Only meaningful on Linux -- macOS has no fan RPM access through psutil at all (verified: the
attribute doesn't exist there), and Windows isn't a primary target (CLAUDE.md). Worth having on
the Linux servers this tool runs long jobs on: a spinning-up fan is an early, useful signal of
thermal load during a run.

Two sources are tried. psutil.sensors_fans() is the normal one, but it only reports hwmon
devices that expose a `name` file, and on plenty of desktop boards the fan sensors sit behind a
kernel module that is not loaded by default (nct6775, it87, ...) -- which is why the readout
stays empty on machines whose fans clearly do spin. The direct sysfs scan below catches the
first case; the second one is a missing driver that no amount of reading can conjure up, so it
is reported as such instead of silently showing a dash.
"""

from __future__ import annotations

import glob
import platform
from dataclasses import dataclass

import psutil

# Both layouts seen in the wild: fan*_input directly in the hwmon directory, and the older
# symlinked device/ subdirectory.
HWMON_PATTERNS = ("/sys/class/hwmon/hwmon*/fan*_input", "/sys/class/hwmon/hwmon*/device/fan*_input")


@dataclass
class FanReading:
    """rpm is the highest speed found; reason says why there is none, for the UI hint."""

    rpm: int | None = None
    source: str | None = None  # "psutil" | "hwmon"
    reason: str | None = None  # "unsupported_platform" | "no_sensors"


def _psutil_speeds() -> list[int]:
    sensors_fn = getattr(psutil, "sensors_fans", None)
    if sensors_fn is None:
        return []
    try:
        fans = sensors_fn()
    except (AttributeError, NotImplementedError, OSError):
        return []
    return [int(e.current) for entries in fans.values() for e in entries if e.current is not None]


def _hwmon_speeds() -> list[int]:
    """Read fan*_input straight from sysfs, for the chips psutil skips."""
    speeds = []
    for pattern in HWMON_PATTERNS:
        for path in glob.glob(pattern):
            try:
                with open(path, encoding="utf-8") as f:
                    speeds.append(int(f.read().strip()))
            except (OSError, ValueError):
                continue  # a sensor that disappears or reports garbage is simply skipped
    return speeds


def read_fan_speed() -> FanReading:
    """Highest fan RPM across all readable sensors, with the reason when there is none."""
    speeds = _psutil_speeds()
    source = "psutil"
    if not speeds:
        speeds = _hwmon_speeds()
        source = "hwmon"

    if not speeds:
        # Not a fault worth reporting on a Mac; there is simply no interface to read.
        return FanReading(reason="unsupported_platform" if platform.system() != "Linux" else "no_sensors")
    return FanReading(rpm=max(speeds), source=source)


def read_fan_speed_rpm() -> int | None:
    """Highest reported fan RPM, or None if unavailable."""
    return read_fan_speed().rpm
