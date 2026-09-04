"""Runtime-editable operational settings (currently: Home Assistant / energy config).

Separate from config.yaml on purpose -- these can change while the service is running and are
edited through the UI, whereas config.yaml is install-time host configuration.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from fdsrouter.core.energy import SETTINGS_KEYS, EnergySettings, read_power_watts
from fdsrouter.core.notifications import (
    SETTINGS_KEYS as NOTIFY_SETTINGS_KEYS,
    NotificationSettings,
    send_email,
    send_webhook,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])

_TOKEN_MASK = "***"
_ALL_SETTINGS_KEYS = tuple(SETTINGS_KEYS) + tuple(NOTIFY_SETTINGS_KEYS)


class SettingsUpdate(BaseModel):
    ha_base_url: str | None = None
    ha_token: str | None = None
    ha_entity_id: str | None = None
    electricity_price_eur_per_kwh: float | None = None
    solar_powered: bool | None = None
    notify_webhook_url: str | None = None
    notify_email_to: str | None = None
    notify_email_smtp_host: str | None = None
    notify_email_smtp_port: int | None = None
    notify_email_smtp_user: str | None = None
    notify_email_smtp_password: str | None = None
    notify_email_from: str | None = None
    notify_events: str | None = None


@router.get("")
def get_settings(request: Request) -> dict:
    raw = dict(request.app.state.db.get_settings(_ALL_SETTINGS_KEYS))
    if raw.get("ha_token"):
        raw["ha_token"] = _TOKEN_MASK  # never echo the real token back to the frontend
    if raw.get("notify_email_smtp_password"):
        raw["notify_email_smtp_password"] = _TOKEN_MASK
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
    if payload.notify_webhook_url is not None:
        values["notify_webhook_url"] = payload.notify_webhook_url
    if payload.notify_email_to is not None:
        values["notify_email_to"] = payload.notify_email_to
    if payload.notify_email_smtp_host is not None:
        values["notify_email_smtp_host"] = payload.notify_email_smtp_host
    if payload.notify_email_smtp_port is not None:
        values["notify_email_smtp_port"] = str(payload.notify_email_smtp_port)
    if payload.notify_email_smtp_user is not None:
        values["notify_email_smtp_user"] = payload.notify_email_smtp_user
    if payload.notify_email_smtp_password is not None and payload.notify_email_smtp_password != _TOKEN_MASK:
        values["notify_email_smtp_password"] = payload.notify_email_smtp_password
    if payload.notify_email_from is not None:
        values["notify_email_from"] = payload.notify_email_from
    if payload.notify_events is not None:
        values["notify_events"] = payload.notify_events
    request.app.state.db.set_settings(values)
    return {"ok": True}


@router.post("/test-energy-connection")
async def test_energy_connection(request: Request) -> dict:
    raw = request.app.state.db.get_settings(SETTINGS_KEYS)
    settings = EnergySettings.from_settings_dict(raw)
    watts = await read_power_watts(settings)
    return {"ok": watts is not None, "watts": watts}


@router.post("/test-notification")
async def test_notification(request: Request) -> dict:
    raw = request.app.state.db.get_settings(NOTIFY_SETTINGS_KEYS)
    settings = NotificationSettings.from_settings_dict(raw)
    test_job = {
        "id": "test",
        "name": "Testbenachrichtigung",
        "status": "done",
        "project": None,
        "exit_message": None,
        "finished_at": None,
    }
    webhook_ok = await send_webhook(settings, test_job)
    email_ok = send_email(settings, test_job)
    return {
        "webhook_configured": settings.webhook_configured,
        "webhook_ok": webhook_ok,
        "email_configured": settings.email_configured,
        "email_ok": email_ok,
    }
