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


def test_sysfs_is_used_when_psutil_reports_nothing(monkeypatch, tmp_path):
    """psutil only reports hwmon chips that expose a `name` file -- the direct scan catches
    the boards it skips, which is why fan speeds stayed empty on some Linux machines."""
    _patch_fans(monkeypatch, lambda: {})
    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "fan1_input").write_text("980\n")
    (hwmon / "fan2_input").write_text("1640\n")
    monkeypatch.setattr(fans, "HWMON_PATTERNS", (str(tmp_path / "hwmon*/fan*_input"),))

    reading = fans.read_fan_speed()

    assert reading.rpm == 1640
    assert reading.source == "hwmon"


def test_unreadable_sysfs_entry_is_skipped(monkeypatch, tmp_path):
    _patch_fans(monkeypatch, lambda: {})
    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / "fan1_input").write_text("not-a-number\n")
    (hwmon / "fan2_input").write_text("1200\n")
    monkeypatch.setattr(fans, "HWMON_PATTERNS", (str(tmp_path / "hwmon*/fan*_input"),))

    assert fans.read_fan_speed().rpm == 1200


def test_missing_sensors_are_reported_with_a_reason(monkeypatch, tmp_path):
    _patch_fans(monkeypatch, lambda: {})
    monkeypatch.setattr(fans, "HWMON_PATTERNS", (str(tmp_path / "nothing*/fan*_input"),))
    monkeypatch.setattr(fans.platform, "system", lambda: "Linux")

    reading = fans.read_fan_speed()

    assert reading.rpm is None
    assert reading.reason == "no_sensors"


def test_platforms_without_fan_access_say_so(monkeypatch, tmp_path):
    _patch_fans(monkeypatch, lambda: {})
    monkeypatch.setattr(fans, "HWMON_PATTERNS", (str(tmp_path / "nothing*/fan*_input"),))
    monkeypatch.setattr(fans.platform, "system", lambda: "Darwin")

    assert fans.read_fan_speed().reason == "unsupported_platform"
