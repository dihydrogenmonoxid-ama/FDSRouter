# FDSRouter

***Deutsch** · [English](README.en.md)*

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

- **Im Netzwerk betreibbar** — der Dienst läuft auf dem Rechner, der rechnet; bedient wird er
  aus dem Browser eines beliebigen Arbeitsplatzes im selben Netz (`host: "0.0.0.0"` setzen).
  Fälle lassen sich vom Arbeitsplatz **hochladen** und das Ergebnisverzeichnis als ZIP wieder
  **herunterladen** — kein Terminal-Zugang zum Rechenknoten nötig
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
- **Archiv** — abgeschlossene Läufe lassen sich aus der Historie ausblenden, bleiben unter
  „Archiv“ einsehbar und kalibrieren weiterhin die Zeitschätzung
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

### Empfohlen: Installationsskript (Linux und macOS)

```bash
git clone https://github.com/dihydrogenmonoxid-ama/FDSRouter.git
cd FDSRouter
./install.sh
```

`install.sh` übernimmt alles, was sonst von Hand nötig wäre:

- prüft, ob ein Python 3.11+ vorhanden ist, und installiert unter Ubuntu/Debian das getrennt
  ausgelieferte `venv`-Paket bei Bedarf nach (fragt dafür nach `sudo`)
- legt die virtuelle Umgebung `.venv` an und installiert FDSRouter hinein — dadurch greift auch
  Ubuntus `externally-managed-environment`-Sperre nicht
- schreibt `config.yaml` und sucht `fds`/`mpirun` nicht nur im `PATH`, sondern auch in den
  üblichen Installationsverzeichnissen (`/opt/FDS…`, `/usr/local/FDS…`, Home, `/Applications`)
