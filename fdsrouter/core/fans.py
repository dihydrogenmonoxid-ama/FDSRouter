"""Best-effort fan speed readout.

Only meaningful on Linux (psutil.sensors_fans() reads /sys/class/hwmon) -- macOS has no fan
RPM access via psutil at all (verified: the attribute doesn't exist there), and Windows isn't
a primary target (CLAUDE.md). Worth having on the Linux servers this tool is meant to run
long jobs on: a spinning-up fan is an early, useful signal of thermal load during a run.
"""

from __future__ import annotations

import psutil


def read_fan_speed_rpm() -> int | None:
    """Highest reported fan RPM across all sensors, or None if unavailable (macOS/no sensors)."""
    sensors_fn = getattr(psutil, "sensors_fans", None)
    if sensors_fn is None:
        return None
    try:
        fans = sensors_fn()
    except (AttributeError, NotImplementedError, OSError):
        return None
    if not fans:
        return None

    speeds = [entry.current for entries in fans.values() for entry in entries if entry.current is not None]
    return max(speeds) if speeds else None
