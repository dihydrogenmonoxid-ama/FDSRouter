from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fdsrouter.api.app import create_app
from fdsrouter.config import Config
from fdsrouter.core import service_control


@pytest.fixture
def client(tmp_path):
    config = Config(project_dir=tmp_path)
    config.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(config)) as test_client:
        yield test_client


@pytest.fixture
def recorded_calls(monkeypatch):
    """Replace the actual systemctl calls -- a test must never restart anything."""
    calls = []
    monkeypatch.setattr(service_control, "restart", lambda: calls.append("restart"))
    monkeypatch.setattr(service_control, "stop", lambda: calls.append("stop"))
    return calls


def _pretend_job_is_running(client, job_id="job-1"):
    client.app.state.queue_manager.current_job_id = lambda: job_id


def test_status_reports_whether_the_service_can_be_controlled(client):
    payload = client.get("/api/service").json()

    assert set(payload) >= {"controllable", "reason", "scope", "active", "can_update", "revision"}
    assert isinstance(payload["controllable"], bool)
    # Either control is possible, or there is a reason the frontend can turn into a hint.
    assert payload["controllable"] is (payload["reason"] is None)


def test_restart_and_stop_are_refused_while_a_job_is_running(client, recorded_calls):
    _pretend_job_is_running(client)

    for action in ("restart", "stop", "update"):
        response = client.post(f"/api/service/{action}", json={"force": False})
        assert response.status_code == 409
        assert response.json()["detail"] == "running_job"

    assert recorded_calls == []


def test_restart_goes_through_once_the_running_job_is_confirmed(client, recorded_calls):
    _pretend_job_is_running(client)

    response = client.post("/api/service/restart", json={"force": True})

    assert response.status_code == 200
    assert recorded_calls == ["restart"]


def test_stop_needs_no_confirmation_when_nothing_is_running(client, recorded_calls):
    response = client.post("/api/service/stop", json={"force": False})

    assert response.status_code == 200
    assert recorded_calls == ["stop"]


def test_failed_systemctl_call_is_reported_as_an_error(client, monkeypatch):
    def fail() -> None:
        raise service_control.ServiceControlError("Unit fdsrouter.service not found.")

    monkeypatch.setattr(service_control, "restart", fail)

    response = client.post("/api/service/restart", json={"force": True})

    assert response.status_code == 500
    assert "not found" in response.json()["detail"]


def test_missing_systemd_is_reported_as_a_reason_instead_of_an_error(monkeypatch):
    monkeypatch.setattr(service_control.shutil, "which", lambda name: None)

    status = service_control.status()

    assert status["controllable"] is False
    assert status["reason"] == "no_systemd"
    assert status["can_update"] is False  # no git either, with which() gone


def test_update_refuses_outside_a_git_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(service_control, "REPO_DIR", Path(tmp_path))

    with pytest.raises(service_control.ServiceControlError, match="no_git_checkout"):
        service_control.update()
