#!/usr/bin/env bash
#
# FDSRouter installer for Linux and macOS.
#
# Creates a virtual environment next to this script, installs FDSRouter into it, writes
# config.yaml and -- on request -- registers a systemd service so FDSRouter starts with the
# machine.
#
#   ./install.sh                     install, then ask about the autostart service
#   ./install.sh --service           install and register a systemd user service
#   ./install.sh --service=system    register a system-wide service instead (needs sudo)
#   ./install.sh --no-service --yes  unattended install without service
#
# Options: --dev (extra test dependencies), --host=HOST, --port=PORT, --yes.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$REPO_DIR/.venv"
PYTHON_BIN=""
SERVICE_MODE="ask"   # ask | user | system | none
TRAY_MODE="ask"      # ask | yes | none
ASSUME_YES=0
WITH_DEV=0
HOST_OVERRIDE=""
PORT_OVERRIDE=""

bold=""; red=""; yellow=""; green=""; reset=""
if [ -t 1 ]; then
    bold=$'\033[1m'; red=$'\033[31m'; yellow=$'\033[33m'; green=$'\033[32m'; reset=$'\033[0m'
fi

info() { printf '\n%s==> %s%s\n' "$bold" "$*" "$reset"; }
ok()   { printf '    %sok%s  %s\n' "$green" "$reset" "$*"; }
note() { printf '        %s\n' "$*"; }
warn() { printf '    %s!!%s  %s\n' "$yellow" "$reset" "$*" >&2; }
die()  { printf '\n    %sAbbruch:%s %s\n\n' "$red" "$reset" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

confirm() {  # confirm "Frage" -- default yes, "nein" when not interactive
    [ "$ASSUME_YES" = 1 ] && return 0
    [ -t 0 ] || return 1
    local answer
    read -r -p "    $1 [J/n] " answer || return 1
    case "${answer:-j}" in [jJyY]*|"") return 0 ;; *) return 1 ;; esac
}

usage() {
    cat <<'HELP'
FDSRouter installieren (Linux und macOS)

  ./install.sh                     installieren und anschliessend nach dem Autostart fragen
  ./install.sh --service           installieren und systemd-Dienst fuer den Benutzer einrichten
  ./install.sh --service=system    systemweiten Dienst einrichten (benoetigt sudo)
  ./install.sh --no-service --yes  unbeaufsichtigt installieren, ohne Dienst

Weitere Optionen:
  --tray           Tray-Icon im Desktop einrichten (Autostart in der Sitzung)
  --no-tray        Tray-Icon ueberspringen
  --dev            zusaetzlich pytest installieren
  --host=ADRESSE   Adresse in config.yaml setzen (z. B. 0.0.0.0 fuer Netzbetrieb)
  --port=PORT      Port in config.yaml setzen
  --yes, -y        keine Rueckfragen stellen
HELP
    exit 0
}

for arg in "$@"; do
    case "$arg" in
        --dev)             WITH_DEV=1 ;;
        --service)         SERVICE_MODE="user" ;;
        --service=user)    SERVICE_MODE="user" ;;
        --service=system)  SERVICE_MODE="system" ;;
        --no-service)      SERVICE_MODE="none" ;;
        --tray)            TRAY_MODE="yes" ;;
        --no-tray)         TRAY_MODE="none" ;;
        --host=*)          HOST_OVERRIDE="${arg#*=}" ;;
        --port=*)          PORT_OVERRIDE="${arg#*=}" ;;
        --yes|-y)          ASSUME_YES=1 ;;
        --help|-h)         usage ;;
        *)                 die "Unbekannte Option: $arg (--help zeigt die Optionen)" ;;
    esac
done

printf '%sFDSRouter-Installation%s  (%s)\n' "$bold" "$reset" "$REPO_DIR"

# --------------------------------------------------------------------------------------
# 1. Package manager helpers
# --------------------------------------------------------------------------------------

