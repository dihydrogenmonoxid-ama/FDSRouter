"""QueueManager.enqueue: MPI-process defaulting/validation against the parsed mesh count."""

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


def _write_case(tmp_path: Path, name: str, mesh_count: int) -> Path:
    path = tmp_path / f"{name}.fds"
    meshes = "\n".join(f"&MESH IJK=2,2,2, XB=0,1,0,1,0,1 /" for _ in range(mesh_count))
    path.write_text(f"&HEAD CHID='{name}' /\n{meshes}\n&TIME T_END=1.0 /\n")
    return path


def test_enqueue_defaults_mpi_processes_to_mesh_count(manager, tmp_path):
    case = _write_case(tmp_path, "three_mesh", 3)
    job = asyncio.run(manager.enqueue(name="three_mesh", fds_file_path=case))
    assert job["mpi_process_count"] == 3


def test_enqueue_rejects_more_processes_than_meshes(manager, tmp_path):
    case = _write_case(tmp_path, "two_mesh", 2)
    with pytest.raises(ValueError, match="MPI-Prozesse"):
        asyncio.run(manager.enqueue(name="two_mesh", fds_file_path=case, mpi_processes=5))


def test_enqueue_allows_fewer_processes_than_meshes(manager, tmp_path):
    case = _write_case(tmp_path, "four_mesh", 4)
    job = asyncio.run(manager.enqueue(name="four_mesh", fds_file_path=case, mpi_processes=2))
    assert job["mpi_process_count"] == 2


def test_enqueue_falls_back_to_config_default_when_no_mesh_found(manager, tmp_path):
    manager.config.default_mpi_processes = 4
    case = tmp_path / "no_mesh.fds"
    case.write_text("&HEAD CHID='no_mesh' /\n&TIME T_END=1.0 /\n")
    job = asyncio.run(manager.enqueue(name="no_mesh", fds_file_path=case))
    assert job["mpi_process_count"] == 4
