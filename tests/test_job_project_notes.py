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


def _write_case(tmp_path, name="case") -> str:
    path = tmp_path / f"{name}.fds"
    path.write_text(f"&HEAD CHID='{name}' /\n&MESH IJK=2,2,2, XB=0,1,0,1,0,1 /\n&TIME T_END=1.0 /\n")
    return str(path)


def test_create_job_accepts_a_project_label(client, tmp_path):
    fds_path = _write_case(tmp_path)
    response = client.post("/api/jobs", json={"fds_file_path": fds_path, "project": "Atrium"})
    assert response.status_code == 200
    assert response.json()["project"] == "Atrium"


def test_patch_updates_project_and_notes_and_logs_it(client, tmp_path):
    fds_path = _write_case(tmp_path)
    job = client.post("/api/jobs", json={"fds_file_path": fds_path}).json()

    response = client.patch(f"/api/jobs/{job['id']}", json={"project": "Atrium", "notes": "Brandlast 2 MW"})
    assert response.status_code == 200
    body = response.json()
    assert body["project"] == "Atrium"
    assert body["notes"] == "Brandlast 2 MW"

    audit = client.get(f"/api/jobs/{job['id']}/audit").json()["entries"]
    assert any(e["action"] == "job_edit" for e in audit)


def test_patch_leaves_a_field_unset_when_omitted(client, tmp_path):
    fds_path = _write_case(tmp_path)
    job = client.post("/api/jobs", json={"fds_file_path": fds_path, "project": "Atrium"}).json()

    response = client.patch(f"/api/jobs/{job['id']}", json={"notes": "erste Notiz"})
    assert response.json()["project"] == "Atrium"
    assert response.json()["notes"] == "erste Notiz"


def test_patch_unknown_job_is_404(client):
    response = client.patch("/api/jobs/does-not-exist", json={"notes": "x"})
    assert response.status_code == 404


def test_create_job_accepts_a_scheduled_stop(client, tmp_path):
    fds_path = _write_case(tmp_path)
    response = client.post(
        "/api/jobs", json={"fds_file_path": fds_path, "scheduled_stop_at": "2100-01-01T00:00:00+00:00"}
    )
    assert response.status_code == 200
    assert response.json()["scheduled_stop_at"] == "2100-01-01T00:00:00+00:00"


def test_patch_sets_and_clears_the_scheduled_stop(client, tmp_path):
    fds_path = _write_case(tmp_path)
    job = client.post("/api/jobs", json={"fds_file_path": fds_path}).json()

    set_resp = client.patch(f"/api/jobs/{job['id']}", json={"scheduled_stop_at": "2100-01-01T00:00:00+00:00"})
    assert set_resp.json()["scheduled_stop_at"] == "2100-01-01T00:00:00+00:00"

    # Explicit null must actually clear it -- unlike project/notes, omission and "please clear
    # this" are not the same request for a deadline.
    clear_resp = client.patch(f"/api/jobs/{job['id']}", json={"scheduled_stop_at": None})
    assert clear_resp.json()["scheduled_stop_at"] is None


def test_patch_without_scheduled_stop_key_leaves_it_untouched(client, tmp_path):
    fds_path = _write_case(tmp_path)
    job = client.post(
        "/api/jobs", json={"fds_file_path": fds_path, "scheduled_stop_at": "2100-01-01T00:00:00+00:00"}
    ).json()

    response = client.patch(f"/api/jobs/{job['id']}", json={"notes": "x"})

    assert response.json()["scheduled_stop_at"] == "2100-01-01T00:00:00+00:00"


def test_job_deep_link_serves_the_frontend_shell(client):
    response = client.get("/job/some-job-id")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
