"""The real Agent driven against a real Controller app, in-process (httpx.ASGITransport, no
socket) -- exercises the actual HTTP contract between the two, not just each side in isolation."""

import asyncio
from pathlib import Path

import httpx
import pytest

from fdsrouter.agent import Agent, AgentConfig
from fdsrouter.api.app import create_app
from fdsrouter.config import Config

TOKEN = "test-cluster-token"


@pytest.fixture
def controller_config(tmp_path):
    config = Config(project_dir=tmp_path / "controller", cluster_token=TOKEN)
    config.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    return config


def _write_case(tmp_path: Path) -> Path:
    case_dir = tmp_path / "controller-case"
    case_dir.mkdir()
    fds_file = case_dir / "demo.fds"
    fds_file.write_text("&HEAD CHID='demo' /\n&MESH IJK=2,2,2, XB=0,1,0,1,0,1 /\n&TIME T_END=1.0 /\n")
    return fds_file


def test_agent_registers_and_runs_an_assigned_job(controller_config, tmp_path):
    async def scenario():
        app = create_app(controller_config)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)

            agent_config = AgentConfig(
                project_dir=tmp_path / "agent",
                controller_url="http://testserver",
                cluster_token=TOKEN,
                data_dir=tmp_path / "agent" / "data",
                fds_binary=None,  # unset on purpose -- must fail fast, but demonstrably attempted
                mpi_executable=None,
            )
            agent_config.resolved_data_dir.mkdir(parents=True, exist_ok=True)
            agent = Agent(agent_config, transport=transport)
            try:
                await agent.register()

                nodes = {n["id"]: n for n in app.state.db.get_nodes()}
                assert agent.node_id in nodes
                assert nodes[agent.node_id]["hostname"]

                fds_file = _write_case(tmp_path)
                job = app.state.db.create_job(
                    name="demo",
                    fds_file_path=str(fds_file),
                    node_id=None,
                    mesh_cell_count=8,
                    sim_end_time_s=1.0,
                    mpi_process_count=1,
                    estimated_duration_s=None,
                )
                app.state.db.assign_job_to_node(job["id"], agent.node_id)
                await app.state.queue_manager.set_auto_advance(True)

                await agent._check_for_assignment()

                finished = app.state.db.get_job(job["id"])
                # No fds_binary configured on the agent -- the run can't actually start, but the
                # whole round trip (assignment, case-file download, failure report back) happened.
                assert finished["status"] == "failed"
                audit = app.state.db.get_audit_entries(job_id=job["id"])
                assert any(e["action"] == "job_finish" for e in audit)
            finally:
                await agent.aclose()

    asyncio.run(scenario())


def test_load_agent_config_writes_and_reloads(tmp_path):
    from fdsrouter.agent import load_agent_config

    cfg = load_agent_config(tmp_path)
    assert cfg.controller_url == "http://127.0.0.1:8000"
    assert (tmp_path / "agent-config.yaml").exists()

    (tmp_path / "agent-config.yaml").write_text(
        "controller_url: http://10.0.0.5:8000\ncluster_token: abc123\n"
    )
    cfg2 = load_agent_config(tmp_path)
    assert cfg2.controller_url == "http://10.0.0.5:8000"
    assert cfg2.cluster_token == "abc123"


def test_save_agent_config_round_trips(tmp_path):
    from fdsrouter.agent import load_agent_config, save_agent_config

    cfg = load_agent_config(tmp_path)
    cfg.controller_url = "http://192.168.1.10:8000"
    cfg.cluster_token = "paired-token"

    save_agent_config(cfg)

    reloaded = load_agent_config(tmp_path)
    assert reloaded.controller_url == "http://192.168.1.10:8000"
    assert reloaded.cluster_token == "paired-token"


def test_register_raises_on_a_bad_token(controller_config, tmp_path):
    async def scenario():
        app = create_app(controller_config)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            agent_config = AgentConfig(
                project_dir=tmp_path / "agent",
                controller_url="http://testserver",
                cluster_token="wrong-token",
                data_dir=tmp_path / "agent" / "data",
            )
            agent_config.resolved_data_dir.mkdir(parents=True, exist_ok=True)
            agent = Agent(agent_config, transport=transport)
            try:
                with pytest.raises(httpx.HTTPStatusError):
                    await agent.register()
            finally:
                await agent.aclose()

    asyncio.run(scenario())