APT_GET=""
if have apt-get; then
    APT_GET="apt-get"
fi
APT_LOG="$(mktemp "${TMPDIR:-/tmp}/fdsrouter-apt.XXXXXX")"
APT_UPDATED=0
trap 'rm -f "$APT_LOG"' EXIT

apt_install() {  # apt_install pkg... -- returns non-zero when the install fails
    local sudo_cmd=()
    if [ "$(id -u)" != 0 ]; then
        have sudo || { warn "sudo ist nicht verfuegbar -- Paket(e) bitte als root installieren: apt install $*"; return 1; }
        sudo_cmd=(sudo)
        sudo -v || return 1   # ask for the password visibly before output is redirected
    fi

    # DPkg::Lock::Timeout waits for packagekitd/unattended-upgrades instead of failing outright.
    local apt_opts=(-o DPkg::Lock::Timeout=180)
    if [ "$APT_UPDATED" = 0 ]; then
        note "apt-get update"
        ${sudo_cmd[@]+"${sudo_cmd[@]}"} "$APT_GET" "${apt_opts[@]}" update >"$APT_LOG" 2>&1 || true
        APT_UPDATED=1
    fi

    note "apt-get install -y $*"
    if ! ${sudo_cmd[@]+"${sudo_cmd[@]}"} "$APT_GET" "${apt_opts[@]}" install -y "$@" >"$APT_LOG" 2>&1; then
        tail -n 5 "$APT_LOG" >&2
        return 1
    fi
}

# --------------------------------------------------------------------------------------
# 2. Pick a Python interpreter that can build virtual environments
# --------------------------------------------------------------------------------------

