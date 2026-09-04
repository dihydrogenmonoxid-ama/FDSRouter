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


def test_list_nodes_reports_online_status(client):
    client.app.state.db.upsert_node("stale-node", "old-host", "linux", 4, 8192)
    client.app.state.db.conn.execute(
        "UPDATE node SET last_heartbeat='2000-01-01T00:00:00+00:00' WHERE id='stale-node'"
    )

    nodes = {n["id"]: n for n in client.get("/api/nodes").json()}

    # The local node heartbeats itself continuously (queue_manager's dispatch loop) -- but this
    # TestClient's lifespan only just started it, so just check the field is present and correct
    # for the node we control directly.
    assert nodes["stale-node"]["online"] is False
