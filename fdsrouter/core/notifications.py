"""Optional webhook/email notification on a run reaching done/failed/cancelled.

Same resilience contract as energy.py's Home Assistant client: unconfigured or unreachable must
never be fatal to a run -- a notification is a courtesy, not part of the job's own result.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage

import httpx

logger = logging.getLogger(__name__)

SETTINGS_KEYS = (
    "notify_webhook_url",
    "notify_email_to",
    "notify_email_smtp_host",
    "notify_email_smtp_port",
    "notify_email_smtp_user",
    "notify_email_smtp_password",
    "notify_email_from",
    "notify_events",
)

DEFAULT_EVENTS = ("done", "failed", "cancelled")


@dataclass
class NotificationSettings:
    webhook_url: str | None = None
    email_to: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    email_from: str | None = None
    events: tuple[str, ...] = field(default_factory=lambda: DEFAULT_EVENTS)

    @property
    def webhook_configured(self) -> bool:
        return bool(self.webhook_url)

    @property
    def email_configured(self) -> bool:
        return bool(self.email_to and self.smtp_host and self.email_from)

    @classmethod
    def from_settings_dict(cls, raw: dict[str, str | None]) -> "NotificationSettings":
        port_raw = raw.get("notify_email_smtp_port")
        try:
            port = int(port_raw) if port_raw else 587
        except ValueError:
            port = 587
        events_raw = raw.get("notify_events")
        events = tuple(e.strip() for e in events_raw.split(",") if e.strip()) if events_raw else DEFAULT_EVENTS
        return cls(
            webhook_url=raw.get("notify_webhook_url") or None,
            email_to=raw.get("notify_email_to") or None,
            smtp_host=raw.get("notify_email_smtp_host") or None,
            smtp_port=port,
            smtp_user=raw.get("notify_email_smtp_user") or None,
            smtp_password=raw.get("notify_email_smtp_password") or None,
            email_from=raw.get("notify_email_from") or None,
            events=events,
        )


def _payload(job: dict) -> dict:
    return {
        "job_id": job["id"],
        "name": job["name"],
        "status": job["status"],
        "project": job.get("project"),
        "exit_message": job.get("exit_message"),
        "finished_at": job.get("finished_at"),
    }


async def send_webhook(settings: NotificationSettings, job: dict) -> bool:
    if not settings.webhook_configured:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(settings.webhook_url, json=_payload(job))
            resp.raise_for_status()
        return True
    except Exception:
        logger.warning("notification webhook failed for job %s", job.get("id"), exc_info=True)
        return False


def send_email(settings: NotificationSettings, job: dict) -> bool:
    if not settings.email_configured:
        return False
    message = EmailMessage()
    message["Subject"] = f"FDSRouter: {job['name']} -- {job['status']}"
    message["From"] = settings.email_from
    message["To"] = settings.email_to
    lines = [f"Lauf: {job['name']}", f"Status: {job['status']}"]
    if job.get("project"):
        lines.append(f"Projekt: {job['project']}")
    if job.get("exit_message"):
        lines.append(f"Meldung: {job['exit_message']}")
    message.set_content("\n".join(lines))
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10.0) as smtp:
            smtp.starttls()
            if settings.smtp_user and settings.smtp_password:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
        return True
    except Exception:
        logger.warning("notification email failed for job %s", job.get("id"), exc_info=True)
        return False
