"""read_cpu_temperature: prefers a CPU-labeled sensor over an arbitrary first reading."""

from collections import namedtuple

import psutil

from fdsrouter.core import temperature

_Entry = namedtuple("_Entry", ["label", "current", "high", "critical"])


def _patch_sensors(monkeypatch, fn):
    # sensors_temperatures doesn't exist on the module at all on macOS -- raising=False lets
    # monkeypatch add it anyway rather than requiring the attribute to already exist.
    monkeypatch.setattr(psutil, "sensors_temperatures", fn, raising=False)


def test_disabled_returns_none(monkeypatch):
    _patch_sensors(monkeypatch, lambda: {"coretemp": [_Entry("Package", 55.0, None, None)]})
    assert temperature.read_cpu_temperature(False) is None


def test_no_sensors_available_returns_none(monkeypatch):
    monkeypatch.delattr(psutil, "sensors_temperatures", raising=False)
    assert temperature.read_cpu_temperature(True) is None


def test_empty_readings_returns_none(monkeypatch):
    _patch_sensors(monkeypatch, lambda: {})
    assert temperature.read_cpu_temperature(True) is None


def test_prefers_coretemp_chip_over_other_sensors(monkeypatch):
    _patch_sensors(
        monkeypatch,
        lambda: {
            "nvme": [_Entry("Composite", 38.0, None, None)],
            "coretemp": [_Entry("Package id 0", 61.5, None, None)],
        },
    )
    assert temperature.read_cpu_temperature(True) == 61.5


def test_falls_back_to_first_reading_when_nothing_matches(monkeypatch):
    _patch_sensors(monkeypatch, lambda: {"nvme": [_Entry("Composite", 38.0, None, None)]})
    assert temperature.read_cpu_temperature(True) == 38.0


def test_sensor_error_returns_none(monkeypatch):
    def _raise():
        raise OSError("no sensors")

    _patch_sensors(monkeypatch, _raise)
    assert temperature.read_cpu_temperature(True) is None