python_version_ok() { "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; }
python_venv_ok()    { "$1" -c 'import ensurepip, venv' >/dev/null 2>&1; }
python_version()    { "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])'; }

select_python() {
    local candidates=(python3 python3.13 python3.12 python3.11 python3.14 python3.15)
    local usable=() candidate resolved seen=""

    for candidate in "${candidates[@]}"; do
        have "$candidate" || continue
        resolved="$(command -v "$candidate")"
        case "$seen" in *":$resolved:"*) continue ;; esac
        seen="$seen:$resolved:"
        python_version_ok "$resolved" || continue
        usable+=("$resolved")
    done

    [ ${#usable[@]} -gt 0 ] || die "Kein Python 3.11+ gefunden. Unter Ubuntu/Debian: sudo apt install python3 python3-venv"

    # Prefer an interpreter whose venv module is already complete -- that needs no root.
    for candidate in "${usable[@]}"; do
        if python_venv_ok "$candidate"; then
            PYTHON_BIN="$candidate"
            return
        fi
    done

    # Ubuntu/Debian ship venv/ensurepip in a separate package; install it for the default one.
    local target="${usable[0]}" version
    version="$(python_version "$target")"
    warn "Python $version hat kein funktionierendes venv-Modul (Debian/Ubuntu liefert es getrennt aus)."

    [ -n "$APT_GET" ] || die "Bitte das venv-Paket der Distribution nachinstallieren (z. B. python${version}-venv) und install.sh erneut ausführen."

    info "Systempaket für virtuelle Umgebungen nachinstallieren"
    if apt_install "python${version}-venv" || apt_install python3-venv; then
        ok "venv-Paket installiert"
    else
        die "Installation von python${version}-venv fehlgeschlagen. Bitte manuell ausführen: sudo apt install python${version}-venv"
    fi

    python_venv_ok "$target" || die "venv steht weiterhin nicht bereit. Bitte 'sudo apt install python${version}-venv python3-full' prüfen."
    PYTHON_BIN="$target"
}

info "Python auswählen"
select_python
ok "$PYTHON_BIN (Version $(python_version "$PYTHON_BIN"))"

# --------------------------------------------------------------------------------------
# 3. Virtual environment + FDSRouter
# --------------------------------------------------------------------------------------

info "Virtuelle Umgebung anlegen"
# A venv left behind by a failed `python3 -m venv` has pyvenv.cfg and the interpreter symlinks
# but no pip, so require both markers before reusing the directory.
if [ -f "$VENV_DIR/pyvenv.cfg" ] && [ -x "$VENV_DIR/bin/python" ] \
   && "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
    ok "vorhanden: $VENV_DIR"
else
    if [ -e "$VENV_DIR" ]; then
        warn "Unvollstaendige Umgebung gefunden -- wird neu angelegt: $VENV_DIR"
        rm -rf "$VENV_DIR"
    fi
    "$PYTHON_BIN" -m venv "$VENV_DIR" || die "venv konnte nicht angelegt werden."
    ok "angelegt: $VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python"
"$VENV_PY" -m pip install --quiet --upgrade pip >/dev/null || warn "pip konnte nicht aktualisiert werden -- Installation wird trotzdem versucht."

TARGET="."
[ "$WITH_DEV" = 1 ] && TARGET=".[dev]"

info "FDSRouter installieren ($TARGET)"
if ! (cd "$REPO_DIR" && "$VENV_PY" -m pip install --quiet -e "$TARGET"); then
    warn "Installation fehlgeschlagen -- möglicherweise fehlen Compiler/Header für ein Paket ohne fertiges Wheel."
    if [ -n "$APT_GET" ] && confirm "Build-Werkzeuge (build-essential, python3-dev) nachinstallieren und erneut versuchen?"; then
        apt_install build-essential "python$(python_version "$PYTHON_BIN")-dev" python3-dev || warn "Nicht alle Build-Pakete konnten installiert werden."
        (cd "$REPO_DIR" && "$VENV_PY" -m pip install --quiet -e "$TARGET") || die "Installation weiterhin fehlgeschlagen. Ausgabe prüfen mit: $VENV_PY -m pip install -e \"$TARGET\""
    else
        die "Installation abgebrochen. Ausgabe prüfen mit: $VENV_PY -m pip install -e \"$TARGET\""
    fi
fi
ok "FDSRouter installiert ($VENV_DIR/bin/fdsrouter)"

# --------------------------------------------------------------------------------------
# 4. config.yaml + FDS/MPI detection
# --------------------------------------------------------------------------------------

info "Konfiguration schreiben"
CONFIGURE_ARGS=()
[ -n "$HOST_OVERRIDE" ] && CONFIGURE_ARGS+=(--host "$HOST_OVERRIDE")
[ -n "$PORT_OVERRIDE" ] && CONFIGURE_ARGS+=(--port "$PORT_OVERRIDE")
(cd "$REPO_DIR" && "$VENV_PY" "$REPO_DIR/scripts/configure.py" --service-env ${CONFIGURE_ARGS[@]+"${CONFIGURE_ARGS[@]}"})

# --------------------------------------------------------------------------------------
# 5. Autostart via systemd
# --------------------------------------------------------------------------------------

install_user_service() {
    local unit_dir="$HOME/.config/systemd/user"
    mkdir -p "$unit_dir"
    cat > "$unit_dir/fdsrouter.service" <<UNIT
[Unit]
Description=FDSRouter - Warteschlangen-Steuerung fuer FDS-Simulationen
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/scripts/fdsrouter-service.sh
Restart=on-failure
RestartSec=5
TimeoutStopSec=60

[Install]
WantedBy=default.target
UNIT
    ok "Unit geschrieben: $unit_dir/fdsrouter.service"

    if ! systemctl --user daemon-reload 2>/dev/null; then
        die "systemctl --user ist hier nicht erreichbar (haeufig bei SSH-Sitzungen ohne Benutzer-Session). Stattdessen: ./install.sh --service=system"
    fi
    systemctl --user enable --now fdsrouter.service || die "Dienst konnte nicht gestartet werden. Log: journalctl --user -u fdsrouter -n 50"
    ok "Dienst aktiviert und gestartet"

    # Without lingering a user service stops at logout and does not start at boot.
    if loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q 'Linger=yes'; then
        ok "Linger bereits aktiv -- der Dienst startet auch ohne Anmeldung"
    else
        note "Damit der Dienst schon beim Hochfahren (ohne Anmeldung) läuft, wird 'linger' aktiviert."
        if sudo loginctl enable-linger "$USER"; then
            ok "Linger aktiviert"
        else
            warn "Linger konnte nicht aktiviert werden -- bitte später ausführen: sudo loginctl enable-linger $USER"
        fi
    fi

    note "Status:  systemctl --user status fdsrouter"
    note "Log:     journalctl --user -u fdsrouter -f"
    note "Stoppen: systemctl --user stop fdsrouter"
}

install_system_service() {
    local unit="/etc/systemd/system/fdsrouter.service"
    local group
    group="$(id -gn)"
    sudo tee "$unit" >/dev/null <<UNIT
[Unit]
Description=FDSRouter - Warteschlangen-Steuerung fuer FDS-Simulationen
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
Group=$group
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/scripts/fdsrouter-service.sh
Restart=on-failure
RestartSec=5
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
UNIT
    ok "Unit geschrieben: $unit"

    sudo systemctl daemon-reload
    sudo systemctl enable --now fdsrouter.service || die "Dienst konnte nicht gestartet werden. Log: journalctl -u fdsrouter -n 50"
    ok "Dienst aktiviert und gestartet"

    note "Status:  systemctl status fdsrouter"
    note "Log:     journalctl -u fdsrouter -f"
    note "Stoppen: sudo systemctl stop fdsrouter"
}

if [ "$SERVICE_MODE" = "ask" ]; then
    if have systemctl; then
        info "Autostart einrichten?"
        note "FDSRouter läuft dann dauerhaft im Hintergrund und startet beim Hochfahren des Rechners mit."
        if confirm "systemd-Dienst für den Benutzer $USER einrichten?"; then
            SERVICE_MODE="user"
        else
            SERVICE_MODE="none"
        fi
    else
        SERVICE_MODE="none"
    fi
fi

case "$SERVICE_MODE" in
    user)
        have systemctl || die "systemctl nicht gefunden -- Autostart bitte manuell einrichten (siehe README)."
        info "systemd-Dienst (Benutzer) einrichten"
        install_user_service
        ;;
    system)
        have systemctl || die "systemctl nicht gefunden -- Autostart bitte manuell einrichten (siehe README)."
        info "systemd-Dienst (systemweit) einrichten"
        install_system_service
        ;;
    none)
        : ;;
