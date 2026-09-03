"""Home Assistant REST client for a power sensor, plus per-job energy/cost accounting.

Generic against any Home-Assistant-exposed sensor.* entity reporting Watts -- this makes it
work with whatever smart plug HA already integrates (IKEA/Zigbee2MQTT, DIRIGERA, Tasmota,
Shelly, ...) rather than hard-wiring one vendor's protocol. Never raises: energy tracking is
optional, and a flaky/unreachable HA instance must not break a job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

SETTINGS_KEYS = (
    "ha_base_url",
    "ha_token",
    "ha_entity_id",
    "electricity_price_eur_per_kwh",
    "solar_powered",
)


@dataclass
class EnergySettings:
    ha_base_url: str | None = None
    ha_token: str | None = None
    ha_entity_id: str | None = None
    electricity_price_eur_per_kwh: float | None = None
    solar_powered: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.ha_base_url and self.ha_token and self.ha_entity_id)

    @classmethod
    def from_settings_dict(cls, raw: dict[str, str | None]) -> "EnergySettings":
        price_raw = raw.get("electricity_price_eur_per_kwh")
        try:
            price = float(price_raw) if price_raw else None
        except ValueError:
            price = None
        return cls(
            ha_base_url=raw.get("ha_base_url") or None,
            ha_token=raw.get("ha_token") or None,
            ha_entity_id=raw.get("ha_entity_id") or None,
            electricity_price_eur_per_kwh=price,
            solar_powered=(raw.get("solar_powered") == "true"),
        )


async def read_power_watts(settings: EnergySettings) -> float | None:
    """Current power draw in Watts from the configured HA sensor, or None if unconfigured or
    unreachable."""
    if not settings.configured:
        return None
    url = f"{settings.ha_base_url.rstrip('/')}/api/states/{settings.ha_entity_id}"
    headers = {"Authorization": f"Bearer {settings.ha_token}"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return float(resp.json()["state"])
    except Exception:
        logger.warning("Home Assistant power sensor read failed", exc_info=True)
        return None


def energy_cost(energy_kwh: float, settings: EnergySettings) -> float | None:
    """Cost in EUR for the given energy, or None if solar-powered (no cost) or no tariff set."""
    if settings.solar_powered or settings.electricity_price_eur_per_kwh is None:
        return None
    return energy_kwh * settings.electricity_price_eur_per_kwh
