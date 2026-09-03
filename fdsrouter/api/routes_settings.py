"""Runtime-editable operational settings (currently: Home Assistant / energy config).

Separate from config.yaml on purpose -- these can change while the service is running and are
edited through the UI, whereas config.yaml is install-time host configuration.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from fdsrouter.core.energy import SETTINGS_KEYS, EnergySettings, read_power_watts

router = APIRouter(prefix="/api/settings", tags=["settings"])

_TOKEN_MASK = "***"


class SettingsUpdate(BaseModel):
    ha_base_url: str | None = None
    ha_token: str | None = None
    ha_entity_id: str | None = None
    electricity_price_eur_per_kwh: float | None = None
    solar_powered: bool | None = None


@router.get("")
def get_settings(request: Request) -> dict:
    raw = dict(request.app.state.db.get_settings(SETTINGS_KEYS))
    if raw.get("ha_token"):
        raw["ha_token"] = _TOKEN_MASK  # never echo the real token back to the frontend
    return raw


@router.put("")
def put_settings(payload: SettingsUpdate, request: Request) -> dict:
    values: dict[str, str | None] = {}
    if payload.ha_base_url is not None:
        values["ha_base_url"] = payload.ha_base_url
    # An unchanged token comes back still masked -- writing that back would destroy it.
    if payload.ha_token is not None and payload.ha_token != _TOKEN_MASK:
        values["ha_token"] = payload.ha_token
    if payload.ha_entity_id is not None:
        values["ha_entity_id"] = payload.ha_entity_id
    if payload.electricity_price_eur_per_kwh is not None:
        values["electricity_price_eur_per_kwh"] = str(payload.electricity_price_eur_per_kwh)
    if payload.solar_powered is not None:
        values["solar_powered"] = "true" if payload.solar_powered else "false"
    request.app.state.db.set_settings(values)
    return {"ok": True}


@router.post("/test-energy-connection")
async def test_energy_connection(request: Request) -> dict:
    raw = request.app.state.db.get_settings(SETTINGS_KEYS)
    settings = EnergySettings.from_settings_dict(raw)
    watts = await read_power_watts(settings)
    return {"ok": watts is not None, "watts": watts}
