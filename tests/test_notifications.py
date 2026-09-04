import asyncio

import httpx
import pytest

from fdsrouter.core import notifications

JOB = {"id": "job-1", "name": "Atrium", "status": "done", "project": "P1", "exit_message": None, "finished_at": "now"}


def test_settings_default_to_all_three_terminal_events():
    settings = notifications.NotificationSettings.from_settings_dict({})
    assert settings.events == ("done", "failed", "cancelled")
    assert settings.webhook_configured is False
    assert settings.email_configured is False


def test_settings_parse_custom_event_list():
    settings = notifications.NotificationSettings.from_settings_dict({"notify_events": "failed, cancelled"})
    assert settings.events == ("failed", "cancelled")


def test_unconfigured_webhook_and_email_are_silently_skipped():
    settings = notifications.NotificationSettings.from_settings_dict({})
    assert asyncio.run(notifications.send_webhook(settings, JOB)) is False
    assert notifications.send_email(settings, JOB) is False


def test_webhook_posts_the_job_payload(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            calls.append((url, json))
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    settings = notifications.NotificationSettings.from_settings_dict({"notify_webhook_url": "https://example.test/hook"})
    ok = asyncio.run(notifications.send_webhook(settings, JOB))

    assert ok is True
    assert calls[0][0] == "https://example.test/hook"
    assert calls[0][1]["job_id"] == "job-1"
    assert calls[0][1]["status"] == "done"


def test_webhook_failure_is_swallowed(monkeypatch):
    class FailingAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            raise httpx.ConnectError("nope")

    monkeypatch.setattr(httpx, "AsyncClient", FailingAsyncClient)

    settings = notifications.NotificationSettings.from_settings_dict({"notify_webhook_url": "https://example.test/hook"})
    assert asyncio.run(notifications.send_webhook(settings, JOB)) is False


def test_email_sends_via_smtp(monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            sent.append(("starttls",))

        def login(self, user, password):
            sent.append(("login", user))

        def send_message(self, message):
            sent.append(("send", message["To"], message["Subject"]))

    import smtplib

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    settings = notifications.NotificationSettings.from_settings_dict(
        {
            "notify_email_to": "ops@example.test",
            "notify_email_smtp_host": "smtp.example.test",
            "notify_email_from": "fdsrouter@example.test",
        }
    )
    ok = notifications.send_email(settings, JOB)

    assert ok is True
    assert ("send", "ops@example.test", "FDSRouter: Atrium -- done") in sent
