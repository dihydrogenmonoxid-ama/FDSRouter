"""read_fan_speed_rpm: highest reported RPM across sensors, best-effort."""

from collections import namedtuple

import psutil

from fdsrouter.core import fans

_Entry = namedtuple("_Entry", ["label", "current"])


def _patch_fans(monkeypatch, fn):
    # sensors_fans doesn't exist on the module at all on macOS -- raising=False lets
    # monkeypatch add it anyway rather than requiring the attribute to already exist.
    monkeypatch.setattr(psutil, "sensors_fans", fn, raising=False)


def test_no_sensors_available_returns_none(monkeypatch):
    monkeypatch.delattr(psutil, "sensors_fans", raising=False)
    assert fans.read_fan_speed_rpm() is None


def test_empty_returns_none(monkeypatch):
    _patch_fans(monkeypatch, lambda: {})
    assert fans.read_fan_speed_rpm() is None


def test_returns_highest_reported_speed(monkeypatch):
    _patch_fans(monkeypatch, lambda: {"chip": [_Entry("fan1", 1200), _Entry("fan2", 2100)]})
    assert fans.read_fan_speed_rpm() == 2100


def test_sensor_error_returns_none(monkeypatch):
    def _raise():
        raise OSError("no sensors")

    _patch_fans(monkeypatch, _raise)
    assert fans.read_fan_speed_rpm() is None