esac

# --------------------------------------------------------------------------------------
# 6. Summary
# --------------------------------------------------------------------------------------

# --------------------------------------------------------------------------------------
# 5b. Desktop tray icon
# --------------------------------------------------------------------------------------

install_tray() {
    local tray_venv="$REPO_DIR/.venv-tray"

    # pystray's Linux backend talks to the desktop through PyGObject, which is a distribution
    # package (building it from PyPI needs the whole GObject toolchain). Hence a second, small
    # environment with --system-site-packages that can see the apt-installed python3-gi -- the
    # service venv stays untouched and self-contained.
    if [ -n "$APT_GET" ]; then
        apt_install python3-gi gir1.2-ayatanaappindicator3-0.1 || \
            warn "GTK-Pakete konnten nicht installiert werden -- das Tray-Icon bleibt unter GNOME evtl. unsichtbar."
    fi

    if [ ! -f "$tray_venv/pyvenv.cfg" ]; then
        "$PYTHON_BIN" -m venv --system-site-packages "$tray_venv" || {
            warn "Tray-Umgebung konnte nicht angelegt werden -- Tray-Icon uebersprungen."
            return 1
        }
    fi
    "$tray_venv/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
    if ! (cd "$REPO_DIR" && "$tray_venv/bin/python" -m pip install --quiet -e ".[tray]"); then
        warn "pystray/Pillow konnten nicht installiert werden -- Tray-Icon uebersprungen."
        return 1
    fi
    ok "Tray-Umgebung: $tray_venv"

    local autostart_dir="$HOME/.config/autostart"
    mkdir -p "$autostart_dir"
    cat > "$autostart_dir/fdsrouter-tray.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=FDSRouter
Comment=Warteschlangen-Steuerung fuer FDS-Simulationen
Exec=$tray_venv/bin/fdsrouter tray
Path=$REPO_DIR
Icon=$REPO_DIR/fdsrouter/static/icon.svg
Terminal=false
X-GNOME-Autostart-enabled=true
DESKTOP
    ok "Autostart-Eintrag: $autostart_dir/fdsrouter-tray.desktop"
    note "Startet mit der naechsten Anmeldung; sofort starten mit:"
    note "    (cd $REPO_DIR && $tray_venv/bin/fdsrouter tray &)"
}

