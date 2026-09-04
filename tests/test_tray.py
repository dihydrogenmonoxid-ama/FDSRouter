"""Tray logic that does not need a display: which service it talks to, and how it reads the
answers. pystray and Pillow are optional extras, so nothing here may import them."""

from pathlib import Path

from fdsrouter import tray


def test_service_url_comes_from_the_config_file(tmp_path):
    (tmp_path / "config.yaml").write_text("host: 127.0.0.1\nport: 8123\n", encoding="utf-8")

    assert tray.service_url(tmp_path) == "http://127.0.0.1:8123"


def test_a_wildcard_bind_is_not_used_as_a_connect_address(tmp_path):
    # 0.0.0.0 says "listen everywhere", it is not an address to talk to.
    (tmp_path / "config.yaml").write_text("host: 0.0.0.0\nport: 9000\n", encoding="utf-8")

    assert tray.service_url(tmp_path) == "http://127.0.0.1:9000"


def test_missing_config_falls_back_to_the_default(tmp_path):
    assert tray.service_url(tmp_path) == "http://127.0.0.1:8000"


def _app(monkeypatch, post_result):
    app = tray.TrayApp("http://127.0.0.1:8000")
    monkeypatch.setattr(app, "_post", lambda *a, **k: post_result)
    monkeypatch.setattr(app, "_wait_for_service_back", lambda: None)
    monkeypatch.setattr(app, "refresh", lambda: None)
    return app


def test_a_dropped_answer_to_restart_counts_as_the_restart_happening(monkeypatch):
    app = _app(monkeypatch, (False, tray.TRANSPORT_ERROR))

    app.restart_service()

    assert "restarting" in app.state.message


def test_a_refused_restart_is_reported_as_it_came_back(monkeypatch):
    app = _app(monkeypatch, (False, "A job is running - confirm in the web interface"))

    app.restart_service()

    assert app.state.message == "A job is running - confirm in the web interface"


def test_stop_without_an_answer_is_still_a_stop(monkeypatch):
    app = _app(monkeypatch, (False, tray.TRANSPORT_ERROR))

    app.stop_service()

    assert app.state.message == "Service stopped"
