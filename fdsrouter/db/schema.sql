-- FDSRouter SQLite schema. See CLAUDE.md section 6 for the data model rationale.

CREATE TABLE IF NOT EXISTS node (
    id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    os TEXT NOT NULL,
    cpu_cores INTEGER NOT NULL,
    ram_total_mb INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'offline' CHECK (status IN ('online', 'offline')),
    last_heartbeat TEXT
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
    archived_at TEXT
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
