"""Preflight (`doctor`) and first-run (`setup`) checks for the YBM stack.

These exist so a missing dependency, missing config, or unreachable local
service produces one readable line instead of a stack trace three layers
deep in a supervised background process (see docs/HISTORY.md P0).
"""

from __future__ import annotations

import importlib.util
import secrets as secrets_module
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from agent_control.config import AppSettings, is_loopback_host, load_settings
from agent_control.config_sync import ConfigManager, read_env_value
from agent_control.schemas import Capability
from agent_control.storage.database import Database
from agent_control.storage.secrets import SecretVault


REQUIRED_MODULES = [
    "fastapi", "cryptography", "httpx", "json_repair", "mcp", "pandas",
    "pydantic", "pydantic_settings", "PIL", "pypdf", "yaml", "streamlit",
    "structlog", "uvicorn", "websocket",
]
DESKTOP_MODULES = ["mss", "pyautogui", "pygetwindow", "pywinauto"]
STATUS_SYMBOL = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]"}


@dataclass
class Check:
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str = ""


def _check_python() -> Check:
    v = sys.version_info
    version = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 12):
        return Check("Python version", "ok", version)
    return Check("Python version", "fail", f"{version} - YBM requires >=3.12")


def _check_venv() -> Check:
    exe = Path(sys.executable)
    in_venv = sys.prefix != sys.base_prefix
    normalized = str(exe).replace("\\", "/")
    if in_venv and "backend/.venv" in normalized:
        return Check("Virtual environment", "ok", str(exe))
    if in_venv:
        return Check("Virtual environment", "warn", f"active venv is not backend/.venv: {exe}")
    return Check("Virtual environment", "warn", f"not running inside backend/.venv ({exe}); run `ybm setup`")


def _check_modules() -> list[Check]:
    return [
        Check(f"module: {mod}", "ok" if importlib.util.find_spec(mod) else "fail",
              "" if importlib.util.find_spec(mod) else "not installed - run `ybm setup`")
        for mod in REQUIRED_MODULES
    ]


def _desktop_capability_requested(settings: AppSettings) -> bool:
    for capability in (Capability.DESKTOP_SCREENSHOT, Capability.DESKTOP_CONTROL):
        policy = settings.capabilities.get(capability)
        if policy is not None and policy.enabled:
            return True
    return bool(settings.adapters.computer_use.enabled)


def _check_desktop_modules(settings: AppSettings) -> list[Check]:
    if not _desktop_capability_requested(settings):
        return [Check("Desktop control modules", "ok", "not requested by config - skipped")]
    missing = [mod for mod in DESKTOP_MODULES if importlib.util.find_spec(mod) is None]
    if not missing:
        return [Check("Desktop control modules", "ok", "installed")]
    return [Check(
        "Desktop control modules", "fail",
        f"desktop capability enabled but missing: {', '.join(missing)} - run `ybm setup --desktop`",
    )]


def _load_settings_checked() -> tuple[Check, AppSettings | None]:
    path = Path("config/config.yaml")
    if not path.exists():
        return Check("config/config.yaml", "fail", "missing - run `ybm setup`"), None
    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001 - reporting, not handling
        return Check("config/config.yaml", "fail", f"invalid: {exc}"), None
    return Check("config/config.yaml", "ok", "loaded"), settings


def _check_db(settings: AppSettings) -> Check:
    try:
        database = Database(settings.storage.database_url)
        database.initialize()
        return Check("Database", "ok", settings.storage.database_url)
    except Exception as exc:  # noqa: BLE001 - reporting, not handling
        return Check("Database", "fail", str(exc))


def _port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            return True
        except OSError:
            return False


def _check_ports() -> list[Check]:
    checks = []
    for name, port in (("LocalDeploy", 8000), ("Backend", 8765), ("Admin UI", 8501)):
        listening = _port_listening(port)
        checks.append(Check(
            f"Port {port} ({name})", "ok" if listening else "warn",
            "listening" if listening else "free - not running yet",
        ))
    return checks


def _http_ok(url: str, timeout: float = 6.0) -> bool:
    """Liveness probe for the doctor checks.

    Was 2.0s, which is under how long a real reply takes: LocalDeploy's
    /health enumerates Ollama's installed models before responding and
    measured ~2.06s on this machine. Doctor therefore reported "LocalDeploy
    not reachable ... fallback profile 'openai_saved' will be used" in the
    same run where it reported port 8000 listening - telling the user their
    local model was down and a paid API would be billed instead, when
    neither was true.
    """
    try:
        with urlopen(url, timeout=timeout) as resp:  # noqa: S310 - local health check only
            return 200 <= resp.status < 300
    except (URLError, OSError, ValueError):
        return False


