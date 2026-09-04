"""Dispatcher gating: nothing auto-starts unless auto_advance is on; manual start otherwise."""

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
    # fds_binary/mpi_executable are left unset on purpose: any dispatch attempt fails fast
    # with a clear RuntimeError instead of trying to spawn a real process, which is exactly
    # what these tests need to observe ("did it attempt to start or not").
    config = Config(project_dir=tmp_path, data_dir=tmp_path)
    db = Database(tmp_path / "test.db")
    db.upsert_node("node-1", "testhost", "darwin", 8, 16384, True)
    return QueueManager(config, db, "node-1", _noop_broadcast, SystemState())


def _write_case(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.fds"
    path.write_text(f"&HEAD CHID='{name}' /\n&MESH IJK=2,2,2, XB=0,1,0,1,0,1 /\n&TIME T_END=1.0 /\n")
    return path


def test_auto_advance_defaults_to_off(manager):
    assert manager.auto_advance is False


def test_dispatch_loop_does_not_auto_start_when_auto_advance_off(manager, tmp_path):
    async def scenario():
        case = _write_case(tmp_path, "a")
        job = await manager.enqueue(name="a", fds_file_path=case)

        manager.start()
        await asyncio.sleep(1.3)
        try:
            assert manager.db.get_job(job["id"])["status"] == "queued"
        finally:
            await manager.stop()

    asyncio.run(scenario())


def test_dispatch_loop_auto_starts_once_enabled(manager, tmp_path):
    async def scenario():
        case = _write_case(tmp_path, "b")
        job = await manager.enqueue(name="b", fds_file_path=case)

        manager.start()
        await manager.set_auto_advance(True)
        await asyncio.sleep(1.3)
        try:
            # No real fds_binary configured -> the attempt fails fast, but it *was* attempted
            # (status left "queued" only when nothing tries to dispatch it at all).
            assert manager.db.get_job(job["id"])["status"] != "queued"
        finally:
            await manager.stop()

    asyncio.run(scenario())


def test_manual_start_requires_queued_status(manager, tmp_path):
    async def scenario():
        case = _write_case(tmp_path, "c")
        job = await manager.enqueue(name="c", fds_file_path=case)
        assert await manager.start_job_manually("does-not-exist") is False
        assert await manager.start_job_manually(job["id"]) is True
        # missing fds_binary -> fails fast, but the manual start was accepted and attempted
        assert manager.db.get_job(job["id"])["status"] == "failed"
        # a second manual start now correctly refuses (no queued job left with this id)
        assert await manager.start_job_manually(job["id"]) is False

    asyncio.run(scenario())
