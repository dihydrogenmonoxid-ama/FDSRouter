from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fdsrouter.api.app import create_app
from fdsrouter.config import Config


@pytest.fixture
def client(tmp_path):
    config = Config(project_dir=tmp_path)
    config.upload_dir = Path("cases")
    config.max_upload_mb = 1
    config.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(config)) as test_client:
        test_client.upload_root = config.resolved_upload_dir
        yield test_client


FDS_SOURCE = b"&HEAD CHID='atrium' /\n&MESH IJK=10,10,10, XB=0,1,0,1,0,1 /\n"


def test_upload_stores_the_case_and_reports_its_path(client):
    response = client.post(
        "/api/upload", files=[("files", ("atrium.fds", FDS_SOURCE, "application/octet-stream"))]
    )

    assert response.status_code == 200
    fds_path = Path(response.json()["fds_file_path"])
    assert fds_path.is_file()
    assert fds_path.read_bytes() == FDS_SOURCE
    # Everything must land under the configured upload directory.
    assert client.upload_root in fds_path.parents


def test_upload_keeps_auxiliary_case_files_next_to_the_input(client):
    response = client.post(
        "/api/upload",
        files=[
            ("files", ("atrium.fds", FDS_SOURCE, "application/octet-stream")),
            ("files", ("ramp.csv", b"t,v\n0,0\n", "text/csv")),
        ],
    )

    case_dir = Path(response.json()["case_dir"])
    assert sorted(f.name for f in case_dir.iterdir()) == ["atrium.fds", "ramp.csv"]


def test_upload_requires_exactly_one_fds_file(client):
    without = client.post("/api/upload", files=[("files", ("notes.txt", b"x", "text/plain"))])
    two = client.post(
        "/api/upload",
        files=[
            ("files", ("a.fds", FDS_SOURCE, "application/octet-stream")),
            ("files", ("b.fds", FDS_SOURCE, "application/octet-stream")),
        ],
    )

    assert without.status_code == 400
    assert two.status_code == 400


def test_upload_filename_cannot_escape_the_case_directory(client):
    response = client.post(
        "/api/upload",
        files=[
            ("files", ("atrium.fds", FDS_SOURCE, "application/octet-stream")),
            ("files", ("../../escaped.txt", b"x", "text/plain")),
        ],
    )

    case_dir = Path(response.json()["case_dir"])
    assert sorted(f.name for f in case_dir.iterdir()) == ["atrium.fds", "escaped.txt"]
    assert not (client.upload_root.parent / "escaped.txt").exists()


def test_oversized_upload_is_rejected_and_leaves_nothing_behind(client):
    oversized = b"x" * (2 * 1024 * 1024)  # max_upload_mb is 1 in this fixture

    response = client.post(
        "/api/upload",
        files=[("files", ("atrium.fds", FDS_SOURCE + oversized, "application/octet-stream"))],
    )

    assert response.status_code == 413
    assert list(client.upload_root.iterdir()) == []
