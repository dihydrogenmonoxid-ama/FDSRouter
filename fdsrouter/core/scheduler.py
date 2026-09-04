"""Which node a queued job should go to, once it isn't pinned to one at creation time.

Deliberately pure: plain dicts in, a node id (or None) out, no DB/asyncio -- QueueManager reads
what it needs from the database, calls this, and writes the result back. That split is what
makes "2 jobs, 3 nodes, 1 busy" scenarios testable without a running dispatch loop.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# ~4.5x the agent's 10s heartbeat cadence -- generous enough that one missed beat over a slow
# network tick doesn't flag a live node as gone, tight enough that a dead agent is noticed well
# within a single FDS run rather than leaving its job stuck "running" indefinitely.
NODE_STALE_AFTER_S = 45.0


def is_node_online(node: dict, now: datetime) -> bool:
    last_heartbeat = node.get("last_heartbeat")
    if not last_heartbeat:
        return False
    seen = datetime.fromisoformat(last_heartbeat)
    return now - seen < timedelta(seconds=NODE_STALE_AFTER_S)


def pick_node_for_job(job: dict, nodes: list[dict], busy_node_ids: set[str], now: datetime) -> str | None:
    """The best online, idle node this job's process count fits on -- or None if no node
    qualifies right now (job stays queued and unassigned until one does)."""
    required_cores = job["mpi_process_count"]
    eligible = [
        node
        for node in nodes
        if node["id"] not in busy_node_ids
        and node["cpu_cores"] >= required_cores
        and is_node_online(node, now)
        # A node without fds_binary/mpi_executable configured would just fail the job
        # immediately -- never worth assigning to, no matter how idle or well-sized it is.
        and node.get("fds_ready")
    ]
    if not eligible:
        return None
    # Idle is binary here (busy nodes are already excluded), so "most free cores" collapses to
    # "biggest machine" as a simple, defensible v1 tie-break; node id as a final deterministic
    # tiebreak so the choice doesn't depend on dict/list ordering.
    best = max(eligible, key=lambda node: (node["cpu_cores"], node["id"]))
    return best["id"]
