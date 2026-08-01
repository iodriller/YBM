#!/usr/bin/env bash
# One-command bootstrap for YBM Control on Linux/macOS:
#   curl -fsSL https://raw.githubusercontent.com/iodriller/YBM/main/scripts/install.sh | sh
#
# Clones the repo (if not already inside it), installs Python dependencies
# via uv, runs the non-interactive `ybm setup` (writes config/config.yaml
# and .env, generates admin/vault tokens, builds the React admin console),
# then `ybm start --open` to launch the stack and open the admin console in
# a browser. The LLM/Telegram choice happens in that browser (the first-run
# wizard), not in this terminal. See docs/LOCAL_SETUP.md for what `setup`
# configures and CONTRIBUTING.md for the development (not just install)
# path. The interactive `ybm onboard` CLI wizard still exists for
# headless/SSH-only installs with no browser to open.
set -euo pipefail

REPO_URL="https://github.com/iodriller/YBM.git"
INSTALL_DIR="${YBM_INSTALL_DIR:-$HOME/ybm}"

log() { printf '==> %s\n' "$1"; }
fail() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }

command -v git >/dev/null 2>&1 || fail "git is required. Install it and re-run."

PY=""
for candidate in python3.12 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    version="$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo "0.0")"
    major="${version%%.*}"
    minor="${version##*.}"
    if [ "$major" -ge 3 ] 2>/dev/null && [ "$minor" -ge 12 ] 2>/dev/null; then
      PY="$candidate"
      break
    fi
  fi
done
[ -n "$PY" ] || fail "Python 3.12+ is required (checked python3.12, python3, python). Install it and re-run."
log "Using $($PY --version)"

if ! command -v uv >/dev/null 2>&1; then
  log "uv not found - installing it (https://astral.sh/uv)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null 2>&1 || fail "uv install did not put 'uv' on PATH - open a new shell and re-run."
fi

if [ -f "backend/pyproject.toml" ] && [ -f "AGENTS.md" ] && [ -f "scripts/ybm.ps1" ]; then
  log "Already inside a YBM checkout - using $(pwd)"
  REPO_DIR="$(pwd)"
else
  if [ -d "$INSTALL_DIR/.git" ]; then
    log "Found existing checkout at $INSTALL_DIR - pulling latest"
    git -C "$INSTALL_DIR" pull --ff-only
  else
    log "Cloning $REPO_URL into $INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi
  REPO_DIR="$INSTALL_DIR"
fi

cd "$REPO_DIR/backend"
log "Installing Python dependencies (uv sync)"
# Keep this extras list identical to scripts/ybm.ps1's Invoke-YbmSetup - these
# drifted before (this line used to say just "--extra dev", which skips
# pytest/telethon/voice/desktop entirely, unlike the Windows path).
uv sync --extra test --extra e2e --extra voice --extra desktop --extra dev

cd "$REPO_DIR"
log "Setting up config, tokens, and the admin console"
"$REPO_DIR/backend/.venv/bin/ybm" setup

log "Starting YBM Control"
"$REPO_DIR/backend/.venv/bin/ybm" start --open || fail "ybm start failed. Run 'ybm doctor' to diagnose."

echo ""
log "Pick a model and (optionally) Telegram in the admin console that just opened."
