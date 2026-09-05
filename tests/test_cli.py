"""`fdsrouter start`'s role auto-detection -- the one-command-everywhere UX (config.yaml's role
starts "auto" and gets decided, and persisted, on first start)."""

import fdsrouter.cli as cli
from fdsrouter.config import Config, load_config
from fdsrouter.core import discovery


def _config(tmp_path, **overrides):
    cfg = Config(project_dir=tmp_path, role="auto")
    cfg.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def test_non_interactive_start_always_becomes_controller(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    calls = []
    monkeypatch.setattr(discovery, "discover_controllers", lambda timeout_s=2.0: calls.append(1) or [])

    config = _config(tmp_path)
    cli._decide_role(config)

    assert config.role == "controller"
    assert calls == []  # never even bothered to look -- a systemd unit must not hang or guess

    reloaded = load_config(tmp_path)
    assert reloaded.role == "controller"


def test_interactive_start_with_no_controller_found_becomes_controller(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(discovery, "discover_controllers", lambda timeout_s=2.0: [])

    config = _config(tmp_path)
    cli._decide_role(config)

    assert config.role == "controller"
    reloaded = load_config(tmp_path)
    assert reloaded.role == "controller"


def test_interactive_start_can_decline_and_stay_a_controller(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        discovery, "discover_controllers",
        lambda timeout_s=2.0: [discovery.DiscoveredController(host="192.168.1.5", port=8000, hostname="other-mac")],
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": "0")

    config = _config(tmp_path)
    cli._decide_role(config)

    assert config.role == "controller"
    reloaded = load_config(tmp_path)
    assert reloaded.role == "controller"


def test_interactive_start_can_join_a_found_controller(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        discovery, "discover_controllers",
        lambda timeout_s=2.0: [discovery.DiscoveredController(host="192.168.1.5", port=8000, hostname="other-mac")],
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")

    config = _config(tmp_path)
    cli._decide_role(config)

    assert config.role == "agent"
    assert config.controller_url == "http://192.168.1.5:8000"
    # Not persisted yet -- only once pairing (token entry) actually succeeds.
    reloaded = load_config(tmp_path)
    assert reloaded.role == "auto"


def test_existing_config_without_role_self_heals_to_controller(tmp_path):
    (tmp_path / "config.yaml").write_text("host: 127.0.0.1\nport: 8000\ncluster_token: abc\n")

    config = load_config(tmp_path)

    assert config.role == "controller"
    reloaded_again = load_config(tmp_path)
    assert reloaded_again.role == "controller"


def test_fresh_config_defaults_to_auto(tmp_path):
    config = load_config(tmp_path)
    assert config.role == "auto"
