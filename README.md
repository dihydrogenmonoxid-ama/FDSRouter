# FDSRouter

[![License: MIT](https://img.shields.io/github/license/dihydrogenmonoxid-ama/FDSRouter?color=blue)](LICENSE)
[![Downloads](https://img.shields.io/github/downloads/dihydrogenmonoxid-ama/FDSRouter/total)](https://github.com/dihydrogenmonoxid-ama/FDSRouter/releases)
[![Release](https://img.shields.io/github/v/release/dihydrogenmonoxid-ama/FDSRouter?display_name=tag&sort=semver)](https://github.com/dihydrogenmonoxid-ama/FDSRouter/releases)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776ab)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS-lightgrey)](#voraussetzungen)
[![Issues](https://img.shields.io/github/issues/dihydrogenmonoxid-ama/FDSRouter)](https://github.com/dihydrogenmonoxid-ama/FDSRouter/issues)
[![Last commit](https://img.shields.io/github/last-commit/dihydrogenmonoxid-ama/FDSRouter)](https://github.com/dihydrogenmonoxid-ama/FDSRouter/commits)
[![Stars](https://img.shields.io/github/stars/dihydrogenmonoxid-ama/FDSRouter?style=flat)](https://github.com/dihydrogenmonoxid-ama/FDSRouter/stargazers)

Lokales Tool zur automatisierten Warteschlangen-Steuerung von [Fire Dynamics Simulator
(FDS)](https://pages.nist.gov/fds-smv/)-Simulationsläufen. Statt FDS-Fälle manuell nacheinander
zu starten, reiht man `.fds`-Dateien in eine Warteschlange ein, FDSRouter arbeitet sie
automatisch nacheinander ab und zeigt währenddessen Live-Systemauslastung, MPI-Prozessstatus
und aus der `.out`-Datei ausgelesene Simulationskennwerte (Zeitschritt, HRR, Fortschritt) an.

Zielgruppe: Ingenieurbüros und Forschungsstellen, die FDS-Simulationen auf einem
Arbeitsplatzrechner oder Server laufen lassen und die Warteschlange nicht mehr von Hand
verwalten wollen.

## Funktionsumfang

- **Warteschlange verwalten** — `.fds`-Dateien über einen serverseitigen Datei-Browser einreihen,
  Reihenfolge per Drag & Drop ändern, auch während bereits ein Lauf aktiv ist (nur der laufende
  Job ist fixiert, alle wartenden Jobs bleiben jederzeit umsortierbar)
- **Automatisierte Ausführung** — Jobs werden sequenziell über ein konfigurierbares
  `mpirun`/`fds`-Kommando gestartet; wahlweise auf Knopfdruck oder mit „Automatisch fortsetzen“
  ohne Zutun bis zum Ende der Warteschlange
- **Zeitschätzung** — Restlaufzeit pro Job aus Mesh-Zellenzahl und MPI-Prozessanzahl geschätzt,
  kalibriert sich mit jedem abgeschlossenen Lauf auf demselben Rechner
- **Live-Monitoring** — CPU (gesamt und je Kern), RAM, Temperatur und Lüfterdrehzahl (best
  effort), Status je MPI-Prozess sowie live aus `.out`, `_hrr.csv` und `_devc.csv` gelesene
  Simulationszeit, Zeitschrittgröße, Wärmefreisetzungsrate und Messstellenwerte
- **Konsolen-Log** — die vollständige Ausgabe von `mpirun`/`fds` je Job, live mitlaufend und
  nach dem Lauf weiterhin abrufbar
- **Externe Läufe erkennen** — FDS-Prozesse, die außerhalb von FDSRouter gestartet wurden,
  werden rein lesend erkannt und mit angezeigt (es wird nie in sie eingegriffen)
- **Energie- und Kostenerfassung** (optional) — Leistungsaufnahme über einen beliebigen
  Home-Assistant-Sensor integrieren, inklusive Strompreis und PV-Kennzeichnung
- **Persistenz** — Warteschlange, Job-Historie und Metrik-Verlauf liegen in SQLite und überstehen
  einen Neustart
- **Zweisprachige Oberfläche** (Deutsch/Englisch), helles und dunkles Farbschema
- **Mehrknotenfähiges Datenmodell** — v1 nutzt nur den lokalen Rechner, das Node-Registrierungs-
  und Heartbeat-Schema ist aber bereits vorhanden, um später weitere Compute-Nodes anzubinden

## Voraussetzungen

- Python 3.11 oder neuer
- Ein installiertes FDS (inkl. `mpirun`/`mpiexec`) — primäre Zielplattformen sind Linux und
  macOS, Windows ist nachrangig
- Der `fds`- und `mpirun`-Pfad werden beim ersten Start automatisch über `PATH` gesucht
  (`shutil.which`); falls das nicht klappt, trägt man sie manuell in `config.yaml` ein

## Installation

```bash
git clone https://github.com/dihydrogenmonoxid-ama/FDSRouter.git
cd FDSRouter
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"          # ohne [dev] reichen die Laufzeit-Abhängigkeiten
```

`pip install -e ".[dev]"` installiert FDSRouter samt Laufzeit-Abhängigkeiten (FastAPI, Uvicorn,
psutil, PyYAML, httpx) und den Console-Befehl `fdsrouter`, sowie `pytest` für die Tests.
Alternativ genügt `pip install -r requirements.txt`, dann startet man über
`python -m fdsrouter.cli start` statt über den `fdsrouter`-Befehl.

## Starten

```bash
fdsrouter start
```

Startet den lokalen Dienst und öffnet automatisch den Standardbrowser mit der Oberfläche unter
`http://127.0.0.1:8000/`. Mit `--no-browser` bleibt der Browser zu, mit `--port <PORT>`
lässt sich der Port überschreiben.

## Konfiguration

Beim allerersten Start wird `config.yaml` im aktuellen Verzeichnis automatisch angelegt
(Vorlage: `config.example.yaml`) und versucht, `fds`/`mpirun` selbst zu finden. Die Datei ist
installationsspezifisch und wird bewusst nicht mit versioniert.

| Schlüssel               | Bedeutung                                                          |
|-------------------------|--------------------------------------------------------------------|
| `host`, `port`          | Adresse, unter der die Oberfläche erreichbar ist                    |
| `open_browser`          | Browser beim Start automatisch öffnen                               |
| `fds_binary`            | Pfad zum `fds`-Executable                                           |
| `mpi_executable`        | Pfad zu `mpirun`/`mpiexec`                                          |
| `mpi_command_template`  | Argumentliste für den Simulationsstart (Platzhalter: `{mpi_exec}`, `{n_processes}`, `{fds_binary}`, `{fds_file}`) |
| `default_mpi_processes` | Standard-Prozessanzahl, falls die Datei keine Meshes erkennen lässt |
| `data_dir`              | Ablageort der SQLite-Datenbank und der Job-Logs                     |
| `temperature_enabled`   | Temperaturanzeige an/aus (auf macOS ohne `sudo` meist ohnehin leer) |

Energie- und Home-Assistant-Einstellungen ändern sich im laufenden Betrieb und werden deshalb
nicht in `config.yaml`, sondern über den Einstellungsdialog der Oberfläche gepflegt.

## Tests

```bash
pytest
```

Deckt u. a. das Parsen der `.out`/`_hrr.csv`-Ausgabe (gegen Fixtures aus einem echten FDS-Lauf),
die Zeitschätzungs-Logik, die Energieabrechnung sowie die Warteschlangen-Regeln (laufender Job
bleibt fixiert) ab.

## Architektur im Überblick

FastAPI-Anwendung (`fdsrouter/api`) mit REST + WebSocket, die zugleich das Vanilla-JS-Frontend
(`fdsrouter/static`) als statische Dateien ausliefert — ein Startbefehl, keine separate
Build- oder Deploy-Pipeline. Die Kernlogik liegt in `fdsrouter/core`:

| Modul               | Aufgabe                                                                    |
|---------------------|-----------------------------------------------------------------------------|
| `job_runner.py`     | startet den `mpirun`/`fds`-Prozess und ermittelt dessen MPI-Kindprozesse     |
| `queue_manager.py`  | dispatcht die Warteschlange sequenziell und setzt die Reorder-Regeln durch   |
| `monitor.py`        | sampelt je laufendem Job Prozess- und `.out`-Kennwerte und broadcastet sie   |
| `system_monitor.py` | sampelt dauerhaft CPU/RAM/Temperatur/Lüfter, unabhängig von einem Job        |
| `out_parser.py`     | liest `.out`, `_hrr.csv` und `_devc.csv` des laufenden Falls                 |
| `fds_parser.py`     | liest Meshanzahl, Zellenzahl und `T_END` aus der `.fds`-Eingabedatei         |
| `estimator.py`      | berechnet die Zeitschätzung aus der Job-Historie desselben Node              |
| `external_jobs.py`  | erkennt rein lesend FDS-Läufe außerhalb von FDSRouter                        |
| `energy.py`         | liest einen Home-Assistant-Leistungssensor und rechnet Energie/Kosten ab     |

Persistenz über SQLite (`fdsrouter/db`), kein separater Datenbankserver. Das Datenmodell kennt
`node`, `job`, `run_metric_sample`, `out_file_metric` und `settings`.

## Lizenz

[MIT](LICENSE)
