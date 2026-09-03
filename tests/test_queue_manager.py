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


def _finish(db, job, status="done"):
    db.start_job(job["id"], pid=1234)
    db.finish_job(job["id"], status)
    return job


def test_archiving_hides_finished_runs_but_keeps_queued_and_running(db):
    done = _finish(db, _make_job(db, "done"))
    queued = _make_job(db, "queued")
    running = _make_job(db, "running")
    db.start_job(running["id"], pid=99)

    assert db.archive_finished_jobs() == 1

    visible = {j["id"] for j in db.get_jobs()}
    assert done["id"] not in visible
    assert {queued["id"], running["id"]} <= visible
    assert [j["id"] for j in db.get_archived_jobs()] == [done["id"]]


def test_archiving_covers_failed_and_cancelled_runs(db):
    _finish(db, _make_job(db, "a"), "failed")
    _finish(db, _make_job(db, "b"), "cancelled")

    assert db.archive_finished_jobs() == 2
    assert db.get_jobs() == []


def test_archiving_twice_does_not_rearchive(db):
    _finish(db, _make_job(db, "a"))

    assert db.archive_finished_jobs() == 1
    assert db.archive_finished_jobs() == 0


def test_archived_runs_still_calibrate_the_estimate(db):
    job = _finish(db, _make_job(db, "a"))
    db.archive_finished_jobs()

    # Archiving is a view concern -- the measurement stays available to the estimator.
    history = db.get_completed_jobs_for_node("node-1")
    assert [j["id"] for j in history] == [job["id"]]


def test_include_archived_returns_everything(db):
    _finish(db, _make_job(db, "a"))
    db.archive_finished_jobs()

    assert db.get_jobs() == []
    assert len(db.get_jobs(include_archived=True)) == 1


def test_opening_a_database_without_archived_at_migrates_it(tmp_path):
    import sqlite3

    # A database created before the archive feature: same schema, minus the new column.
    db_path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        "CREATE TABLE job (id TEXT PRIMARY KEY, name TEXT NOT NULL, fds_file_path TEXT NOT NULL, "
        "case_hash TEXT, node_id TEXT, status TEXT NOT NULL DEFAULT 'queued', queue_position INTEGER, "
        "priority INTEGER NOT NULL DEFAULT 0, mesh_cell_count INTEGER, sim_end_time_s REAL, "
        "mpi_process_count INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, started_at TEXT, "
        "finished_at TEXT, estimated_duration_s REAL, actual_duration_s REAL, pid INTEGER, "
        "exit_message TEXT, energy_kwh REAL, energy_cost_eur REAL)"
    )
    legacy.execute(
        "INSERT INTO job (id, name, fds_file_path, status, created_at) "
        "VALUES ('old', 'Altlauf', '/cases/old.fds', 'done', '2026-01-01T00:00:00+00:00')"
    )
    legacy.commit()
    legacy.close()

    database = Database(db_path)

    # The existing row survives and the new column is usable.
    assert [j["id"] for j in database.get_jobs()] == ["old"]
    assert database.archive_finished_jobs() == 1
    assert [j["id"] for j in database.get_archived_jobs()] == ["old"]
