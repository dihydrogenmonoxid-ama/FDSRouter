import pytest

from fdsrouter.db.database import Database


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    database.upsert_node("node-1", "testhost", "darwin", 8, 16384)
    return database


def _make_job(db, name):
    return db.create_job(
        name=name,
        fds_file_path=f"/cases/{name}.fds",
        node_id="node-1",
        mesh_cell_count=1000,
        sim_end_time_s=60.0,
        mpi_process_count=1,
        estimated_duration_s=10.0,
    )


def test_new_jobs_get_increasing_queue_positions(db):
    j1 = _make_job(db, "a")
    j2 = _make_job(db, "b")
    assert j1["queue_position"] == 0
    assert j2["queue_position"] == 1


def test_reorder_changes_positions(db):
    j1 = _make_job(db, "a")
    j2 = _make_job(db, "b")
    j3 = _make_job(db, "c")

    db.reorder_queue("node-1", [j3["id"], j1["id"], j2["id"]])

    jobs = {j["id"]: j for j in db.get_jobs(statuses=["queued"])}
    assert jobs[j3["id"]]["queue_position"] == 0
    assert jobs[j1["id"]]["queue_position"] == 1
    assert jobs[j2["id"]]["queue_position"] == 2


def test_running_job_is_excluded_from_reorderable_set(db):
    j1 = _make_job(db, "a")
    j2 = _make_job(db, "b")
    db.start_job(j1["id"], pid=12345)

    # j1 is running now -- it must not be accepted as part of the reorder set.
    with pytest.raises(ValueError):
        db.reorder_queue("node-1", [j1["id"], j2["id"]])

    # Reordering just the still-queued jobs works fine and leaves the running job alone.
    db.reorder_queue("node-1", [j2["id"]])
    assert db.get_job(j1["id"])["status"] == "running"
    assert db.get_job(j1["id"])["queue_position"] is None


def test_reorder_rejects_unknown_or_missing_ids(db):
    j1 = _make_job(db, "a")
    _make_job(db, "b")

    with pytest.raises(ValueError):
        db.reorder_queue("node-1", [j1["id"]])  # missing j2

    with pytest.raises(ValueError):
        db.reorder_queue("node-1", [j1["id"], "does-not-exist"])


def test_cancel_only_affects_queued_jobs(db):
    j1 = _make_job(db, "a")
    db.start_job(j1["id"], pid=1)
    assert db.cancel_queued_job(j1["id"]) is False

    j2 = _make_job(db, "b")
    assert db.cancel_queued_job(j2["id"]) is True
    assert db.get_job(j2["id"])["status"] == "cancelled"


def test_cancel_does_not_affect_done_jobs(db):
    j1 = _make_job(db, "a")
    db.start_job(j1["id"], pid=1)
    db.finish_job(j1["id"], "done")

    assert db.cancel_queued_job(j1["id"]) is False
    assert db.get_job(j1["id"])["status"] == "done"


def test_get_next_queued_job_respects_position(db):
    j1 = _make_job(db, "a")
    j2 = _make_job(db, "b")
    db.reorder_queue("node-1", [j2["id"], j1["id"]])
    nxt = db.get_next_queued_job("node-1")
    assert nxt["id"] == j2["id"]
