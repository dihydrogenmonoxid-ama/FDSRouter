"""Pure scheduling decisions -- no DB, no asyncio."""

from datetime import datetime, timedelta, timezone

from fdsrouter.core import scheduler

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _node(id, cores=8, heartbeat_age_s=0, fds_ready=True):
    heartbeat = (NOW - timedelta(seconds=heartbeat_age_s)).isoformat()
    return {"id": id, "cpu_cores": cores, "last_heartbeat": heartbeat, "fds_ready": fds_ready}


def _job(mpi=4):
    return {"mpi_process_count": mpi}


def test_is_node_online_true_for_recent_heartbeat():
    assert scheduler.is_node_online(_node("n", heartbeat_age_s=5), NOW) is True


def test_is_node_online_false_once_stale():
    assert scheduler.is_node_online(_node("n", heartbeat_age_s=scheduler.NODE_STALE_AFTER_S + 1), NOW) is False


def test_is_node_online_false_with_no_heartbeat_yet():
    assert scheduler.is_node_online({"id": "n", "last_heartbeat": None}, NOW) is False


def test_picks_the_only_eligible_node():
    nodes = [_node("solo")]
    assert scheduler.pick_node_for_job(_job(), nodes, busy_node_ids=set(), now=NOW) == "solo"


def test_no_eligible_node_returns_none():
    assert scheduler.pick_node_for_job(_job(), [], busy_node_ids=set(), now=NOW) is None


def test_busy_node_is_excluded():
    nodes = [_node("busy")]
    assert scheduler.pick_node_for_job(_job(), nodes, busy_node_ids={"busy"}, now=NOW) is None


def test_node_with_too_few_cores_is_excluded():
    nodes = [_node("small", cores=2)]
    assert scheduler.pick_node_for_job(_job(mpi=4), nodes, busy_node_ids=set(), now=NOW) is None


def test_stale_node_is_excluded():
    nodes = [_node("dead", heartbeat_age_s=scheduler.NODE_STALE_AFTER_S + 10)]
    assert scheduler.pick_node_for_job(_job(), nodes, busy_node_ids=set(), now=NOW) is None


def test_tie_break_prefers_more_cores():
    nodes = [_node("small", cores=4), _node("big", cores=16)]
    assert scheduler.pick_node_for_job(_job(), nodes, busy_node_ids=set(), now=NOW) == "big"


def test_deterministic_tie_break_when_cores_equal():
    nodes = [_node("b", cores=8), _node("a", cores=8)]
    first = scheduler.pick_node_for_job(_job(), nodes, busy_node_ids=set(), now=NOW)
    second = scheduler.pick_node_for_job(_job(), list(reversed(nodes)), busy_node_ids=set(), now=NOW)
    assert first == second


def test_only_the_eligible_one_among_several_is_picked():
    nodes = [_node("busy", cores=16), _node("small", cores=1), _node("winner", cores=8)]
    result = scheduler.pick_node_for_job(_job(mpi=4), nodes, busy_node_ids={"busy"}, now=NOW)
    assert result == "winner"


def test_node_without_fds_configured_is_excluded():
    """A node with no fds_binary/mpi_executable set would just fail the job immediately --
    never worth assigning to, however idle or well-sized it is."""
    nodes = [_node("not-ready", fds_ready=False)]
    assert scheduler.pick_node_for_job(_job(), nodes, busy_node_ids=set(), now=NOW) is None


def test_ready_node_is_preferred_over_a_bigger_unready_one():
    nodes = [_node("big-unready", cores=32, fds_ready=False), _node("small-ready", cores=4)]
    assert scheduler.pick_node_for_job(_job(), nodes, busy_node_ids=set(), now=NOW) == "small-ready"
