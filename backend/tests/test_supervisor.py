from __future__ import annotations

from pathlib import Path

from agent_control.supervisor import build_service_specs


def _isolate(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("YBM_LOCALDEPLOY_ROOT", raising=False)


def _write_config(tmp_path: Path, contents: str) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(contents, encoding="utf-8")


def test_build_service_specs_excludes_whatsapp_when_disabled_by_default(monkeypatch, tmp_path) -> None:
    """No config.yaml at all -> AppSettings defaults -> whatsapp.enabled is
    False. Unlike telegram_polling (always attempted, required=True), a
    disabled-by-default WhatsApp service must not even be attempted -
    poll-whatsapp refuses to start when disabled, so attempting it anyway
    would crash-loop 4 times on every single `ybm start` for every user who
    has never touched the feature (docs/UI_UX_AUDIT.md Phase 16 review)."""
    _isolate(monkeypatch, tmp_path)

    specs = build_service_specs()

    by_name = {spec.name: spec for spec in specs}
    assert "whatsapp" not in by_name
    assert by_name["telegram_polling"].required is True


def test_build_service_specs_includes_whatsapp_as_not_required_when_enabled(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    _write_config(tmp_path, "channels:\n  whatsapp:\n    enabled: true\n")

    specs = build_service_specs()

    by_name = {spec.name: spec for spec in specs}
    assert "whatsapp" in by_name
    assert by_name["whatsapp"].required is False
    assert by_name["whatsapp"].args[-1] == "poll-whatsapp"


def test_build_service_specs_no_whatsapp_excludes_the_service_even_when_enabled(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    _write_config(tmp_path, "channels:\n  whatsapp:\n    enabled: true\n")

    specs = build_service_specs(no_whatsapp=True)

    assert "whatsapp" not in {spec.name for spec in specs}


def test_build_service_specs_excludes_whatsapp_when_config_is_unreadable(monkeypatch, tmp_path) -> None:
    """Fail closed: a broken config.yaml must not crash `ybm start` itself
    just from trying to decide whether to attempt WhatsApp - `ybm doctor`
    (which runs first, unless -SkipDoctor) is where that belongs."""
    _isolate(monkeypatch, tmp_path)
    _write_config(tmp_path, "channels: [this is not a mapping")

    specs = build_service_specs()

    assert "whatsapp" not in {spec.name for spec in specs}
