"""Best-effort CPU temperature readout (CLAUDE.md section 8).

Linux exposes sensors via psutil.sensors_temperatures() (lm-sensors backed). macOS has no
equivalent without `powermetrics` (requires sudo) or SMC access, so this returns None there --
by design, not an error. Queue/monitoring/out-parsing must never depend on a temperature value
being present.
"""

from __future__ import annotations

import psutil

# Common Linux hwmon chip/label names for the CPU package/core sensor, checked in order --
# picking one of these over an arbitrary "first sensor" is what makes the reading actually
# "CPU temperature" rather than some other component (e.g. an NVMe or battery sensor) psutil
# happens to report first.
_CPU_HINTS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal", "cpu")


def _all_readings() -> dict[str, float]:
    sensors_fn = getattr(psutil, "sensors_temperatures", None)
    if sensors_fn is None:
        return {}
    try:
        temps = sensors_fn()
    except (AttributeError, NotImplementedError, OSError):
        return {}
    if not temps:
        return {}

    result: dict[str, float] = {}
    for chip_name, entries in temps.items():
        for i, entry in enumerate(entries):
            label = f"{chip_name}:{entry.label}" if entry.label else f"{chip_name}_{i}"
            if entry.current is not None:
                result[label] = entry.current
    return result


def read_cpu_temperature(enabled: bool) -> float | None:
    """Best-effort single CPU temperature in °C, preferring a sensor whose chip/label looks
    like the CPU package/cores over an arbitrary first reading. None on macOS (no sensor
    access without third-party tools) or when nothing matches."""
    if not enabled:
        return None
    readings = _all_readings()
    if not readings:
        return None

    for hint in _CPU_HINTS:
        for label, value in readings.items():
            if hint in label.lower():
                return value
    return next(iter(readings.values()))
