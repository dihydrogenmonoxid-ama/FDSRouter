import pytest
from fastapi.testclient import TestClient

from fdsrouter.api.app import create_app
from fdsrouter.config import Config


@pytest.fixture
def client(tmp_path):
    config = Config(project_dir=tmp_path)
    config.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(config)) as test_client:
        yield test_client


def test_bootstrap_mode_is_fully_open(client):
    session = client.get("/api/auth/session").json()
    assert session == {"authenticated": False, "user": None, "bootstrap": True}

    # No account exists yet -- the app behaves exactly as it did before auth existed.
    assert client.get("/api/jobs").status_code == 200


def test_registering_the_first_account_logs_in_and_requires_login_from_then_on(client):
    response = client.post(
        "/api/auth/register", json={"username": "alice", "password": "hunter2", "display_name": "Alice"}
    )
    assert response.status_code == 200
    assert response.json()["username"] == "alice"

    # Bootstrap registration logs the new account in immediately.
    assert client.get("/api/jobs").status_code == 200

    # Bootstrap mode ended -- a fresh, cookie-less request is now rejected.
    fresh = TestClient(client.app)
    assert fresh.get("/api/jobs").status_code == 401
    assert fresh.get("/api/auth/session").json() == {"authenticated": False, "user": None, "bootstrap": False}


def test_login_logout_round_trip(client):
    client.post("/api/auth/register", json={"username": "alice", "password": "hunter2"})

    wrong = client.post("/api/auth/login", json={"username": "alice", "password": "nope"})
    assert wrong.status_code == 401

    ok = client.post("/api/auth/login", json={"username": "alice", "password": "hunter2"})
    assert ok.status_code == 200
    assert ok.json()["username"] == "alice"

    # TestClient persists the Set-Cookie session across calls on the same instance.
    assert client.get("/api/jobs").status_code == 200
    assert client.get("/api/auth/session").json()["authenticated"] is True

    client.post("/api/auth/logout")
    assert client.get("/api/jobs").status_code == 401


def test_second_registration_requires_an_existing_session(client):
    client.post("/api/auth/register", json={"username": "alice", "password": "hunter2"})

    anonymous = TestClient(client.app)
    assert anonymous.post("/api/auth/register", json={"username": "bob", "password": "x"}).status_code == 401

    client.post("/api/auth/login", json={"username": "alice", "password": "hunter2"})
    logged_in = client.post("/api/auth/register", json={"username": "bob", "password": "x"})
    assert logged_in.status_code == 200


def test_duplicate_username_is_rejected(client):
    client.post("/api/auth/register", json={"username": "alice", "password": "hunter2"})
    client.post("/api/auth/login", json={"username": "alice", "password": "hunter2"})

    response = client.post("/api/auth/register", json={"username": "alice", "password": "other"})
    assert response.status_code == 409


def test_trusted_proxy_header_auto_provisions_and_logs_in(tmp_path):
    config = Config(project_dir=tmp_path, trusted_proxy_header="X-Remote-User")
    config.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(config)) as client:
        client.post("/api/auth/register", json={"username": "alice", "password": "hunter2"})

        response = client.get("/api/jobs", headers={"X-Remote-User": "carol"})
        assert response.status_code == 200

        session = client.get("/api/auth/session", headers={"X-Remote-User": "carol"}).json()
        assert session["authenticated"] is True
        assert session["user"]["username"] == "carol"
