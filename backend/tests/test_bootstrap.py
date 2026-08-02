from __future__ import annotations

import shutil
from pathlib import Path

from agent_control import bootstrap
from agent_control.config import AppSettings
from agent_control.config_sync import read_env_value


REPO_EXAMPLE_CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.example.yaml"


def _isolate(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_SECRET_VAULT_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("YBM_LOCALDEPLOY_ROOT", raising=False)


def test_setup_creates_config_from_example(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir()
    shutil.copy(REPO_EXAMPLE_CONFIG, tmp_path / "config" / "config.example.yaml")

    exit_code = bootstrap.run_setup()

    assert exit_code == 0
    assert (tmp_path / "config" / "config.yaml").exists()


def test_setup_generates_admin_token_and_vault_key(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir()
    shutil.copy(REPO_EXAMPLE_CONFIG, tmp_path / "config" / "config.example.yaml")

    bootstrap.run_setup()

    assert read_env_value("AGENT_ADMIN_TOKEN") is not None
    assert read_env_value("AGENT_SECRET_VAULT_KEY") is not None


def test_setup_is_idempotent_on_generated_secrets(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir()
    shutil.copy(REPO_EXAMPLE_CONFIG, tmp_path / "config" / "config.example.yaml")

    bootstrap.run_setup()
    first_token = read_env_value("AGENT_ADMIN_TOKEN")

    bootstrap.run_setup()
    second_token = read_env_value("AGENT_ADMIN_TOKEN")

    assert first_token == second_token


def test_setup_saves_telegram_token_when_provided(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir()
    shutil.copy(REPO_EXAMPLE_CONFIG, tmp_path / "config" / "config.example.yaml")

    bootstrap.run_setup(telegram_token="test-token-123")

    assert read_env_value("TELEGRAM_BOT_TOKEN") == "test-token-123"


def test_setup_fails_without_example_config(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    exit_code = bootstrap.run_setup()
    assert exit_code == 1


def test_doctor_fails_when_config_missing(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    exit_code = bootstrap.run_doctor()
    assert exit_code == 1


def test_doctor_reports_ok_after_setup(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "config").mkdir()
    shutil.copy(REPO_EXAMPLE_CONFIG, tmp_path / "config" / "config.example.yaml")
    bootstrap.run_setup()

    exit_code = bootstrap.run_doctor()

    assert exit_code == 0


def test_check_admin_token_fails_when_unset_and_non_loopback(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    settings = AppSettings(_env_file=None, server={"host": "0.0.0.0"})
    check = bootstrap._check_admin_token(settings)
    assert check.status == "fail"


def test_check_admin_token_warns_when_unset_and_loopback(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    settings = AppSettings(_env_file=None)
    check = bootstrap._check_admin_token(settings)
    assert check.status == "warn"


def test_check_telegram_ok_when_disabled(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    settings = AppSettings(_env_file=None)
    check = bootstrap._check_telegram(settings)
    assert check.status == "ok"


def test_check_telegram_fails_when_enabled_without_token(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    settings = AppSettings(
        _env_file=None,
        channels={"telegram": {"enabled": True, "token_env": "TELEGRAM_BOT_TOKEN"}},
    )
    check = bootstrap._check_telegram(settings)
    assert check.status == "fail"


def test_desktop_capability_not_requested_by_default(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    settings = AppSettings(_env_file=None)
    assert bootstrap._desktop_capability_requested(settings) is False


# ---- Admin console build fingerprinting (docs/UI_UX_AUDIT.md Phase 10, second review) ----


def _write(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_admin_console_fingerprint_is_stable_when_nothing_changes(tmp_path) -> None:
    frontend_dir = tmp_path / "frontend"
    _write(frontend_dir / "src" / "App.tsx", "const App = () => null")
    _write(frontend_dir / "package.json", "{}")

    first = bootstrap._admin_console_fingerprint(frontend_dir)
    second = bootstrap._admin_console_fingerprint(frontend_dir)

    assert first == second


def test_admin_console_fingerprint_changes_when_a_source_file_is_edited(tmp_path) -> None:
    frontend_dir = tmp_path / "frontend"
    source = frontend_dir / "src" / "App.tsx"
    _write(source, "const App = () => null")
    before = bootstrap._admin_console_fingerprint(frontend_dir)

    _write(source, "const App = () => <div>changed</div>")

    after = bootstrap._admin_console_fingerprint(frontend_dir)
    assert after != before


def test_admin_console_fingerprint_changes_when_a_file_is_added(tmp_path) -> None:
    frontend_dir = tmp_path / "frontend"
    _write(frontend_dir / "src" / "App.tsx", "const App = () => null")
    before = bootstrap._admin_console_fingerprint(frontend_dir)

    _write(frontend_dir / "src" / "NewPage.tsx", "const NewPage = () => null")

    after = bootstrap._admin_console_fingerprint(frontend_dir)
    assert after != before


def _setup_built_console(tmp_path: Path, monkeypatch) -> Path:
    """A repo layout where the admin console was already built once and
    nothing has changed since - the state _build_admin_console should
    recognize as "skip the rebuild", matching backend/'s real relative
    position to frontend/ (both siblings of the repo root, which is this
    function's CWD when run_setup() actually calls it)."""
    monkeypatch.chdir(tmp_path)
    frontend_dir = tmp_path / "frontend"
    _write(frontend_dir / "src" / "App.tsx", "const App = () => null")
    static_dir = tmp_path / "backend" / "src" / "agent_control" / "static" / "admin"
    _write(static_dir / "index.html", "<html></html>")
    fingerprint = bootstrap._admin_console_fingerprint(frontend_dir)
    _write(static_dir / ".ybm_build_fingerprint", fingerprint)
    return frontend_dir


def test_build_admin_console_skips_npm_when_fingerprint_matches(monkeypatch, tmp_path) -> None:
    _setup_built_console(tmp_path, monkeypatch)
    calls: list[list[str]] = []
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    bootstrap._build_admin_console()

    assert calls == []


def test_build_admin_console_rebuilds_when_a_source_file_changed(monkeypatch, tmp_path) -> None:
    frontend_dir = _setup_built_console(tmp_path, monkeypatch)
    _write(frontend_dir / "src" / "App.tsx", "const App = () => <div>edited</div>")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/npm")
    (frontend_dir / "node_modules").mkdir()
    calls: list[list[str]] = []

    class _Result:
        returncode = 0

    monkeypatch.setattr(bootstrap.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _Result())

    bootstrap._build_admin_console()

    assert any(cmd[:2] == ["npm", "run"] for cmd in calls)


def test_build_admin_console_rebuilds_when_no_prior_build_exists(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    frontend_dir = tmp_path / "frontend"
    _write(frontend_dir / "src" / "App.tsx", "const App = () => null")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/npm")
    (frontend_dir / "node_modules").mkdir()
    calls: list[list[str]] = []

    class _Result:
        returncode = 0

    monkeypatch.setattr(bootstrap.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _Result())

    bootstrap._build_admin_console()

    assert any(cmd[:2] == ["npm", "run"] for cmd in calls)
    written = (tmp_path / "backend" / "src" / "agent_control" / "static" / "admin" / ".ybm_build_fingerprint")
    assert written.exists()
    assert written.read_text(encoding="utf-8") == bootstrap._admin_console_fingerprint(frontend_dir)


def test_build_admin_console_writes_fingerprint_to_backend_not_repo_root(monkeypatch, tmp_path) -> None:
    """Regression test: an earlier version of this path was missing the
    backend/ prefix and silently wrote a bogus src/agent_control/static/
    admin/ at the repo root instead of the real
    backend/src/agent_control/static/admin/ - confirmed the hard way
    running scripts/ybm.ps1 run live against this actual repo."""
    monkeypatch.chdir(tmp_path)
    frontend_dir = tmp_path / "frontend"
    _write(frontend_dir / "src" / "App.tsx", "const App = () => null")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/npm")
    (frontend_dir / "node_modules").mkdir()

    class _Result:
        returncode = 0

    monkeypatch.setattr(bootstrap.subprocess, "run", lambda cmd, **kw: _Result())

    bootstrap._build_admin_console()

    assert not (tmp_path / "src").exists()
    assert (tmp_path / "backend" / "src" / "agent_control" / "static" / "admin" / ".ybm_build_fingerprint").exists()
