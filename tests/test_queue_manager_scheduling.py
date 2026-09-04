"""QueueManager's scheduler tick and stale-remote-node sweep -- the multi-node glue between the
pure fdsrouter.core.scheduler module and the actual dispatch loop."""

import asyncio
from pathlib import Path

import pytest

from fdsrouter.config import Config
from fdsrouter.core.queue_manager import QueueManager
from fdsrouter.core.system_monitor import SystemState
from fdsrouter.db.database import Database


async def _noop_broadcast(message):
    pass


@pytest.fixture
def manager(tmp_path):
    config = Config(project_dir=tmp_path, data_dir=tmp_path)
    db = Database(tmp_path / "test.db")
    db.upsert_node("node-1", "local-host", "darwin", 4, 8192, True)
    return QueueManager(config, db, "node-1", _noop_broadcast, SystemState())


def _write_case(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.fds"
    path.write_text(f"&HEAD CHID='{name}' /\n&MESH IJK=2,2,2, XB=0,1,0,1,0,1 /\n&TIME T_END=1.0 /\n")
    return path


def test_solo_install_assigns_a_fresh_job_to_the_local_node(manager, tmp_path):
    async def scenario():
        case = _write_case(tmp_path, "a")
        job = await manager.enqueue(name="a", fds_file_path=case)
        assert job["node_id"] is None  # not pinned at creation time anymore

        await manager._run_scheduler_tick()

        assert manager.db.get_job(job["id"])["node_id"] == "node-1"

    asyncio.run(scenario())


def test_job_too_big_for_local_node_goes_to_a_bigger_remote_node(manager, tmp_path):
    async def scenario():
        manager.db.upsert_node("node-2", "big-host", "linux", 16, 65536, True)
        case = _write_case(tmp_path, "big")
        job = await manager.enqueue(name="big", fds_file_path=case, mpi_processes=1)
        # Force a process count the local 4-core node can't take.
        manager.db.conn.execute("UPDATE job SET mpi_process_count=8 WHERE id=?", (job["id"],))

        await manager._run_scheduler_tick()

        assert manager.db.get_job(job["id"])["node_id"] == "node-2"

    asyncio.run(scenario())


def test_busy_node_is_skipped_in_favour_of_an_idle_one(manager, tmp_path):
    async def scenario():
        manager.db.upsert_node("node-2", "idle-host", "linux", 4, 8192, True)
        busy_case = _write_case(tmp_path, "busy")
        busy_job = await manager.enqueue(name="busy", fds_file_path=busy_case)
        manager.db.assign_job_to_node(busy_job["id"], "node-1")
        manager.db.start_job(busy_job["id"], pid=1)

        case = _write_case(tmp_path, "new")
        job = await manager.enqueue(name="new", fds_file_path=case)

        await manager._run_scheduler_tick()

        assert manager.db.get_job(job["id"])["node_id"] == "node-2"

    asyncio.run(scenario())


def test_no_eligible_node_leaves_the_job_unassigned(manager, tmp_path):
    async def scenario():
        case = _write_case(tmp_path, "huge")
        job = await manager.enqueue(name="huge", fds_file_path=case)
        manager.db.conn.execute("UPDATE job SET mpi_process_count=99 WHERE id=?", (job["id"],))

        await manager._run_scheduler_tick()

        assert manager.db.get_job(job["id"])["node_id"] is None

    asyncio.run(scenario())


def test_stale_sweep_finalizes_a_hung_remote_job_as_failed(manager, tmp_path):
    async def scenario():
        manager.db.upsert_node("node-2", "dead-host", "linux", 4, 8192, True)
        case = _write_case(tmp_path, "remote")
        job = await manager.enqueue(name="remote", fds_file_path=case)
        manager.db.assign_job_to_node(job["id"], "node-2")
        manager.db.start_job(job["id"], pid=1)
        manager.db.conn.execute("UPDATE node SET last_heartbeat='2000-01-01T00:00:00+00:00' WHERE id='node-2'")

        await manager._sweep_stale_remote_jobs()

        finished = manager.db.get_job(job["id"])
        assert finished["status"] == "failed"
        assert "Heartbeat" in finished["exit_message"]

    asyncio.run(scenario())


def test_manual_start_on_a_remote_job_marks_it_requested_instead_of_running_it_locally(manager, tmp_path):
    """Starting a job assigned to a remote node can't run it directly -- there's no channel to
    that agent besides its own poll -- so this must only flag it, never spawn a local process."""

    async def scenario():
        manager.db.upsert_node("node-2", "remote-host", "linux", 8, 16384, True)
        case = _write_case(tmp_path, "remote")
        job = await manager.enqueue(name="remote", fds_file_path=case)
        manager.db.assign_job_to_node(job["id"], "node-2")

        ok = await manager.start_job_manually(job["id"])

        assert ok is True
        after = manager.db.get_job(job["id"])
        assert after["status"] == "queued"  # not started locally
        assert after["start_requested_at"] is not None
        assert manager._running is None

    asyncio.run(scenario())


def test_assignment_gate_respects_start_requested_for_the_local_dispatch_loop_too(manager, tmp_path):
    async def scenario():
        case = _write_case(tmp_path, "local")
        job = await manager.enqueue(name="local", fds_file_path=case)
        manager.db.assign_job_to_node(job["id"], "node-1")

        # auto_advance is off and nothing requested yet -- the dispatch condition must not fire.
        next_job = manager.db.get_next_queued_job("node-1")
        assert not (manager.auto_advance or next_job.get("start_requested_at"))

        manager.db.set_start_requested(job["id"])
        next_job = manager.db.get_next_queued_job("node-1")
        assert manager.auto_advance or next_job.get("start_requested_at")

    asyncio.run(scenario())


def test_stale_sweep_never_touches_the_local_nodes_own_running_job(manager, tmp_path):
    async def scenario():
        case = _write_case(tmp_path, "local")
        job = await manager.enqueue(name="local", fds_file_path=case)
        manager.db.assign_job_to_node(job["id"], "node-1")
        manager.db.start_job(job["id"], pid=1)
        # Simulate a local heartbeat that hasn't been refreshed in a while -- must still be left
        # alone, since a node never distrusts its own liveness.
        manager.db.conn.execute("UPDATE node SET last_heartbeat='2000-01-01T00:00:00+00:00' WHERE id='node-1'")

        await manager._sweep_stale_remote_jobs()

        assert manager.db.get_job(job["id"])["status"] == "running"

    asyncio.run(scenario())