def _check_localdeploy(settings: AppSettings) -> Check:
    profile = settings.llm.profiles.get(settings.llm.default_profile)
    base_url = profile.base_url if profile else None
    if not base_url or not any(host in base_url for host in ("127.0.0.1", "localhost")):
        return Check("LocalDeploy", "ok", "default LLM profile is not local - skipped")
    health_url = base_url.rsplit("/v1", 1)[0].rstrip("/") + "/health"
    if _http_ok(health_url):
        return Check("LocalDeploy", "ok", f"reachable at {health_url}")
    root = read_env_value("YBM_LOCALDEPLOY_ROOT")
    detail = "not reachable" + (f" (YBM_LOCALDEPLOY_ROOT={root})" if root else " (YBM_LOCALDEPLOY_ROOT not set)")
    if settings.llm.fallback_profile:
        detail += f"; fallback profile '{settings.llm.fallback_profile}' will be used"
    else:
        detail += "; no fallback_profile configured, LLM calls will fail"
    return Check("LocalDeploy", "warn", detail)


def _check_telegram(settings: AppSettings) -> Check:
    if not settings.channels.telegram.enabled:
        return Check("Telegram", "ok", "disabled in config")
    if read_env_value(settings.channels.telegram.token_env):
        return Check("Telegram", "ok", "token present")
    return Check("Telegram", "fail", f"enabled but {settings.channels.telegram.token_env} is not set")


def _check_admin_token(settings: AppSettings) -> Check:
    if read_env_value(settings.server.admin_token_env):
        return Check("Admin token", "ok", "set")
    if is_loopback_host(settings.server.host):
        return Check(
            "Admin token",
            "warn",
            "not set - admin API trusts any loopback caller (cross-origin requests are still "
            "rejected regardless; run `ybm setup` to generate a token for defense in depth)",
        )
    return Check("Admin token", "fail", f"not set and server.host={settings.server.host} is not loopback-only")


def _check_vault(settings: AppSettings) -> Check:
    if read_env_value(settings.secrets.key_env):
        return Check("Secret vault key", "ok", "set")
    return Check("Secret vault key", "warn", f"{settings.secrets.key_env} not set - run `ybm setup`")


def collect_checks() -> list[Check]:
    checks: list[Check] = [_check_python(), _check_venv(), *_check_modules()]
    config_check, settings = _load_settings_checked()
    checks.append(config_check)
    if settings is not None:
        checks.extend(_check_desktop_modules(settings))
        checks.append(_check_db(settings))
        checks.append(_check_localdeploy(settings))
        checks.append(_check_telegram(settings))
        checks.append(_check_admin_token(settings))
        checks.append(_check_vault(settings))
    checks.extend(_check_ports())
    return checks


def run_doctor() -> int:
    checks = collect_checks()
    width = max(len(c.name) for c in checks)
    for check in checks:
        line = f"{STATUS_SYMBOL[check.status]} {check.name.ljust(width)}"
        if check.detail:
            line += f"  {check.detail}"
        print(line)

    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]
    oks = len(checks) - len(fails) - len(warns)
    print()
    print(f"{oks} ok, {len(warns)} warning(s), {len(fails)} failure(s)")
    if fails:
        print("Fix the failures above (or run `ybm setup`) before starting the stack.")
        return 1
    return 0


def run_setup(*, telegram_token: str | None = None) -> int:
    print("YBM setup")
    print("=========")

    config_manager = ConfigManager()
    config_path = Path("config/config.yaml")
    if not config_path.exists():
        example_path = Path("config/config.example.yaml")
        if not example_path.exists():
            print("FAIL: config/config.example.yaml is missing - cannot bootstrap config.")
            return 1
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"created {config_path} from config.example.yaml (every capability starts disabled)")
    else:
        print(f"{config_path} already exists - leaving it as is")

    env_updates: dict[str, str] = {}
    if not read_env_value("AGENT_ADMIN_TOKEN"):
        env_updates["AGENT_ADMIN_TOKEN"] = secrets_module.token_urlsafe(32)
        print("generated AGENT_ADMIN_TOKEN")
    else:
        print("AGENT_ADMIN_TOKEN already set")

    if not read_env_value("AGENT_SECRET_VAULT_KEY"):
        env_updates["AGENT_SECRET_VAULT_KEY"] = SecretVault.generate_key()
        print("generated AGENT_SECRET_VAULT_KEY")
    else:
        print("AGENT_SECRET_VAULT_KEY already set")

    if telegram_token:
        env_updates["TELEGRAM_BOT_TOKEN"] = telegram_token
        print("saved TELEGRAM_BOT_TOKEN")
    elif not read_env_value("TELEGRAM_BOT_TOKEN"):
        print("NOTE: TELEGRAM_BOT_TOKEN not set - pass `ybm setup --telegram-token <token>` "
              "or set it in .env, then enable channels.telegram in config/config.yaml")

    if env_updates:
        config_manager.upsert_env(env_updates)

    if not read_env_value("YBM_LOCALDEPLOY_ROOT"):
        print("NOTE: YBM_LOCALDEPLOY_ROOT is not set - set it in .env if you run a local "
              "LocalDeploy checkout, otherwise point llm.profiles at a reachable "
              "OpenAI-compatible endpoint in config/config.yaml.")

    database = Database(load_settings().storage.database_url)
    database.initialize()
    print(f"database ready at {load_settings().storage.database_url}")

    print()
    print("Next: `ybm doctor` to verify the environment, then `ybm start`.")
    return 0
