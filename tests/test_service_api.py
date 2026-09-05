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


def test_cluster_info_exposes_what_an_agent_needs_to_pair(client):
    payload = client.get("/api/service/cluster-info").json()

    assert set(payload) == {"hostname", "port", "cluster_token", "lan_reachable", "discovery_active"}
    assert payload["cluster_token"] == client.app.state.config.cluster_token
    assert payload["port"] == client.app.state.config.port
    # The default Config() fixture binds to loopback -- discovery can never actually help there.
    assert payload["lan_reachable"] is False
    assert payload["discovery_active"] is False


def test_get_editable_config_exposes_only_the_safe_subset(client):
    payload = client.get("/api/service/config").json()

    assert set(payload) == {
        "host", "port", "open_browser", "fds_binary", "mpi_executable",
        "default_mpi_processes", "temperature_enabled", "discovery_enabled", "max_upload_mb",
    }
    # Never exposed for editing here, regardless of what's added to the response above.
    assert "role" not in payload
    assert "controller_url" not in payload
    assert "cluster_token" not in payload
    assert "trusted_proxy_header" not in payload
    assert "data_dir" not in payload


def test_put_editable_config_applies_immediately_without_a_restart_flag(client):
    response = client.put("/api/service/config", json={"default_mpi_processes": 4, "max_upload_mb": 1024})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "restart_required": False}
    # Applied live to the shared in-memory config -- no restart needed for these fields.
    assert client.app.state.config.default_mpi_processes == 4
    assert client.app.state.config.max_upload_mb == 1024


def test_put_editable_config_flags_restart_for_host_port_and_discovery(client):
    response = client.put("/api/service/config", json={"port": 9001})

    assert response.json()["restart_required"] is True
    assert client.app.state.config.port == 9001


def test_put_editable_config_persists_to_disk(client, tmp_path):
    client.put("/api/service/config", json={"fds_binary": "/opt/fds/fds"})

    from fdsrouter.config import load_config

    reloaded = load_config(tmp_path)
    assert reloaded.fds_binary == "/opt/fds/fds"


def test_put_editable_config_logs_an_audit_entry(client):
    client.put("/api/service/config", json={"temperature_enabled": False})

    audit = client.app.state.db.get_audit_entries()
    assert any(e["action"] == "config_update" for e in audit)


def test_put_editable_config_cannot_touch_role_or_secrets(client):
    response = client.put(
        "/api/service/config",
        json={"port": 9002, "role": "agent", "cluster_token": "attacker-supplied"},
    )

    # Unknown fields are simply ignored by the pydantic model -- role/cluster_token never change.
    assert response.status_code == 200
    assert client.app.state.config.role != "agent"
    assert client.app.state.config.cluster_token != "attacker-supplied"


def test_cluster_info_reports_lan_reachable_once_host_is_not_loopback(tmp_path):
    config = Config(project_dir=tmp_path, host="0.0.0.0")
    config.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(config)) as test_client:
        payload = test_client.get("/api/service/cluster-info").json()

    assert payload["lan_reachable"] is True
    assert payload["discovery_active"] is True


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


class _Result:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_restarting_our_own_unit_survives_being_killed_by_it(monkeypatch):
    """systemd tears down the control group the systemctl client itself runs in, so the call
    comes back as 'killed by SIGTERM' (-15) even though the restart was accepted."""
    monkeypatch.setattr(service_control.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(service_control, "_scope", lambda: "user")
    monkeypatch.setattr(service_control.subprocess, "run", lambda *a, **k: _Result(-15))

    service_control.restart()  # must not raise


def test_a_real_systemctl_failure_is_still_an_error(monkeypatch):
    monkeypatch.setattr(service_control.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(service_control, "_scope", lambda: "user")
    monkeypatch.setattr(
        service_control.subprocess,
        "run",
        lambda *a, **k: _Result(1, stderr="Unit fdsrouter.service not found."),
    )

    with pytest.raises(service_control.ServiceControlError, match="not found"):
        service_control.restart()


def test_a_signal_death_outside_the_restart_path_is_not_tolerated(monkeypatch):
    monkeypatch.setattr(service_control.subprocess, "run", lambda *a, **k: _Result(-9))

    with pytest.raises(service_control.ServiceControlError):
        service_control._run(["git", "pull"], timeout=5)
