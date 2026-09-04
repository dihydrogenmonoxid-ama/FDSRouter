"""Multi-node scheduling support added to Database (Phase 0 of the cluster feature)."""

import pytest

from fdsrouter.db.database import Database


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    database.upsert_node("node-1", "host-1", "linux", 8, 16384)
    database.upsert_node("node-2", "host-2", "darwin", 4, 8192)
    return database


def _make_job(db, name, node_id=None, mpi=1):
    return db.create_job(
        name=name,
        fds_file_path=f"/cases/{name}.fds",
        node_id=node_id,
        mesh_cell_count=1000,
        sim_end_time_s=60.0,
        mpi_process_count=mpi,
        estimated_duration_s=10.0,
    )


def test_get_completed_jobs_ignores_node(db):
    j = _make_job(db, "a", node_id="node-1")
    db.finish_job(j["id"], "done")
    db.conn.execute("UPDATE job SET actual_duration_s=5.0 WHERE id=?", (j["id"],))

    completed = db.get_completed_jobs()
    assert [c["id"] for c in completed] == [j["id"]]


def test_get_unassigned_queued_jobs_orders_by_position(db):
    j1 = _make_job(db, "a")
    j2 = _make_job(db, "b")
    _make_job(db, "c", node_id="node-1")  # already assigned -- must not show up

    unassigned = db.get_unassigned_queued_jobs()
    assert [j["id"] for j in unassigned] == [j1["id"], j2["id"]]


def test_get_busy_node_ids_excludes_queued_and_done(db):
    running = _make_job(db, "running", node_id="node-1")
    db.start_job(running["id"], pid=1)
    queued = _make_job(db, "queued", node_id="node-2")
    done = _make_job(db, "done", node_id="node-2")
    db.finish_job(done["id"], "done")

    assert db.get_busy_node_ids() == {"node-1"}


def test_assign_job_to_node_is_idempotent(db):
    job = _make_job(db, "a")
    db.assign_job_to_node(job["id"], "node-1")
    db.assign_job_to_node(job["id"], "node-2")  # second call must not steal the assignment

    assert db.get_job(job["id"])["node_id"] == "node-1"


def test_assign_job_to_node_is_a_no_op_once_running(db):
    job = _make_job(db, "a", node_id="node-1")
    db.start_job(job["id"], pid=1)

    db.assign_job_to_node(job["id"], "node-2")

    assert db.get_job(job["id"])["node_id"] == "node-1"


def test_set_stop_requested_only_affects_running_jobs(db):
    queued = _make_job(db, "queued")
    running = _make_job(db, "running", node_id="node-1")
    db.start_job(running["id"], pid=1)

    db.set_stop_requested(queued["id"])
    db.set_stop_requested(running["id"])

    assert db.get_job(queued["id"])["stop_requested_at"] is None
    assert db.get_job(running["id"])["stop_requested_at"] is not None


def test_get_running_jobs_with_stale_node_respects_threshold(db):
    job = _make_job(db, "a", node_id="node-1")
    db.start_job(job["id"], pid=1)

    # Fresh heartbeat (set by the db fixture's upsert_node) -- not stale yet.
    assert db.get_running_jobs_with_stale_node(threshold_s=3600) == []

    db.conn.execute("UPDATE node SET last_heartbeat='2000-01-01T00:00:00+00:00' WHERE id='node-1'")
    stale = db.get_running_jobs_with_stale_node(threshold_s=3600)
    assert [j["id"] for j in stale] == [job["id"]]


def test_get_running_jobs_with_stale_node_ignores_other_statuses(db):
    _make_job(db, "queued", node_id="node-1")
    db.conn.execute("UPDATE node SET last_heartbeat='2000-01-01T00:00:00+00:00' WHERE id='node-1'")

    assert db.get_running_jobs_with_stale_node(threshold_s=1) == []


def test_set_scheduled_stop_and_clear_it(db):
    job = _make_job(db, "a")
    db.set_scheduled_stop(job["id"], "2026-01-01T00:00:00+00:00")
    assert db.get_job(job["id"])["scheduled_stop_at"] == "2026-01-01T00:00:00+00:00"

    db.set_scheduled_stop(job["id"], None)
    assert db.get_job(job["id"])["scheduled_stop_at"] is None


def test_set_scheduled_stop_ignores_finished_jobs(db):
    job = _make_job(db, "a", node_id="node-1")
    db.finish_job(job["id"], "done")

    db.set_scheduled_stop(job["id"], "2026-01-01T00:00:00+00:00")

    assert db.get_job(job["id"])["scheduled_stop_at"] is None


def test_get_jobs_past_scheduled_stop(db):
    past = _make_job(db, "past")
    db.set_scheduled_stop(past["id"], "2000-01-01T00:00:00+00:00")
    future = _make_job(db, "future")
    db.set_scheduled_stop(future["id"], "2100-01-01T00:00:00+00:00")
    unset = _make_job(db, "unset")

    due = db.get_jobs_past_scheduled_stop("2026-01-01T00:00:00+00:00")

    assert [j["id"] for j in due] == [past["id"]]


def test_get_jobs_past_scheduled_stop_covers_running_too(db):
    job = _make_job(db, "a", node_id="node-1")
    db.start_job(job["id"], pid=1)
    db.set_scheduled_stop(job["id"], "2000-01-01T00:00:00+00:00")

    due = db.get_jobs_past_scheduled_stop("2026-01-01T00:00:00+00:00")

    assert [j["id"] for j in due] == [job["id"]]
