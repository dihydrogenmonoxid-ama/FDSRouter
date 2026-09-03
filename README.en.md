# FDSRouter

*[Deutsch](README.md) · **English***

[![License: MIT](https://img.shields.io/github/license/dihydrogenmonoxid-ama/FDSRouter?color=blue)](LICENSE)
[![Downloads](https://img.shields.io/github/downloads/dihydrogenmonoxid-ama/FDSRouter/total)](https://github.com/dihydrogenmonoxid-ama/FDSRouter/releases)
[![Release](https://img.shields.io/github/v/release/dihydrogenmonoxid-ama/FDSRouter?display_name=tag&sort=semver)](https://github.com/dihydrogenmonoxid-ama/FDSRouter/releases)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776ab)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS-lightgrey)](#requirements)
[![Issues](https://img.shields.io/github/issues/dihydrogenmonoxid-ama/FDSRouter)](https://github.com/dihydrogenmonoxid-ama/FDSRouter/issues)
[![Last commit](https://img.shields.io/github/last-commit/dihydrogenmonoxid-ama/FDSRouter)](https://github.com/dihydrogenmonoxid-ama/FDSRouter/commits)
[![Stars](https://img.shields.io/github/stars/dihydrogenmonoxid-ama/FDSRouter?style=flat)](https://github.com/dihydrogenmonoxid-ama/FDSRouter/stargazers)

A local queue manager for [Fire Dynamics Simulator (FDS)](https://pages.nist.gov/fds-smv/) runs.
Instead of starting FDS cases by hand one after another, you queue up `.fds` files; FDSRouter
works through them automatically and shows live system load, MPI process status and the
simulation values read straight out of the `.out` file (time step, HRR, progress) while a case
is running.

Intended for engineering offices and research groups running FDS on a workstation or a server
who no longer want to babysit the queue by hand.

> The user interface is in German by default and can be switched to English in the settings
> dialog; German is the default because the tool is written for German-speaking fire safety
> engineers.

## Features

- **Runs across the network** — the service runs on the machine that computes; it is operated
  from a browser on any workstation on the same network (set `host: "0.0.0.0"`). Cases can be
  **uploaded** from the workstation and the result directory **downloaded** again as a ZIP — no
  terminal access to the compute node required
- **Queue management** — add `.fds` files through a server-side file browser and reorder them by
  drag & drop, including while a run is already active (only the running job is pinned, every
  waiting job stays reorderable at any time)
- **Automated execution** — jobs are started sequentially through a configurable `mpirun`/`fds`
  command, either on demand or, with "auto-continue" enabled, all the way through the queue
  without further input
- **Runtime estimation** — per-job estimate derived from the mesh cell count and MPI process
  count, recalibrated by every completed run on the same machine
- **Live monitoring** — CPU (total and per core), RAM, temperature and fan speed (best effort),
  per-MPI-process status, plus simulation time, time step size, heat release rate and device
  readings parsed live from `.out`, `_hrr.csv` and `_devc.csv`
- **Console log** — the full `mpirun`/`fds` output per job, tailing live and still available
  after the run has finished
- **External run detection** — FDS processes started outside FDSRouter are detected read-only
  and displayed alongside your own jobs (they are never signalled or interfered with)
- **Energy and cost accounting** (optional) — integrate power draw from any Home Assistant
  sensor, including electricity tariff and a solar-power flag
- **Archive** — finished runs can be moved out of the history, stay readable under "Archive"
  and keep calibrating the runtime estimate
- **Persistence** — queue, job history and metric time series live in SQLite and survive a
  restart
- **Bilingual interface** (German/English), light and dark colour scheme
- **Multi-node ready data model** — v1 only uses the local machine, but the node registration
  and heartbeat schema is already in place so further compute nodes can be attached later

## Requirements

- Python 3.11 or newer
- An FDS installation including `mpirun`/`mpiexec` — Linux and macOS are the primary target
  platforms, Windows is secondary
- The `fds` and `mpirun` paths are auto-detected from `PATH` on first start (`shutil.which`);
  if that fails, set them manually in `config.yaml`

## Installation

```bash
git clone https://github.com/dihydrogenmonoxid-ama/FDSRouter.git
cd FDSRouter
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"          # without [dev] you only get the runtime dependencies
```

`pip install -e ".[dev]"` installs FDSRouter with its runtime dependencies (FastAPI, Uvicorn,
psutil, PyYAML, httpx) and the `fdsrouter` console command, plus `pytest` for the test suite.
`pip install -r requirements.txt` is enough as well — then start it with
`python -m fdsrouter.cli start` instead of the `fdsrouter` command.

## Running

```bash
fdsrouter start
```

Starts the local service and opens the interface at `http://127.0.0.1:8000/` in your default
browser. `--no-browser` keeps the browser closed, `--port <PORT>` overrides the port.

## Configuration

On the very first start, `config.yaml` is created in the current directory (template:
`config.example.yaml`) and FDSRouter tries to locate `fds`/`mpirun` itself. The file is specific
to your installation and is deliberately not version-controlled.

| Key                     | Meaning                                                             |
|-------------------------|---------------------------------------------------------------------|
| `host`, `port`          | address the interface is served on                                  |
| `open_browser`          | open the browser automatically on start                             |
| `fds_binary`            | path to the `fds` executable                                        |
| `mpi_executable`        | path to `mpirun`/`mpiexec`                                          |
| `mpi_command_template`  | argument list used to launch a simulation (placeholders: `{mpi_exec}`, `{n_processes}`, `{fds_binary}`, `{fds_file}`) |
| `default_mpi_processes` | fallback process count when no meshes can be read from the file     |
| `data_dir`              | location of the SQLite database and the job logs                    |
| `upload_dir`            | where uploaded cases are stored (one subdirectory per upload)        |
| `max_upload_mb`         | size limit for a single upload, in MB                               |
| `temperature_enabled`   | temperature readout on/off (usually empty on macOS without `sudo`)  |

Energy and Home Assistant settings change during operation and are therefore edited in the
interface's settings dialog rather than in `config.yaml`.

## Running it on the network

By default FDSRouter listens on `127.0.0.1` only, so it is reachable from the machine itself.
For office use — service on the compute server, operated from a workstation — set this in
`config.yaml`:

```yaml
host: "0.0.0.0"
```

The interface is then available at `http://<server-ip>:8000/`. Cases are uploaded through
"Neuer Job → Vom Rechner hochladen" (exactly one `.fds` file, further case files such as ramps
or includes optional); FDS computes in the created upload directory, and the "Ergebnisse" button
on a job card returns the result directory as a ZIP.

**FDSRouter has no user management yet.** Anyone who can reach the interface can enqueue and
stop jobs, so the service belongs on a trusted network only, never on the open internet.

## Tests

```bash
pytest
```

Covers, among other things, parsing of the `.out`/`_hrr.csv` output (against fixtures from a
real FDS run), the runtime estimation logic, energy accounting and the queue rules (the running
job stays pinned).

## Architecture at a glance

A FastAPI application (`fdsrouter/api`) exposing REST + WebSocket that also serves the vanilla
JS frontend (`fdsrouter/static`) as static files — one command to start, no separate build or
deploy pipeline. The core logic lives in `fdsrouter/core`:

| Module              | Responsibility                                                              |
|---------------------|-----------------------------------------------------------------------------|
| `job_runner.py`     | starts the `mpirun`/`fds` process and discovers its MPI child processes      |
| `queue_manager.py`  | dispatches the queue sequentially and enforces the reordering rules          |
| `monitor.py`        | samples per-job process and `.out` values and broadcasts them                |
| `system_monitor.py` | samples CPU/RAM/temperature/fans continuously, independent of any job        |
| `out_parser.py`     | reads `.out`, `_hrr.csv` and `_devc.csv` of the running case                 |
| `fds_parser.py`     | reads mesh count, cell count and `T_END` from the `.fds` input file          |
| `estimator.py`      | computes the runtime estimate from the same node's job history               |
| `external_jobs.py`  | detects FDS runs outside FDSRouter, strictly read-only                       |
| `energy.py`         | reads a Home Assistant power sensor and accounts energy/cost                 |

Persistence through SQLite (`fdsrouter/db`), no separate database server. The data model
consists of `node`, `job`, `run_metric_sample`, `out_file_metric` and `settings`.

## License

[MIT](LICENSE)
