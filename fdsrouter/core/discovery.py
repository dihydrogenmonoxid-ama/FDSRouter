"""LAN auto-discovery for pairing an Agent with a Controller.

A plain UDP broadcast, not mDNS/Zeroconf: no new dependency, and this only ever needs to answer
"which address is the Controller on this LAN", not full DNS-SD service advertisement. The
cluster_token itself is never sent over this channel -- a discovery reply only reveals that a
Controller exists and where, never anything secret. Pairing still requires the operator to enter
the correct token (see cli.py's interactive `fdsrouter agent` setup), so a stray machine that
merely answers discovery pings still can't join the cluster.
"""

from __future__ import annotations

import json
import logging
import platform
import socket
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DISCOVERY_PORT = 57632
_REQUEST_TYPE = "fdsrouter-discover"
_RESPONSE_TYPE = "fdsrouter-controller"
_MAX_PACKET = 2048
# How often the responder wakes up to check for a shutdown request -- kept short so tearing down
# a Controller (or, in tests, constructing/discarding many apps in a row) isn't gated on it.
_RESPONDER_POLL_S = 0.1


@dataclass
class DiscoveredController:
    host: str
    port: int
    hostname: str


def run_discovery_responder(http_port: int, stop_event: threading.Event) -> None:
    """Blocking loop: answers every discovery broadcast on the LAN with this Controller's own
    address. Meant to run in its own daemon thread for the process lifetime -- plain
    request/reply, no reason to route it through the asyncio event loop or touch the database.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(_RESPONDER_POLL_S)
    try:
        sock.bind(("", DISCOVERY_PORT))
    except OSError:
        logger.warning(
            "discovery: UDP port %s already in use (another FDSRouter Controller on this "
            "machine?) -- auto-discovery disabled for this instance, agents will need the "
            "address entered manually",
            DISCOVERY_PORT,
        )
        sock.close()
        return

    hostname = platform.node()
    try:
        while not stop_event.is_set():
            try:
                raw, addr = sock.recvfrom(_MAX_PACKET)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                payload = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue
            if payload.get("type") != _REQUEST_TYPE:
                continue
            response = json.dumps({"type": _RESPONSE_TYPE, "hostname": hostname, "port": http_port}).encode("utf-8")
            try:
                sock.sendto(response, addr)
            except OSError:
                pass
    finally:
        sock.close()


def discover_controllers(timeout_s: float = 2.0) -> list[DiscoveredController]:
    """Broadcast a discovery request and collect every Controller that answers within
    timeout_s, deduplicated by address. Synchronous and short-lived -- meant for the interactive
    `fdsrouter agent` first-run pairing step, not a background loop."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.5)

    found: dict[tuple[str, int], DiscoveredController] = {}
    try:
        sock.sendto(json.dumps({"type": _REQUEST_TYPE}).encode("utf-8"), ("<broadcast>", DISCOVERY_PORT))
    except OSError as exc:
        logger.warning("discovery broadcast failed: %s", exc)
        sock.close()
        return []

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            raw, addr = sock.recvfrom(_MAX_PACKET)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
        if payload.get("type") != _RESPONSE_TYPE:
            continue
        host = addr[0]
        port = int(payload.get("port") or 8000)
        found[(host, port)] = DiscoveredController(host=host, port=port, hostname=payload.get("hostname") or host)
    sock.close()
    return list(found.values())
