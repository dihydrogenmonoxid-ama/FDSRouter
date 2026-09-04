"""LAN discovery: a real UDP broadcast round-trip on localhost, not mocked -- this is exactly
the kind of thing that looks right in isolation but breaks on socket option/platform details."""

import threading
import time

import pytest

from fdsrouter.core import discovery


@pytest.fixture
def responder():
    stop = threading.Event()
    thread = threading.Thread(target=discovery.run_discovery_responder, args=(8123, stop), daemon=True)
    thread.start()
    time.sleep(0.2)  # let the socket actually bind before the test broadcasts
    yield
    stop.set()
    thread.join(timeout=2)


def test_discover_finds_a_running_responder(responder):
    found = discovery.discover_controllers(timeout_s=1.5)
    assert any(c.port == 8123 for c in found)


def test_discovery_reply_never_contains_a_token(responder):
    """The whole point of "discovery, token as auth" -- discovery may reveal presence and
    address, never anything secret."""
    import json
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(1.5)
    sock.sendto(json.dumps({"type": "fdsrouter-discover"}).encode("utf-8"), ("<broadcast>", discovery.DISCOVERY_PORT))
    raw, _ = sock.recvfrom(2048)
    sock.close()
    payload = json.loads(raw.decode("utf-8"))
    assert "token" not in payload
    assert "cluster_token" not in payload
    assert set(payload) == {"type", "hostname", "port"}


def test_discover_returns_nothing_when_no_responder_is_running():
    found = discovery.discover_controllers(timeout_s=0.3)
    assert found == []


def test_discover_deduplicates_by_address(responder):
    found = discovery.discover_controllers(timeout_s=1.5)
    addresses = [(c.host, c.port) for c in found]
    assert len(addresses) == len(set(addresses))
