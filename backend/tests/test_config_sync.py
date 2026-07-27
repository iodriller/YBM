from __future__ import annotations

from pathlib import Path

import yaml

from agent_control.config_sync import parse_scalar, set_config_path


def _write_minimal_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"server": {"port": 8765}}), encoding="utf-8")


def test_set_config_path_writes_nested_value(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _write_minimal_config(tmp_path / "config" / "config.yaml")

    ok, message = set_config_path("server.port", "9999")

    assert ok is True
    written = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))
    assert written["server"]["port"] == 9999
    assert "server.port" in message


def test_set_config_path_creates_missing_nested_sections(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _write_minimal_config(tmp_path / "config" / "config.yaml")

    ok, _ = set_config_path("scheduler.enabled", "true")

    assert ok is True
    written = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))
    assert written["scheduler"]["enabled"] is True


def test_parse_scalar_coerces_bool_int_float_string() -> None:
    assert parse_scalar("true") is True
    assert parse_scalar("false") is False
    assert parse_scalar("42") == 42
    assert parse_scalar("1.5") == 1.5
    assert parse_scalar("hello") == "hello"


def test_set_config_path_writes_bool_and_int_fields(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _write_minimal_config(tmp_path / "config" / "config.yaml")

    ok_bool, _ = set_config_path("scheduler.enabled", "false")
    ok_int, _ = set_config_path("server.port", "9000")

    assert ok_bool is True
    assert ok_int is True
    written = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))
    assert written["scheduler"]["enabled"] is False
    assert written["server"]["port"] == 9000


def test_set_config_path_reverts_on_invalid_result(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _write_minimal_config(tmp_path / "config" / "config.yaml")

    ok, message = set_config_path("totally_unknown_top_level_section.foo", "bar")

    assert ok is False
    assert "reverted" in message
    written = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))
    assert "totally_unknown_top_level_section" not in written


def test_set_config_path_rejects_empty_path(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _write_minimal_config(tmp_path / "config" / "config.yaml")

    ok, message = set_config_path("", "value")

    assert ok is False
    assert "non-empty" in message


def test_set_config_path_works_with_no_existing_config(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()

    ok, _ = set_config_path("scheduler.enabled", "false")

    assert ok is True
    written = yaml.safe_load((tmp_path / "config" / "config.yaml").read_text(encoding="utf-8"))
    assert written["scheduler"]["enabled"] is False
