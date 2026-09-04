"""System tray icon for the FDSRouter service.

Runs in the operator's desktop session, not inside the service: the service is started by
systemd at boot, before anyone logs in, and has no display to draw on. The tray therefore talks
to the running service over its own HTTP API, which also means it behaves identically whether
the service runs as a user unit, a system unit, or was started by hand.

Linux is the target (install.sh registers it as an XDG autostart entry). pystray also has
macOS and Windows backends, so `fdsrouter tray` works there too, but it is not part of the
supported setup.

Stopping or restarting is deliberately sent WITHOUT the force flag: the API then refuses while
a simulation is running, and the tray says so instead of ending a twelve-hour run from a menu
that cannot ask for confirmation.
"""

from __future__ import annotations

import logging
import sys
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 10.0
REQUEST_TIMEOUT_S = 10.0
UPDATE_TIMEOUT_S = 600.0

ACCENT = (255, 106, 61, 255)
ACCENT_INNER = (255, 209, 128, 255)
OFFLINE = (123, 132, 148, 255)
OFFLINE_INNER = (180, 186, 196, 255)

# The flame from static/icon.svg as cubic Bezier segments, in the same 64x64 coordinate space.
OUTER_FLAME = (
    ((36, 4), (47, 17), (52, 27), (48, 38)),
    ((48, 38), (46, 52), (39, 61), (31, 61)),
    ((31, 61), (22, 61), (14, 53), (14, 41)),
    ((14, 41), (14, 31), (20, 26), (23, 17)),
    ((23, 17), (26, 26), (31, 28), (31, 21)),
    ((31, 21), (31, 14), (33, 9), (36, 4)),
)
INNER_FLAME = (
    ((35, 29), (40, 36), (42, 42), (39, 48)),
    ((39, 48), (37, 55), (34, 58), (31, 58)),
    ((31, 58), (28, 58), (25, 54), (25, 47)),
    ((25, 47), (25, 41), (32, 38), (35, 29)),
)


@dataclass
class ServiceState:
    reachable: bool = False
    running_job: str | None = None
    queued: int = 0
    revision: str | None = None
    message: str = ""

    @property
    def summary(self) -> str:
        if not self.reachable:
            return "FDSRouter - not reachable"
        if self.running_job:
            return f"FDSRouter - running: {self.running_job}"
        return f"FDSRouter - idle ({self.queued} queued)"


def _bezier_points(p0, p1, p2, p3, steps: int = 48):
    for index in range(steps + 1):
        t = index / steps
        u = 1 - t
        yield (
            u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
            u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1],
        )


def _flame_polygon(segments, scale: float) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for segment in segments:
        points.extend(_bezier_points(*segment))
    return [(x * scale, y * scale) for x, y in points]


