"""System tray icon for YBM Control (docs/UI_UX_AUDIT.md Phase 6).

A thin GUI shell around the existing, tested scripts/ybm.ps1 - this file
has no process-supervision logic of its own (AGENTS.md: "scripts/ybm.ps1
is the public lifecycle interface. Keep service scripts behind it"). It
only opens the admin console, shells out to ybm.ps1 start/stop/restart/
status, and shows the result as a notification balloon.

Run directly: backend/.venv/Scripts/python.exe scripts/tray_app.py
(the ``tray`` extra - see backend/pyproject.toml - must be installed;
``ybm setup`` already includes it).
"""

from __future__ import annotations

import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import yaml
from PIL import Image, ImageDraw

try:
    import pystray
except ImportError:
    print(
        "pystray is not installed. Run 'uv sync --extra tray' in backend/ "
        "(or 'ybm setup', which already includes it) and try again.",
        file=sys.stderr,
    )
    raise SystemExit(1) from None

REPO_ROOT = Path(__file__).resolve().parent.parent
YBM_PS1 = REPO_ROOT / "scripts" / "ybm.ps1"
LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo_256.png"
APP_NAME = "YBM Control"


def _admin_port() -> int:
    """Best-effort port lookup so "Open Admin Console" hits the right URL
    even when server.port was customized - falls back to the documented
    default rather than failing if config.yaml doesn't exist yet."""
    for candidate in (REPO_ROOT / "config" / "config.yaml", REPO_ROOT / "config" / "config.example.yaml"):
        if not candidate.exists():
            continue
        try:
            data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            port = data.get("server", {}).get("port")
            if isinstance(port, int):
                return port
        except (yaml.YAMLError, OSError):
            continue
    return 8765


def _make_icon_image() -> Image.Image:
    """The real mark (docs/UI_UX_AUDIT.md Phase 10), not a placeholder -
    scripts/assets/logo_256.png is a rasterized copy of
    frontend/public/favicon.svg (the same purple/blue bolt already shown
    in the browser tab), so the tray icon and the browser tab agree
    instead of the tray showing a generic Lucide-icon badge nothing else
    in the product uses. Falls back to a plain generated badge only if the
    asset is somehow missing, so a packaging mistake degrades visibly
    rather than crashing the tray app outright.
    """
    if LOGO_PATH.exists():
        return Image.open(LOGO_PATH).convert("RGBA")
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, 62, 62), fill=(126, 20, 255, 255))
    return image


def _run_ybm(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(YBM_PS1), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )


def _run_ybm_async(icon: "pystray.Icon", verb: str, *args: str) -> None:
    """ybm.ps1 start/stop/restart can genuinely take a minute or more
    (LocalDeploy model load) - never block the tray's event loop on it."""

    def worker() -> None:
        icon.notify(f"{verb.capitalize()}ing YBM Control...", APP_NAME)
        try:
            result = _run_ybm(*args)
        except subprocess.TimeoutExpired:
            icon.notify(f"{verb.capitalize()} timed out after 3 minutes.", APP_NAME)
            return
        if result.returncode == 0:
            icon.notify(f"YBM Control: {verb} completed.", APP_NAME)
        else:
            tail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or ["unknown error"]
            icon.notify(f"YBM Control: {verb} failed - {tail[0]}", APP_NAME)

    threading.Thread(target=worker, daemon=True).start()


def _open_admin_console(icon: "pystray.Icon" = None, item: object = None) -> None:
    webbrowser.open(f"http://127.0.0.1:{_admin_port()}/admin")


def _show_status(icon: "pystray.Icon", item: object) -> None:
    def worker() -> None:
        try:
            result = _run_ybm("status")
        except subprocess.TimeoutExpired:
            icon.notify("Status check timed out.", APP_NAME)
            return
        lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
        summary = "\n".join(lines[-6:]) or "No status output."
        icon.notify(summary, APP_NAME)

    threading.Thread(target=worker, daemon=True).start()


def _quit(icon: "pystray.Icon", item: object) -> None:
    icon.stop()


def build_menu() -> "pystray.Menu":
    return pystray.Menu(
        pystray.MenuItem("Open Admin Console", _open_admin_console, default=True),
        pystray.MenuItem("Status", _show_status),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Start", lambda icon, item: _run_ybm_async(icon, "start", "start")),
        pystray.MenuItem("Stop", lambda icon, item: _run_ybm_async(icon, "stop", "stop")),
        pystray.MenuItem("Restart", lambda icon, item: _run_ybm_async(icon, "restart", "restart")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit tray icon", _quit),
    )


def main() -> None:
    if not YBM_PS1.exists():
        print(f"Could not find {YBM_PS1} - is this script still under a YBM checkout's scripts/ dir?", file=sys.stderr)
        raise SystemExit(1)
    icon = pystray.Icon(APP_NAME, _make_icon_image(), APP_NAME, menu=build_menu())
    icon.run()


if __name__ == "__main__":
    main()