- fragt zum Schluss, ob FDSRouter automatisch mitstarten soll (siehe [Autostart](#autostart-systemd))

| Option | Wirkung |
|--------|---------|
| `--service` / `--no-service` | Autostart-Dienst ohne Rückfrage einrichten bzw. überspringen |
| `--service=system` | systemweiten Dienst statt eines Benutzerdienstes anlegen |
| `--host=0.0.0.0`, `--port=8080` | Adresse und Port direkt setzen (siehe „Betrieb im Netzwerk") |
| `--dev` | zusätzlich `pytest` installieren |
| `--yes` | ohne Rückfragen durchlaufen (unbeaufsichtigte Installation) |

Das Skript lässt sich jederzeit erneut ausführen — vorhandene Werte in `config.yaml` und eine
angepasste `service.env` bleiben dabei erhalten.

### Von Hand

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

Unter Ubuntu/Debian ist vorher einmalig `sudo apt install git python3-venv` nötig; ohne dieses
Paket bricht `python3 -m venv` mit „ensurepip is not available" ab.

### Stolpersteine unter Ubuntu/Debian

| Meldung | Ursache und Abhilfe |
|---------|---------------------|
| `ensurepip is not available` / `apt install python3.X-venv` | Debian und Ubuntu liefern `venv` getrennt vom Interpreter aus. `sudo apt install python3-venv` (bzw. genau das im Fehlertext genannte Paket) — `install.sh` erledigt das selbst. |
| `error: externally-managed-environment` | `pip` außerhalb einer virtuellen Umgebung ist auf Systemen mit PEP 668 gesperrt. Nicht mit `--break-system-packages` umgehen, sondern die `.venv` anlegen und `.venv/bin/pip` verwenden. |
| `Sperre /var/lib/dpkg/lock-frontend … (packagekitd)` | Die grafische Aktualisierungsverwaltung hält `apt` gerade belegt. Kurz warten oder „Software-Aktualisierung" schließen; `install.sh` wartet dank `DPkg::Lock::Timeout` von sich aus bis zu drei Minuten. |
| `Für Paket »python« existiert kein Installationskandidat` | Das Paket heißt `python3`. Für den Befehl `python` zusätzlich `python-is-python3` installieren — FDSRouter braucht das nicht. |

## Starten

```bash
fdsrouter start
```

Startet den lokalen Dienst und öffnet automatisch den Standardbrowser mit der Oberfläche unter
`http://127.0.0.1:8000/`. Mit `--no-browser` bleibt der Browser zu, mit `--port <PORT>`
lässt sich der Port überschreiben.

Ohne aktivierte virtuelle Umgebung genügt auch der direkte Aufruf `.venv/bin/fdsrouter start`.

## Autostart (systemd)

Damit FDSRouter dauerhaft im Hintergrund läuft und einen Neustart des Rechners übersteht,
richtet `./install.sh --service` einen systemd-Dienst ein (nachträglich jederzeit möglich):

```bash
./install.sh --service            # Benutzerdienst (Standard, empfohlen)
./install.sh --service=system     # systemweiter Dienst unter /etc/systemd/system
```

Der **Benutzerdienst** landet in `~/.config/systemd/user/fdsrouter.service`, läuft unter der
eigenen Kennung und braucht keine Root-Rechte. Damit er auch ohne Anmeldung schon beim
Hochfahren startet, aktiviert das Skript einmalig `sudo loginctl enable-linger $USER`. Der
**systemweite Dienst** ist die Alternative für Rechenserver, auf denen mehrere Personen
arbeiten; er startet mit `multi-user.target`, läuft aber ebenfalls unter der installierenden
Kennung.

| Aufgabe | Benutzerdienst | Systemweiter Dienst |
|---------|----------------|---------------------|
| Status | `systemctl --user status fdsrouter` | `systemctl status fdsrouter` |
| Log live | `journalctl --user -u fdsrouter -f` | `journalctl -u fdsrouter -f` |
| Neu starten | `systemctl --user restart fdsrouter` | `sudo systemctl restart fdsrouter` |
| Autostart aus | `systemctl --user disable --now fdsrouter` | `sudo systemctl disable --now fdsrouter` |

Ein Dienst startet ohne die Shell-Umgebung des Benutzers, kennt also weder die `PATH`-Einträge
noch die Variablen, die das FDS-Installationsprogramm der `~/.bashrc` hinzufügt. Deshalb liest
der Starter `scripts/fdsrouter-service.sh` vorher die Datei `service.env` im Projektverzeichnis
ein — `install.sh` legt sie mit dem gefundenen `FDS6VARS.sh` und den passenden `PATH`-Einträgen
an. Sie ist installationsspezifisch, wird nicht mit versioniert und lässt sich frei anpassen.

**Achtung beim Neustart des Dienstes:** systemd beendet dabei auch die laufenden `fds`-Prozesse,
weil sie Kindprozesse des Dienstes sind. Vor `restart` oder `stop` also prüfen, ob gerade eine
Simulation läuft.

Aktualisieren:

```bash
cd ~/FDSRouter
git pull
./install.sh --no-service --yes          # Abhängigkeiten nachziehen
systemctl --user restart fdsrouter       # bzw. sudo systemctl restart fdsrouter
```

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
| `upload_dir`            | Ablageort hochgeladener Fälle (je Upload ein Unterverzeichnis)      |
| `max_upload_mb`         | Obergrenze für einen Upload in MB                                   |
| `temperature_enabled`   | Temperaturanzeige an/aus (auf macOS ohne `sudo` meist ohnehin leer) |

Energie- und Home-Assistant-Einstellungen ändern sich im laufenden Betrieb und werden deshalb
nicht in `config.yaml`, sondern über den Einstellungsdialog der Oberfläche gepflegt.

## Betrieb im Netzwerk

Standardmäßig lauscht FDSRouter nur auf `127.0.0.1`, ist also allein vom Rechner selbst aus
erreichbar. Für den Bürobetrieb — Dienst auf dem Rechenserver, Bedienung vom Arbeitsplatz —
genügt in der `config.yaml`:

```yaml
host: "0.0.0.0"
```

Alternativ setzt `./install.sh --host=0.0.0.0 --no-service --yes` denselben Wert; läuft
FDSRouter bereits als Dienst, wird die Änderung erst mit `systemctl --user restart fdsrouter`
wirksam.

Danach ist die Oberfläche unter `http://<server-ip>:8000/` erreichbar. Fälle lädt man über
„Neuer Job → Vom Rechner hochladen" hoch (genau eine `.fds`-Datei, weitere Falldateien wie
Rampen oder Include-Dateien optional); FDS rechnet im angelegten Upload-Verzeichnis, und über
„Ergebnisse" an der Job-Karte kommt das Ergebnisverzeichnis als ZIP zurück.

**FDSRouter hat noch keine Benutzerverwaltung.** Wer die Oberfläche erreicht, darf Jobs
einreihen und beenden — der Dienst gehört daher ausschließlich in ein vertrauenswürdiges Netz,
nicht ans offene Internet.

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
