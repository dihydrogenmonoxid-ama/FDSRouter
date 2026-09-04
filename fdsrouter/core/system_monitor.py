"""Always-on system-wide resource monitor.

Independent of any job's lifecycle -- CPU/RAM/temperature stay visible even with nothing
queued or running (a permanent "task manager" style panel), and system-wide CPU sampling
must happen from exactly one place: psutil.cpu_percent() is delta-based against its own
last-call timestamp, so a second concurrent caller would corrupt both readings.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

import psutil

from fdsrouter.config import Config
from fdsrouter.core import fans, temperature

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 2.0

Broadcast = Callable[[dict], Awaitable[None]]


class SystemState:
    """Holds the most recent system-wide sample so job-specific code (run_metric_sample
    persistence) can reuse it instead of calling psutil.cpu_percent() a second time."""

    def __init__(self) -> None:
        self.latest: dict[str, Any] | None = None


def _sample() -> dict[str, Any]:
    cpu_per_core = psutil.cpu_percent(percpu=True)
    cpu_total = sum(cpu_per_core) / len(cpu_per_core) if cpu_per_core else None
    vm = psutil.virtual_memory()
    return {
        "cpu_percent_total": cpu_total,
        "cpu_percent_per_core": cpu_per_core,
        "ram_percent": vm.percent,
        "ram_used_mb": (vm.total - vm.available) / (1024 * 1024),
        "ram_total_mb": vm.total / (1024 * 1024),
    }


async def poll_loop(config: Config, state: SystemState, broadcast: Broadcast) -> None:
    psutil.cpu_percent(percpu=True)  # prime the delta-based counter
    try:
        while True:
            await asyncio.sleep(POLL_INTERVAL_S)
            try:
                snapshot = _sample()
                snapshot["cpu_temperature_c"] = temperature.read_cpu_temperature(config.temperature_enabled)
                fan = fans.read_fan_speed()
                snapshot["fan_rpm"] = fan.rpm
                # Carried along so the panel can say why the field is empty instead of
                # showing a bare dash that looks like a broken readout.
                snapshot["fan_status"] = fan.reason
                state.latest = snapshot
                await broadcast({"type": "system_metrics", **snapshot})
            except Exception:
                logger.exception("system monitor sample failed")
    except asyncio.CancelledError:
        raise
