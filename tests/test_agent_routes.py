import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fdsrouter.api.app import create_app
from fdsrouter.config import Config

TOKEN = "test-cluster-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path):
    config = Config(project_dir=tmp_path, cluster_token=TOKEN)
    config.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(config)) as test_client:
        test_client.data_dir = config.resolved_data_dir
        yield test_client


def test_agent_routes_require_the_cluster_token(client):
    assert client.post("/api/agent/register", json={}).status_code == 401
    assert client.post("/api/agent/register", headers={"Authorization": "Bearer nope"}, json={}).status_code == 401


def test_register_and_heartbeat(client):
    payload = {"id": "remote-1", "hostname": "compute1", "os": "linux", "cpu_cores": 16, "ram_total_mb": 65536}
    assert client.post("/api/agent/register", headers=AUTH, json=payload).status_code == 200
    assert client.post("/api/agent/remote-1/heartbeat", headers=AUTH).status_code == 200

    nodes = {n["id"]: n for n in client.get("/api/nodes").json()}
    assert "remote-1" in nodes
    assert nodes["remote-1"]["hostname"] == "compute1"


def _register(client, node_id="remote-1", cores=16):
    client.post(
        "/api/agent/register",
        headers=AUTH,
        json={"id": node_id, "hostname": "compute1", "os": "linux", "cpu_cores": cores, "ram_total_mb": 65536},
    )


def test_full_agent_lifecycle(client, tmp_path):
    _register(client)

    # A case dir with an .fds file and one auxiliary input, as if uploaded through the browser.
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    fds_file = case_dir / "demo.fds"
    fds_file.write_text("&HEAD CHID='demo' /\n&MESH IJK=2,2,2, XB=0,1,0,1,0,1 /\n&TIME T_END=1.0 /\n")
    (case_dir / "aux_ramp.csv").write_text("t,v\n0,0\n")

    job = client.post("/api/jobs", json={"fds_file_path": str(fds_file)}).json()
    job_id = job["id"]
    # Bypass the scheduler for test determinism -- directly assign to our agent.
    client.app.state.db.assign_job_to_node(job_id, "remote-1")
    # Assignment alone isn't permission to start -- same auto_advance gate a local job respects.
    client.post("/api/queue/auto-advance", json={"enabled": True})

    assignment = client.get("/api/agent/remote-1/assignment", headers=AUTH).json()
    assert assignment["job"]["id"] == job_id

    case_zip = client.get(f"/api/agent/jobs/{job_id}/case-files", headers=AUTH)
    assert case_zip.status_code == 200
    with zipfile.ZipFile(io.BytesIO(case_zip.content)) as archive:
        names = set(archive.namelist())
    assert {"demo.fds", "aux_ramp.csv"} <= names

    assert client.post(f"/api/agent/jobs/{job_id}/start", headers=AUTH, json={"pid": 4242}).status_code == 200
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "running"

    metrics_resp = client.post(
        f"/api/agent/jobs/{job_id}/metrics",
        headers=AUTH,
        json={
            "processes": [{"pid": 4242, "cpu_percent": 50.0, "ram_percent": 5.0, "core": 0}],
            "devices": [],
            "out": {"step_number": 1, "step_size_s": 0.01, "simulation_time_s": 0.5, "total_hrr_kw": 100.0, "warnings_count": 0},
            "cpu_percent_total": 25.0,
        },
    )
    assert metrics_resp.json() == {"stop": False}
    stored = client.get(f"/api/jobs/{job_id}/metrics").json()
    assert stored["out_file_metrics"][0]["total_hrr_kw"] == 100.0

    # Requesting a stop is picked up on the *next* metrics report.
    assert client.post(f"/api/jobs/{job_id}/stop", json={}).status_code == 200
    stop_check = client.post(
        f"/api/agent/jobs/{job_id}/metrics", headers=AUTH, json={"processes": [], "devices": [], "out": None}
    )
    assert stop_check.json() == {"stop": True}

    log_resp = client.post(f"/api/agent/jobs/{job_id}/log", headers=AUTH, json={"lines": ["line one", "line two"]})
    assert log_resp.status_code == 200
    log_path = client.data_dir / "logs" / f"{job_id}.log"
    assert log_path.read_text() == "line one\nline two\n"

    # Upload results back -- extracted into the job's own (Controller-side) case directory.
    results_buf = io.BytesIO()
    with zipfile.ZipFile(results_buf, "w") as archive:
        archive.writestr("demo.out", "some output")
        archive.writestr("demo_hrr.csv", "Time,HRR\n0,0\n")
    results_buf.seek(0)
    upload_resp = client.post(
        f"/api/agent/jobs/{job_id}/results",
        headers=AUTH,
        files={"archive": ("results.zip", results_buf, "application/zip")},
    )
    assert upload_resp.status_code == 200
    assert upload_resp.json()["files"] == 2
    assert (case_dir / "demo.out").read_text() == "some output"

    finish_resp = client.post(
        f"/api/agent/jobs/{job_id}/finish",
        headers=AUTH,
        json={"status": "done", "exit_message": None},
    )
    assert finish_resp.status_code == 200
    finished = client.get(f"/api/jobs/{job_id}").json()
    assert finished["status"] == "done"

    audit = client.get(f"/api/jobs/{job_id}/audit").json()["entries"]
    assert any(e["action"] == "job_finish" for e in audit)


def test_assignment_withholds_a_job_until_permitted_to_start(client, tmp_path):
    """A job the scheduler assigned to a node isn't automatically permission to run it -- same
    guarantee a local job gets from auto_advance/manual Start."""
    _register(client)
    fds_file = tmp_path / "demo.fds"
    fds_file.write_text("&HEAD CHID='demo' /\n&MESH IJK=2,2,2, XB=0,1,0,1,0,1 /\n&TIME T_END=1.0 /\n")
    job = client.post("/api/jobs", json={"fds_file_path": str(fds_file)}).json()
    client.app.state.db.assign_job_to_node(job["id"], "remote-1")

    withheld = client.get("/api/agent/remote-1/assignment", headers=AUTH).json()
    assert withheld == {"job": None}

    # Explicit per-job "Start" (mirrors clicking Start in the UI) is enough on its own, even
    # with auto_advance still off.
    assert client.post(f"/api/jobs/{job['id']}/start").status_code == 200
    permitted = client.get("/api/agent/remote-1/assignment", headers=AUTH).json()
    assert permitted["job"]["id"] == job["id"]


def test_unknown_job_is_404_on_agent_routes(client):
    _register(client)
    assert client.get("/api/agent/jobs/does-not-exist/case-files", headers=AUTH).status_code == 404
    assert client.post("/api/agent/jobs/does-not-exist/start", headers=AUTH, json={}).status_code == 404
    assert (
        client.post(
            "/api/agent/jobs/does-not-exist/metrics", headers=AUTH, json={"processes": [], "devices": [], "out": None}
        ).status_code
        == 404
    )
