"""SQLite repository layer for FDSRouter.

Uses a single shared connection guarded by a lock rather than SQLAlchemy -- the
access pattern is simple CRUD plus small time-series inserts, and stdlib sqlite3
keeps the dependency footprint minimal (see CLAUDE.md section 12).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Database:
    def __init__(self, db_path: Path):
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        with self._lock, self.conn:
            self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created.

        schema.sql only runs CREATE TABLE IF NOT EXISTS, so an existing installation would
        never see a new column -- and dropping the table would throw away the job history.
        """
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(job)")}
        for column, ddl in (
            ("archived_at", "ALTER TABLE job ADD COLUMN archived_at TEXT"),
            # Who queued the run, which project it belongs to, and the operator's own note --
            # all optional, so a database from before accounts existed keeps working unchanged.
            ("created_by", "ALTER TABLE job ADD COLUMN created_by TEXT"),
            ("project", "ALTER TABLE job ADD COLUMN project TEXT"),
            ("notes", "ALTER TABLE job ADD COLUMN notes TEXT"),
        ):
            if column not in columns:
                self.conn.execute(ddl)

    # -- Node -----------------------------------------------------------------

    def upsert_node(self, node_id: str, hostname: str, os_name: str, cpu_cores: int, ram_total_mb: int) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO node (id, hostname, os, cpu_cores, ram_total_mb, status, last_heartbeat)
                VALUES (?, ?, ?, ?, ?, 'online', ?)
                ON CONFLICT(id) DO UPDATE SET
                    hostname=excluded.hostname, os=excluded.os, cpu_cores=excluded.cpu_cores,
                    ram_total_mb=excluded.ram_total_mb, status='online', last_heartbeat=excluded.last_heartbeat
                """,
                (node_id, hostname, os_name, cpu_cores, ram_total_mb, _now()),
            )

    def heartbeat_node(self, node_id: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE node SET status='online', last_heartbeat=? WHERE id=?", (_now(), node_id)
            )

    def get_nodes(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM node").fetchall()
        return [dict(r) for r in rows]

    # -- Job --------------------------------------------------------------------

    def create_job(
        self,
        *,
        name: str,
        fds_file_path: str,
        node_id: str,
        mesh_cell_count: int | None,
        sim_end_time_s: float | None,
        mpi_process_count: int,
        estimated_duration_s: float | None,
        case_hash: str | None = None,
        priority: int = 0,
        created_by: str | None = None,
        project: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        with self._lock, self.conn:
            next_pos = self.conn.execute(
                "SELECT COALESCE(MAX(queue_position), -1) + 1 FROM job WHERE status='queued'"
            ).fetchone()[0]
            self.conn.execute(
                """
                INSERT INTO job (
                    id, name, fds_file_path, case_hash, node_id, status, queue_position, priority,
                    mesh_cell_count, sim_end_time_s, mpi_process_count, created_at, estimated_duration_s,
                    created_by, project, notes
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, name, fds_file_path, case_hash, node_id, next_pos, priority,
                    mesh_cell_count, sim_end_time_s, mpi_process_count, _now(), estimated_duration_s,
                    created_by, project, notes,
                ),
            )
        return self.get_job(job_id)  # type: ignore[return-value]

    def update_job(self, job_id: str, project: str | None, notes: str | None) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE job SET project=?, notes=? WHERE id=?", (project, notes, job_id)
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT job.*, node.hostname AS node_hostname FROM job "
                "LEFT JOIN node ON node.id = job.node_id WHERE job.id=?",
                (job_id,),
            ).fetchone()
        return _row_to_dict(row)

    def get_jobs(
        self, statuses: Iterable[str] | None = None, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        # node_hostname is which machine a job (will run/is running/ran) on -- shown next to
        # the running job now, and lets a future multi-node UI distinguish jobs per machine.
        conditions = []
        params: list[Any] = []
        if statuses:
            statuses = list(statuses)
            conditions.append(f"job.status IN ({','.join('?' for _ in statuses)})")
            params.extend(statuses)
        if not include_archived:
            conditions.append("job.archived_at IS NULL")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._lock:
            rows = self.conn.execute(
                "SELECT job.*, node.hostname AS node_hostname FROM job "
                f"LEFT JOIN node ON node.id = job.node_id {where} "
                "ORDER BY (job.status='running') DESC, job.queue_position ASC, job.created_at DESC",
                tuple(params),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_archived_jobs(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT job.*, node.hostname AS node_hostname FROM job "
                "LEFT JOIN node ON node.id = job.node_id WHERE job.archived_at IS NOT NULL "
                "ORDER BY job.archived_at DESC, job.finished_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def archive_finished_jobs(self) -> int:
        """Move every finished run out of the history view. Queued and running jobs are never
        touched, so archiving during an active run is safe. Returns how many were archived."""
        with self._lock, self.conn:
            cursor = self.conn.execute(
                "UPDATE job SET archived_at=? "
                "WHERE archived_at IS NULL AND status IN ('done', 'failed', 'cancelled')",
                (_now(),),
            )
            return cursor.rowcount

    def get_running_job(self, node_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM job WHERE node_id=? AND status='running'", (node_id,)
            ).fetchone()
        return _row_to_dict(row)

    def get_next_queued_job(self, node_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM job WHERE node_id=? AND status='queued' "
                "ORDER BY queue_position ASC LIMIT 1",
                (node_id,),
            ).fetchone()
        return _row_to_dict(row)

    def start_job(self, job_id: str, pid: int) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE job SET status='running', started_at=?, pid=?, queue_position=NULL WHERE id=?",
                (_now(), pid, job_id),
            )

    def finish_job(
        self,
        job_id: str,
        status: str,
        exit_message: str | None = None,
        energy_kwh: float | None = None,
        energy_cost_eur: float | None = None,
    ) -> None:
        with self._lock, self.conn:
            row = self.conn.execute("SELECT started_at FROM job WHERE id=?", (job_id,)).fetchone()
            duration = None
            if row and row["started_at"]:
                started = datetime.fromisoformat(row["started_at"])
                duration = (datetime.now(timezone.utc) - started).total_seconds()
            # queue_position is cleared here too: a job that failed before it ever started
            # (bad binary/path) still holds one, and would otherwise keep a gap in the queue.
            self.conn.execute(
                "UPDATE job SET status=?, finished_at=?, actual_duration_s=?, exit_message=?, pid=NULL, "
                "queue_position=NULL, energy_kwh=?, energy_cost_eur=? WHERE id=?",
                (status, _now(), duration, exit_message, energy_kwh, energy_cost_eur, job_id),
            )

    def cancel_queued_job(self, job_id: str) -> bool:
        """Remove a not-yet-started job from the queue. Returns False if it's already
        running/terminal (use stop_running_job for a running job)."""
        with self._lock, self.conn:
            row = self.conn.execute("SELECT status FROM job WHERE id=?", (job_id,)).fetchone()
            if row is None or row["status"] != "queued":
                return False
            self.conn.execute(
                "UPDATE job SET status='cancelled', finished_at=?, queue_position=NULL WHERE id=?",
                (_now(), job_id),
            )
        return True

    def reorder_queue(self, node_id: str, ordered_job_ids: list[str]) -> None:
        """Set queue_position for the given queued job ids, in the given order.

        Only jobs currently in status='queued' may be reordered -- a running job is never
        touched here, which is how "running stays pinned" is enforced (see CLAUDE.md 7.2).
        """
        with self._lock, self.conn:
            queued_ids = {
                r["id"]
                for r in self.conn.execute(
                    "SELECT id FROM job WHERE node_id=? AND status='queued'", (node_id,)
                ).fetchall()
            }
            if set(ordered_job_ids) != queued_ids:
                raise ValueError("ordered_job_ids must contain exactly the currently queued jobs")
            for position, job_id in enumerate(ordered_job_ids):
                self.conn.execute("UPDATE job SET queue_position=? WHERE id=?", (position, job_id))

    def get_completed_jobs_for_node(self, node_id: str) -> list[dict[str, Any]]:
        # Deliberately includes archived runs: archiving tidies the UI, it does not discard the
        # measurements the runtime estimate calibrates against.
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM job WHERE node_id=? AND status='done' AND mesh_cell_count IS NOT NULL "
                "AND actual_duration_s IS NOT NULL ORDER BY finished_at DESC",
                (node_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- Metrics ------------------------------------------------------------------

    def insert_run_metric_sample(
        self,
        *,
        job_id: str,
        cpu_percent_total: float | None,
        cpu_percent_per_core: list[float] | None,
        ram_percent: float | None,
        temperature_c: float | None,
        per_process_stats: list[dict[str, Any]] | None,
    ) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO run_metric_sample
                    (job_id, timestamp, cpu_percent_total, cpu_percent_per_core, ram_percent,
                     temperature_c, per_process_stats)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id, _now(), cpu_percent_total,
                    json.dumps(cpu_percent_per_core) if cpu_percent_per_core is not None else None,
                    ram_percent,
                    json.dumps(temperature_c) if temperature_c is not None else None,
                    json.dumps(per_process_stats) if per_process_stats is not None else None,
                ),
            )

    def insert_out_file_metric(
        self,
        *,
        job_id: str,
        simulation_time_s: float | None,
        walltime_s: float | None,
        step_size_s: float | None,
        total_hrr_kw: float | None,
        step_number: int | None,
        warnings_count: int,
    ) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO out_file_metric
                    (job_id, timestamp, simulation_time_s, walltime_s, step_size_s, total_hrr_kw,
                     step_number, warnings_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, _now(), simulation_time_s, walltime_s, step_size_s, total_hrr_kw, step_number, warnings_count),
            )

    def get_run_metric_samples(self, job_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM run_metric_sample WHERE job_id=? ORDER BY id DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        samples = [dict(r) for r in reversed(rows)]
        for sample in samples:
            for field in ("cpu_percent_per_core", "temperature_c", "per_process_stats"):
                if sample.get(field):
                    sample[field] = json.loads(sample[field])
        return samples

    def get_out_file_metrics(self, job_id: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM out_file_metric WHERE job_id=? ORDER BY id DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_latest_out_file_metric(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM out_file_metric WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,)
            ).fetchone()
        return _row_to_dict(row)

    # -- Runtime settings (Home Assistant / energy config) ------------------------------------

    def get_settings(self, keys: Iterable[str]) -> dict[str, str | None]:
        keys = list(keys)
        with self._lock:
            placeholders = ",".join("?" for _ in keys)
            rows = self.conn.execute(
                f"SELECT key, value FROM settings WHERE key IN ({placeholders})", keys
            ).fetchall()
        found = {r["key"]: r["value"] for r in rows}
        return {k: found.get(k) for k in keys}

    def set_settings(self, values: dict[str, str | None]) -> None:
        with self._lock, self.conn:
            for key, value in values.items():
                self.conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )

    # -- Users, sessions, audit log --------------------------------------------------------

    def count_users(self) -> int:
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) FROM user").fetchone()[0]

    def create_user(self, *, username: str, display_name: str, password_hash: str | None) -> dict[str, Any]:
        user_id = uuid.uuid4().hex
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO user (id, username, display_name, password_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, username, display_name, password_hash, _now()),
            )
        return self.get_user(user_id)  # type: ignore[return-value]

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM user WHERE id=?", (user_id,)).fetchone()
        return _row_to_dict(row)

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM user WHERE username=?", (username,)).fetchone()
        return _row_to_dict(row)

    def touch_last_login(self, user_id: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("UPDATE user SET last_login_at=? WHERE id=?", (_now(), user_id))

    def create_session_row(self, token_hash: str, user_id: str, expires_at: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO session (token_hash, user_id, created_at, expires_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (token_hash, user_id, _now(), expires_at, _now()),
            )

    def get_session(self, token_hash: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM session WHERE token_hash=?", (token_hash,)).fetchone()
        return _row_to_dict(row)

    def touch_session(self, token_hash: str, expires_at: str, last_seen_at: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE session SET expires_at=?, last_seen_at=? WHERE token_hash=?",
                (expires_at, last_seen_at, token_hash),
            )

    def delete_session(self, token_hash: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM session WHERE token_hash=?", (token_hash,))

    def insert_audit_entry(
        self, username: str | None, action: str, job_id: str | None = None, detail: str | None = None
    ) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO audit_entry (timestamp, username, action, job_id, detail) VALUES (?, ?, ?, ?, ?)",
                (_now(), username, action, job_id, detail),
            )

    def get_audit_entries(self, job_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            if job_id is not None:
                rows = self.conn.execute(
                    "SELECT * FROM audit_entry WHERE job_id=? ORDER BY id DESC LIMIT ?", (job_id, limit)
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM audit_entry ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        return [dict(r) for r in rows]
