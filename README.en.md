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
  **uploaded** from the workstation — including a newly created, self-named working directory on
  the compute node — and the result directory **downloaded** again as a ZIP; no terminal access
  to the compute node required
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
  readings parsed live from `.out`, `_hrr.csv` and `_devc.csv`. The live chart (HRR or one DEVC
  device) is drawn with [uPlot](https://github.com/leeoniya/uPlot) including a cursor readout;
  the device list is a collapsible section from which any device can be put on the chart
- **Console log** — the full `mpirun`/`fds` output per job, tailing live and still available
  after the run has finished
- **External run detection** — FDS processes started outside FDSRouter are detected read-only
  and displayed alongside your own jobs (they are never signalled or interfered with)
- **Energy and cost accounting** (optional) — integrate power draw from any Home Assistant
  sensor, including electricity tariff and a solar-power flag
- **Archive** — finished runs can be moved out of the history, stay readable under "Archive"
  and keep calibrating the runtime estimate
- **Service control from the interface** — restart, update and stop the systemd service right
  from the settings dialog, with a confirmation while a simulation is running
- **Persistence** — queue, job history and metric time series live in SQLite and survive a
  restart
- **Tray icon** (Linux desktop) — a small flame in the menu bar with status, open, restart,
  update and stop; starts with the session
- **Bilingual interface** — English by default, German switchable in the settings (the choice
  is remembered in the browser); light and dark colour scheme
- **Multi-machine cluster** — attach further Linux/macOS machines as compute nodes via
  `fdsrouter agent`; a scheduler automatically hands each waiting job to the next free node it
  fits on (see [Cluster / multiple machines](#cluster--multiple-machines))

## Requirements

- Python 3.11 or newer
- An FDS installation including `mpirun`/`mpiexec` — Linux and macOS are the primary target
  platforms, Windows is secondary
- The `fds` and `mpirun` paths are auto-detected from `PATH` on first start (`shutil.which`);
  if that fails, set them manually in `config.yaml`

## Installation

### Recommended: the installer script (Linux and macOS)

```bash
git clone https://github.com/dihydrogenmonoxid-ama/FDSRouter.git
cd FDSRouter
./install.sh
```

`install.sh` takes care of everything you would otherwise do by hand:

- checks for a Python 3.11+ interpreter and, on Ubuntu/Debian, installs the separately shipped
  `venv` package when it is missing (asking for `sudo`)
- creates the `.venv` virtual environment and installs FDSRouter into it, which also keeps
  Ubuntu's `externally-managed-environment` lock out of the way
- writes `config.yaml`, looking for `fds`/`mpirun` on `PATH` as well as in the usual
  installation directories (`/opt/FDS…`, `/usr/local/FDS…`, home, `/Applications`)
- finally asks whether FDSRouter should start with the machine (see [Autostart](#autostart-systemd))

| Option | Effect |
|--------|--------|
| `--service` / `--no-service` | set up (or skip) the autostart service without asking |
| `--service=system` | install a system-wide service instead of a user service |
| `--host=0.0.0.0`, `--port=8080` | set address and port right away (see "Running it on the network") |
| `--tray` / `--no-tray` | set up (or skip) the desktop tray icon |
| `--dev` | also install `pytest` |
| `--yes` | never ask (unattended installation) |

The script can be re-run at any time — existing values in `config.yaml` and an edited
`service.env` are kept.

### By hand

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

On Ubuntu/Debian, `sudo apt install git python3-venv` is needed once up front; without that
package `python3 -m venv` aborts with "ensurepip is not available".

### Common Ubuntu/Debian pitfalls

| Message | Cause and fix |
|---------|---------------|
| `ensurepip is not available` / `apt install python3.X-venv` | Debian and Ubuntu ship `venv` separately from the interpreter. Run `sudo apt install python3-venv` (or exactly the package named in the error) — `install.sh` does this for you. |
| `error: externally-managed-environment` | PEP 668 blocks `pip` outside a virtual environment. Do not work around it with `--break-system-packages`; create the `.venv` and use `.venv/bin/pip`. |
| `Could not get lock /var/lib/dpkg/lock-frontend … (packagekitd)` | The graphical updater is holding `apt`. Wait a moment or close "Software Updater"; `install.sh` waits up to three minutes on its own via `DPkg::Lock::Timeout`. |
| `Package 'python' has no installation candidate` | The package is called `python3`. For the bare `python` command add `python-is-python3` — FDSRouter does not need it. |

## Running

```bash
fdsrouter start
```

Starts the local service and opens the interface at `http://127.0.0.1:8000/` in your default
browser. `--no-browser` keeps the browser closed, `--port <PORT>` overrides the port.

Without activating the virtual environment, `.venv/bin/fdsrouter start` works just as well.

## Autostart (systemd)

To keep FDSRouter running in the background across reboots, `./install.sh --service` registers
a systemd service (you can do this at any time, also after the initial install):

```bash
./install.sh --service            # user service (default, recommended)
./install.sh --service=system     # system-wide service under /etc/systemd/system
```

The **user service** goes to `~/.config/systemd/user/fdsrouter.service`, runs under your own
account and needs no root privileges. So that it also starts at boot without anyone logging in,
the script runs `sudo loginctl enable-linger $USER` once. The **system-wide service** is the
alternative for shared compute servers; it starts with `multi-user.target` but still runs under
the account that installed it.

| Task | User service | System-wide service |
|------|--------------|---------------------|
| Status | `systemctl --user status fdsrouter` | `systemctl status fdsrouter` |
| Follow the log | `journalctl --user -u fdsrouter -f` | `journalctl -u fdsrouter -f` |
| Restart | `systemctl --user restart fdsrouter` | `sudo systemctl restart fdsrouter` |
| Disable autostart | `systemctl --user disable --now fdsrouter` | `sudo systemctl disable --now fdsrouter` |

A service starts without your shell environment, so it knows neither the `PATH` entries nor the
variables the FDS installer appends to `~/.bashrc`. That is why the launcher
`scripts/fdsrouter-service.sh` sources `service.env` from the project directory first —
`install.sh` writes it with the `FDS6VARS.sh` it found and the matching `PATH` entries. The file
is installation-specific, is not version-controlled and can be edited freely.

**Careful when restarting the service:** systemd also terminates the running `fds` processes,
since they are children of the service. Check whether a simulation is running before `restart`
or `stop`.

### Controlling the service from the interface

The settings dialog has a "Dienst" (service) section showing the running version and three
buttons:

- **Update** — fetches the current state (`git pull --ff-only`), reinstalls it into the virtual
  environment and restarts the service afterwards
- **Restart** — restarts the service, for instance after editing `config.yaml`
- **Stop** — stops the service; the interface is then unreachable and has to be started again on
  the machine itself (`systemctl --user start fdsrouter`)

While a simulation is running, the interface asks for confirmation by job name before touching
the service — FDS is a child process of the service and is stopped with it. A run cut short this
way shows up in the history as failed, with a matching note. Without a registered systemd
service the buttons stay disabled and state the reason.

The same steps by hand:

```bash
cd ~/FDSRouter
git pull
./install.sh --no-service --yes          # pull in dependency changes
systemctl --user restart fdsrouter       # or: sudo systemctl restart fdsrouter
```

## Tray icon (Linux desktop)

```bash
./install.sh --tray
```

Adds a small flame to the menu bar that starts with the session
(`~/.config/autostart/fdsrouter-tray.desktop`). The menu shows the status ("running: Atrium_v3"
or "not reachable") and offers open interface, restart, update and stop — the same actions as
the settings dialog, through the same API.

While a simulation is running, the API refuses restart and stop; the tray reports that and
points to the web interface, where it can be confirmed. So a menu entry can never end a running
job.

The icon deliberately does **not** live inside the service: systemd starts the service at boot,
long before anyone logs in, and it has no display at all. It therefore gets its own small
environment (`.venv-tray`, created with `--system-site-packages`), because `pystray`'s Linux
backend needs the distribution package `python3-gi` — the service's own environment stays
untouched. On GNOME the "AppIndicator and KStatusNotifierItem Support" extension is required as
well, which Ubuntu ships by default.

## Configuration

On the very first start, `config.yaml` is created in the current directory (template:
`config.example.yaml`) and FDSRouter tries to locate `fds`/`mpirun` itself. The file is specific
to your installation and is deliberately not version-controlled.

The most common fields (`host`, `port`, `open_browser`, `fds_binary`, `mpi_executable`,
`default_mpi_processes`, `temperature_enabled`, `discovery_enabled`, `max_upload_mb`) can also be
edited directly in the interface under "Operations" → "Configuration", without opening the file
by hand. Most changes apply to the next run immediately; `host`/`port` and network discovery need
a service restart (button right next to it). `role`, `controller_url`, `data_dir`, `upload_dir`,
`mpi_command_template`, `trusted_proxy_header` and `cluster_token` stay file-only on purpose —
some are security-sensitive, others too consequential for a plain form field.

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
| `cluster_token`         | shared secret for compute nodes (`fdsrouter agent`), generated automatically — see [Cluster / multiple machines](#cluster--multiple-machines) |
| `discovery_enabled`     | answer an agent's network search (only effective once `host` isn't `127.0.0.1`) |
| `role`                  | `controller`, `agent`, or `auto` (decided interactively only on the very first `fdsrouter start`) |
| `controller_url`        | only with `role: agent` -- which Controller this machine joins                |

Energy and Home Assistant settings change during operation and are therefore edited in the
interface's settings dialog rather than in `config.yaml`.

## Running it on the network

By default FDSRouter listens on `127.0.0.1` only, so it is reachable from the machine itself.
For office use — service on the compute server, operated from a workstation — set this in
`config.yaml`:

```yaml
host: "0.0.0.0"
```

`./install.sh --host=0.0.0.0 --no-service --yes` sets the same value; when FDSRouter already
runs as a service, the change takes effect after `systemctl --user restart fdsrouter`.

The interface is then available at `http://<server-ip>:8000/`. Cases are uploaded through
"Neuer Job → Vom Rechner hochladen" (exactly one `.fds` file, further case files such as ramps
or includes optional).

The upload creates a new directory on the compute node: this is the working directory FDS runs
in and writes its `.out` and `.csv` output to, and it is exactly what the "Ergebnisse" button on
a job card returns as a ZIP. Two fields control it:

- **Target folder** — the name of the new directory. Prefilled from the `.fds` file name and
  freely editable; leaving it empty falls back to an automatic name built from a timestamp and
  the case name. An existing folder is refused, so two cases never share one result directory.
- **Create in** — either the `upload_dir` configured in `config.yaml`, or the directory
  currently open in the file browser below. That puts the case straight into an existing project
  structure on the server.

The line underneath spells out the full path that will be created.

**Access.** Until an account has been created the interface is open — anyone who can reach it can
enqueue and stop jobs and control the service. As soon as the first account is created via the
login screen, FDSRouter requires a login from then on; there is still no role/permission system,
every account can do everything. Either way, the service belongs on a trusted network only,
never on the open internet.

### The interface is not reachable from another machine

Check these in order on the machine FDSRouter runs on:

```bash
grep '^host' config.yaml              # 127.0.0.1 = accepts local connections only
ss -tlnp | grep :8000                 # expected: 0.0.0.0:8000, not 127.0.0.1:8000
sudo ufw status                       # if active: sudo ufw allow 8000/tcp
ip -4 addr show scope global          # address and subnet -- must match the workstation
```

If `host` is `127.0.0.1`, no IP address will help — change it and restart the service:

```bash
./install.sh --host=0.0.0.0 --no-service --yes
systemctl --user restart fdsrouter
```

If the machine does not even answer `ping` from the workstation, the cause is the network
(separate subnets, VLAN or wireless client isolation), not FDSRouter. A LAN scan that does not
list the machine means little on its own: without `avahi-daemon` a Linux box does not announce
itself over mDNS, and many scanners only evaluate ping replies.

### Fan speed stays empty

FDSRouter reads fans through `psutil.sensors_fans()` and, when that yields nothing, straight
from `/sys/class/hwmon/*/fan*_input`. If the field still stays empty, the machine exposes no fan
sensors — usually because the sensor chip's kernel module is not loaded:

```bash
sudo apt install lm-sensors
sudo sensors-detect          # accept the questions with ENTER, let it add the modules
sensors                      # shows what is readable afterwards
```

If `sensors` finds no fans either, the hardware does not expose them (common on servers and in
virtual machines). No core functionality depends on it, and the reason is shown as a tooltip on
the readout.

## Cluster / multiple machines

One FDSRouter machine (the "Controller") can attach further Linux/macOS machines as compute
nodes. The queue stays a single global list — no target machine is picked when a job is
enqueued; a simple scheduler automatically hands each waiting job to the next free node whose
core count fits the chosen MPI process count.

Prerequisite: the Controller must actually be reachable on the network (`host: "0.0.0.0"`, see
[Running it on the network](#running-it-on-the-network)) — otherwise a compute node may still
*find* it via discovery but won't be able to actually connect.

There's only **one** command, on every machine: `fdsrouter start`. On a fresh machine, run
interactively at a real terminal, the first start looks for an already-running Controller on the
local network automatically (a UDP broadcast, no IP to type in):

```bash
git clone https://github.com/dihydrogenmonoxid-ama/FDSRouter.git
cd FDSRouter
python3 -m venv .venv && .venv/bin/pip install -e .
mkdir second-machine && cd second-machine
../.venv/bin/fdsrouter start
```

- If it finds one, it asks: join this machine as a compute node? Say yes and it just asks for the
  **cluster token** (shown on the Controller under "Operations" → "Cluster", with a copy button)
  — verified against the Controller immediately, so a typo is caught right there instead of three
  retries into a background poll loop nobody is watching.
- If it finds none (or you decline / choose to run your own Controller), the machine simply
  starts as a Controller, same as always.
- Running unattended (a script, systemd, no terminal) never asks — it always becomes a Controller,
  exactly like before this existed.

The decision is written into `config.yaml` (`role: controller` or `role: agent`) and never asked
again on later starts; `role` can also be set there directly to skip the question entirely (handy
for scripting many identical compute nodes). If the Controller isn't on the same network segment,
or `discovery_enabled: false` turns discovery off there, the explicit path with a manual address
still works:

```bash
../.venv/bin/fdsrouter agent --controller-url http://<controller-ip>:8000
```

(`fdsrouter agent` writes its own `agent-config.yaml` and suits a machine meant to be
unambiguously a compute node from the first command typed, e.g. scripted rollout of many
identical nodes. `fdsrouter agent --pair` redoes the setup against a different Controller.)

Once connected, the node shows up in the interface under "Nodes" (listing every known machine
with its status, cores/RAM and current job — including the Controller itself). Case files travel
to and from the node as a ZIP through the Controller — no shared network drive is needed, only a
network path from the compute node to the Controller (the agent polls the Controller itself, so
no inbound firewall rule is needed on the compute node; the one-time network search additionally
uses a UDP broadcast on port 57632 that reveals only hostname and port -- never the token).

"Auto-continue" and the per-job Start button still apply across nodes: a job assigned to a node
only starts there once the queue is set to auto-continue, or that job was started individually —
exactly like a local run.

For always-on operation on a compute node, a systemd service analogous to `fdsrouter start`
works well (see [Autostart](#autostart-systemd)), just with `fdsrouter agent` as `ExecStart`.

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
| `service_control.py`| drives the systemd service (restart, update, stop) for the settings dialog   |

The frontend still needs no build step: the only third-party library is
[uPlot](https://github.com/leeoniya/uPlot) for the live chart, vendored under
`fdsrouter/static/vendor/` — no CDN at runtime, in line with the "local, no cloud dependency"
rule. The tray icon lives in `fdsrouter/tray.py` and only ever talks to the HTTP API, so it runs
independently of the service.

Persistence through SQLite (`fdsrouter/db`), no separate database server. The data model
consists of `node`, `job`, `run_metric_sample`, `out_file_metric` and `settings`.

## License

[MIT](LICENSE)