def render_icon(size: int = 64, reachable: bool = True):
    """Draw the flame at the requested size; greyed out while the service is unreachable."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    scale = size / 64
    draw.polygon(_flame_polygon(OUTER_FLAME, scale), fill=ACCENT if reachable else OFFLINE)
    draw.polygon(_flame_polygon(INNER_FLAME, scale), fill=ACCENT_INNER if reachable else OFFLINE_INNER)
    return image


def service_url(project_dir: Path) -> str:
    """Read host/port out of config.yaml without creating one -- the tray only observes."""
    host, port = "127.0.0.1", 8000
    config_path = project_dir / "config.yaml"
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        port = int(raw.get("port", port))
        configured = str(raw.get("host", host))
        # 0.0.0.0 is a bind address, not something to connect to.
        host = "127.0.0.1" if configured in ("0.0.0.0", "::", "") else configured
    except (OSError, ValueError, yaml.YAMLError):
        pass
    return f"http://{host}:{port}"


class TrayApp:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.state = ServiceState()
        self.icon = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ API calls

    def _get(self, path: str) -> dict | list | None:
        try:
            response = httpx.get(f"{self.base_url}{path}", timeout=REQUEST_TIMEOUT_S)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError):
            return None

    def _post(self, path: str, timeout: float = REQUEST_TIMEOUT_S) -> tuple[bool, str]:
        """(ok, message) -- a 409 means a job is running and the action was refused."""
        try:
            response = httpx.post(f"{self.base_url}{path}", json={"force": False}, timeout=timeout)
        except httpx.HTTPError as exc:
            return False, f"Service not reachable ({exc.__class__.__name__})"
        if response.status_code == 409:
            return False, "A job is running - confirm in the web interface"
        if response.status_code >= 400:
            detail = ""
            try:
                detail = response.json().get("detail", "")
            except ValueError:
                pass
            return False, detail or f"Failed (HTTP {response.status_code})"
        return True, "ok"

    # ------------------------------------------------------------------ state

    def refresh(self) -> None:
        jobs = self._get("/api/jobs")
        if jobs is None:
            self.state = ServiceState(reachable=False, message=self.state.message)
        else:
            running = next((job for job in jobs if job.get("status") == "running"), None)
            service = self._get("/api/service") or {}
            self.state = ServiceState(
                reachable=True,
                running_job=running.get("name") if running else None,
                queued=sum(1 for job in jobs if job.get("status") == "queued"),
                revision=service.get("revision"),
                message=self.state.message,
            )
        self._apply_state()

    def _apply_state(self) -> None:
        if self.icon is None:
            return
        self.icon.icon = render_icon(64, self.state.reachable)
        self.icon.title = self.state.summary
        self.icon.update_menu()

    def _notify(self, message: str) -> None:
        """Desktop notification where the backend supports one, menu text otherwise."""
        self.state.message = message
        try:
            if self.icon is not None:
                self.icon.notify(message, "FDSRouter")
        except Exception:  # notification support is backend-dependent, never fatal here
            logger.debug("tray notification unavailable: %s", message)
        self._apply_state()

    # ------------------------------------------------------------------ actions

    def open_interface(self) -> None:
        webbrowser.open(f"{self.base_url}/")

    def restart_service(self) -> None:
        ok, message = self._post("/api/service/restart")
        self._notify("Service is restarting" if ok else message)

    def stop_service(self) -> None:
        ok, message = self._post("/api/service/stop")
        self._notify("Service stopped" if ok else message)

    def update_service(self) -> None:
        self._notify("Update running...")
        ok, message = self._post("/api/service/update", timeout=UPDATE_TIMEOUT_S)
        self._notify("Updated, service is restarting" if ok else message)

    def quit(self) -> None:
        self._stop.set()
        if self.icon is not None:
            self.icon.stop()

    # ------------------------------------------------------------------ run loop

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:  # a tray icon must never die of a failed poll
                logger.exception("tray poll failed")
            self._stop.wait(POLL_INTERVAL_S)

    def build_menu(self):
        import pystray

        def status_text(_item) -> str:
            return self.state.summary

        def message_text(_item) -> str:
            return self.state.message or "-"

        return pystray.Menu(
            pystray.MenuItem(status_text, None, enabled=False),
            pystray.MenuItem(message_text, None, enabled=False, visible=lambda _i: bool(self.state.message)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open interface", lambda: self.open_interface(), default=True),
            pystray.MenuItem("Restart service", lambda: self.restart_service()),
            pystray.MenuItem("Update and restart", lambda: self.update_service()),
            pystray.MenuItem("Stop service", lambda: self.stop_service()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit tray icon", lambda: self.quit()),
        )

    def run(self) -> int:
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            print(
                "Das Tray-Icon benoetigt pystray und Pillow.\n"
                "Installation: ./install.sh --tray  (richtet auch den Autostart ein)",
                file=sys.stderr,
            )
            return 2

        import pystray

        self.icon = pystray.Icon("fdsrouter", render_icon(64, False), "FDSRouter", menu=self.build_menu())
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self.icon.run()
        return 0


def main(base_url: str | None = None, project_dir: Path | None = None) -> int:
    url = base_url or service_url(project_dir or Path.cwd())
    logger.info("tray talking to %s", url)
    return TrayApp(url).run()


if __name__ == "__main__":
    raise SystemExit(main())
