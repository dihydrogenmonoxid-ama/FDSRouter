"""Actor threading and audit-log entries for job/queue mutations (Stufe 3)."""

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
    db.upsert_node("node-1", "testhost", "darwin", 8, 16384, True)
    return QueueManager(config, db, "node-1", _noop_broadcast, SystemState())


def _write_case(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.fds"
    path.write_text(f"&HEAD CHID='{name}' /\n&MESH IJK=2,2,2, XB=0,1,0,1,0,1 /\n&TIME T_END=1.0 /\n")
    return path


def test_enqueue_records_creator_project_and_audit_entry(manager, tmp_path):
    async def scenario():
        case = _write_case(tmp_path, "a")
        job = await manager.enqueue(name="a", fds_file_path=case, actor="alice", project="Atrium")
        assert job["created_by"] == "alice"
        assert job["project"] == "Atrium"

        entries = manager.db.get_audit_entries(job_id=job["id"])
        assert len(entries) == 1
        assert entries[0]["action"] == "job_create"
        assert entries[0]["username"] == "alice"
        return job

    asyncio.run(scenario())


def test_cancel_logs_the_actor(manager, tmp_path):
    async def scenario():
        case = _write_case(tmp_path, "b")
        job = await manager.enqueue(name="b", fds_file_path=case)
        assert await manager.cancel(job["id"], actor="bob") is True

        entries = manager.db.get_audit_entries(job_id=job["id"])
        assert any(e["action"] == "job_cancel" and e["username"] == "bob" for e in entries)

    asyncio.run(scenario())


def test_reorder_and_archive_log_without_a_job_id(manager, tmp_path):
    async def scenario():
        case = _write_case(tmp_path, "c")
        job = await manager.enqueue(name="c", fds_file_path=case)
        await manager.reorder([job["id"]], actor="carol")
        manager.db.finish_job(job["id"], "done")
        assert await manager.archive_finished(actor="carol") == 1

        entries = manager.db.get_audit_entries()
        actions = [e["action"] for e in entries]
        assert "queue_reorder" in actions
        assert "job_archive" in actions

    asyncio.run(scenario())


def test_run_finishing_on_its_own_logs_with_no_actor(manager, tmp_path):
    """A job reaching a terminal state by itself (not stopped/cancelled by a user) is still
    audit-logged, but with username=None -- distinguishing "the system" from "nobody logged"."""
    manager.db.upsert_node("node-1", "testhost", "darwin", 8, 16384, True)
    job = manager.db.create_job(
        name="d", fds_file_path="/tmp/d.fds", node_id="node-1", mesh_cell_count=8,
        sim_end_time_s=1.0, mpi_process_count=1, estimated_duration_s=None,
    )
    manager.db.start_job(job["id"], pid=99999)
    manager.db.finish_job(job["id"], "failed", exit_message="boom")
    manager.db.insert_audit_entry(None, "job_finish", job_id=job["id"], detail="failed")

    entries = manager.db.get_audit_entries(job_id=job["id"])
    assert entries[0]["action"] == "job_finish"
    assert entries[0]["username"] is None
    assert entries[0]["detail"] == "failed"
