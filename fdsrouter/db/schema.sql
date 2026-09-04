-- FDSRouter SQLite schema. See CLAUDE.md section 6 for the data model rationale.

CREATE TABLE IF NOT EXISTS node (
    id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    os TEXT NOT NULL,
    cpu_cores INTEGER NOT NULL,
    ram_total_mb INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'offline' CHECK (status IN ('online', 'offline')),
    last_heartbeat TEXT,
    -- Whether this node's own config.yaml/agent-config.yaml has fds_binary and mpi_executable
    -- both set -- reported at registration time. A node can be online but not fds_ready (e.g. a
    -- Controller-only install with no local FDS), and the scheduler must never assign it a job.
    fds_ready INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS job (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    fds_file_path TEXT NOT NULL,
    case_hash TEXT,
    node_id TEXT REFERENCES node(id),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    queue_position INTEGER,
    priority INTEGER NOT NULL DEFAULT 0,
    mesh_cell_count INTEGER,
    sim_end_time_s REAL,
    mpi_process_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    estimated_duration_s REAL,
    actual_duration_s REAL,
    pid INTEGER,
    exit_message TEXT,
    energy_kwh REAL,
    energy_cost_eur REAL,
    -- Set when a finished run is moved out of the history view. The row is kept: archived runs
    -- still calibrate the runtime estimator, they are just no longer shown by default.
    archived_at TEXT,
    created_by TEXT,
    project TEXT,
    notes TEXT,
    stop_requested_at TEXT,
    start_requested_at TEXT,
    -- Operator-chosen deadline (ISO datetime): a queued job past this is cancelled instead of
    -- started, a running one is stopped -- for a case that must not still be occupying the
    -- machine (or blocking the next in line) come a certain time, regardless of progress.
    scheduled_stop_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_job_status_queue ON job (status, queue_position);

CREATE TABLE IF NOT EXISTS run_metric_sample (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES job(id),
    timestamp TEXT NOT NULL,
    cpu_percent_total REAL,
    cpu_percent_per_core TEXT,
    ram_percent REAL,
    temperature_c TEXT,
    per_process_stats TEXT
);

CREATE INDEX IF NOT EXISTS idx_run_metric_job_ts ON run_metric_sample (job_id, timestamp);

CREATE TABLE IF NOT EXISTS out_file_metric (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES job(id),
    timestamp TEXT NOT NULL,
    simulation_time_s REAL,
    walltime_s REAL,
    step_size_s REAL,
    total_hrr_kw REAL,
    step_number INTEGER,
    warnings_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_out_file_metric_job_ts ON out_file_metric (job_id, timestamp);

-- Runtime-editable operational settings (Home Assistant power sensor, electricity tariff, ...).
-- Deliberately separate from config.yaml, which is install-time/host config, not something the
-- UI should rewrite on disk while the service is running.
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Accounts. FDSRouter enforces a login as soon as at least one user exists, so an existing
-- installation is never locked out by an update -- see core/auth.py.
CREATE TABLE IF NOT EXISTS user (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    -- scrypt (stdlib hashlib), stored as algorithm$params$salt$hash. NULL for accounts that
    -- authenticate through a reverse proxy instead of a password.
    password_hash TEXT,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

-- Only the hash of a session token is stored: a stolen database must not hand out live sessions.
CREATE TABLE IF NOT EXISTS session (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES user(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_session_user ON session (user_id);

-- Who did what. Written for the actions that change what the machine is doing, so a shared
-- compute node stays explainable after the fact.
CREATE TABLE IF NOT EXISTS audit_entry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    username TEXT,
    action TEXT NOT NULL,
    job_id TEXT,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_entry (timestamp DESC);
