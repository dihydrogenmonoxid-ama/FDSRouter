"""A job's operator-chosen deadline: cancelled if still queued, stopped if already running,
regardless of node (see QueueManager._enforce_scheduled_stops)."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from fdsrouter.config import Config
from fdsrouter.core import job_runner
from fdsrouter.core.queue_manager import QueueManager
from fdsrouter.core.system_monitor import SystemState
from fdsrouter.db.database import Database

PAST = "2000-01-01T00:00:00+00:00"
FUTURE = "2100-01-01T00:00:00+00:00"


async def _noop_broadcast(message):
    pass


@pytest.fixture
def manager(tmp_path):
    config = Config(project_dir=tmp_path, data_dir=tmp_path)
    db = Database(tmp_path / "test.db")
    db.upsert_node("node-1", "local-host", "darwin", 4, 8192)
    db.upsert_node("node-2", "remote-host", "linux", 8, 16384)
    return QueueManager(config, db, "node-1", _noop_broadcast, SystemState())


def _write_case(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.fds"
    path.write_text(f"&HEAD CHID='{name}' /\n&MESH IJK=2,2,2, XB=0,1,0,1,0,1 /\n&TIME T_END=1.0 /\n")
    return path


def test_queued_job_past_deadline_is_cancelled(manager, tmp_path):
    async def scenario():
        case = _write_case(tmp_path, "a")
        job = await manager.enqueue(name="a", fds_file_path=case, scheduled_stop_at=PAST)

        await manager._enforce_scheduled_stops()

        assert manager.db.get_job(job["id"])["status"] == "cancelled"

    asyncio.run(scenario())


def test_queued_job_with_a_future_deadline_is_left_alone(manager, tmp_path):
    async def scenario():
        case = _write_case(tmp_path, "a")
        job = await manager.enqueue(name="a", fds_file_path=case, scheduled_stop_at=FUTURE)

        await manager._enforce_scheduled_stops()

        assert manager.db.get_job(job["id"])["status"] == "queued"

    asyncio.run(scenario())


def test_remote_running_job_past_deadline_gets_a_stop_request(manager, tmp_path):
    async def scenario():
        case = _write_case(tmp_path, "a")
        job = await manager.enqueue(name="a", fds_file_path=case)
        manager.db.assign_job_to_node(job["id"], "node-2")
        manager.db.start_job(job["id"], pid=1)
        manager.db.set_scheduled_stop(job["id"], PAST)

        await manager._enforce_scheduled_stops()

        assert manager.db.get_job(job["id"])["stop_requested_at"] is not None
        assert manager.db.get_job(job["id"])["status"] == "running"  # the agent still has to act on it

    asyncio.run(scenario())


@dataclass
class _FakeProcess:
    terminated: bool = False


def test_local_running_job_past_deadline_is_terminated(manager, tmp_path, monkeypatch):
    async def scenario():
        case = _write_case(tmp_path, "a")
        job = await manager.enqueue(name="a", fds_file_path=case)
        manager.db.assign_job_to_node(job["id"], "node-1")
        manager.db.start_job(job["id"], pid=1)

        fake_process = _FakeProcess()

        async def fake_terminate(process, grace_period_s=15.0):
            process.terminated = True

        monkeypatch.setattr(job_runner, "terminate", fake_terminate)
        manager._running = type("R", (), {"process": fake_process})()
        manager._running_job_id = job["id"]
        manager.db.set_scheduled_stop(job["id"], PAST)

        await manager._enforce_scheduled_stops()
        await asyncio.sleep(0)  # let the terminate task created inside stop_running_job run

        assert manager._stop_requested is True
        assert fake_process.terminated is True

    asyncio.run(scenario())


def test_job_finish_audit_entry_after_deadline(manager, tmp_path):
    async def scenario():
        case = _write_case(tmp_path, "a")
        job = await manager.enqueue(name="a", fds_file_path=case, scheduled_stop_at=PAST)

        await manager._enforce_scheduled_stops()

        entries = manager.db.get_audit_entries(job_id=job["id"])
        assert any(e["action"] == "job_cancel" for e in entries)

    asyncio.run(scenario())