if [ "$TRAY_MODE" = "ask" ]; then
    # Only worth offering where there is a desktop session to put an icon into.
    if [ -n "${XDG_CURRENT_DESKTOP:-}" ] || [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
        info "Tray-Icon einrichten?"
        note "Kleines Flammen-Symbol in der Menueleiste mit Oeffnen, Neustart, Update und Beenden."
        if confirm "Tray-Icon fuer den Desktop einrichten?"; then TRAY_MODE="yes"; else TRAY_MODE="none"; fi
    else
        TRAY_MODE="none"
    fi
fi

if [ "$TRAY_MODE" = "yes" ]; then
    info "Tray-Icon einrichten"
    install_tray || true
fi

read_config() {  # read_config key default -- read one value out of config.yaml
    "$VENV_PY" - "$REPO_DIR/config.yaml" "$1" "$2" <<'PY'
import pathlib
import sys

import yaml

raw = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")) or {}
value = raw.get(sys.argv[2])
print(sys.argv[3] if value is None else value)
PY
}

PORT="$(read_config port 8000)"
HOST="$(read_config host 127.0.0.1)"
lan_ip() {  # best effort -- hostname -I is Linux-only, the rest covers macOS
    hostname -I 2>/dev/null | awk '{print $1; exit}' && return 0
    ipconfig getifaddr en0 2>/dev/null && return 0
    return 0
}
LAN_IP="$(lan_ip || true)"
[ -n "$LAN_IP" ] || LAN_IP="<server-ip>"

info "Fertig"
if [ "$SERVICE_MODE" = "user" ] || [ "$SERVICE_MODE" = "system" ]; then
    note "Der Dienst läuft bereits und startet künftig automatisch mit."
else
    note "Starten mit:  $VENV_DIR/bin/fdsrouter start"
    note "Oder:         source $VENV_DIR/bin/activate && fdsrouter start"
    note "Autostart später nachrüsten:  ./install.sh --service"
fi

if [ "$HOST" = "127.0.0.1" ] || [ "$HOST" = "localhost" ]; then
    note "Oberfläche: http://localhost:$PORT/ -- nur von diesem Rechner aus erreichbar."
    note "Für die Bedienung von einem anderen Rechner im Netz:"
    note "    ./install.sh --host=0.0.0.0 --no-service --yes"
    if [ "$SERVICE_MODE" = "user" ]; then
        note "    systemctl --user restart fdsrouter"
    elif [ "$SERVICE_MODE" = "system" ]; then
        note "    sudo systemctl restart fdsrouter"
    fi
else
    note "Oberfläche: http://$LAN_IP:$PORT/ (im Netz erreichbar, host: $HOST)"
    if have ufw && sudo -n ufw status 2>/dev/null | grep -q '^Status: active'; then
        warn "Die Firewall ufw ist aktiv -- Port freigeben mit: sudo ufw allow $PORT/tcp"
    fi
fi
printf '\n'
