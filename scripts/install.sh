#!/usr/bin/env bash
# One-command bootstrap for YBM Control on Linux/macOS:
#   curl -fsSL https://raw.githubusercontent.com/iodriller/YBM/main/scripts/install.sh | sh
#
# Requires nothing preinstalled. Not git, not Python.
#
#   - uv is a standalone binary that needs no Python, and `uv python install`
#     provides the interpreter. An earlier version of this script hunted for
#     python3.12/python3/python and refused to continue without one, then never
#     used it: `uv sync` builds the venv against a uv-managed interpreter, and
#     that venv is what every later command runs.
#   - git is used when present, and a source tarball is downloaded when it is
#     not.
#
# Keep this in step with scripts/install.ps1 - the two have drifted before.
set -euo pipefail

# Pinned deliberately: an unpinned installer means two machines a week apart get
# different uv versions, and a bad uv release breaks every install at once.
UV_VERSION="0.9.7"
UV_INSTALLER="https://astral.sh/uv/${UV_VERSION}/install.sh"
PYTHON_VERSION="3.12"

REPO_URL="https://github.com/iodriller/YBM.git"
TARBALL_URL="https://codeload.github.com/iodriller/YBM/tar.gz/refs/heads/main"
INSTALL_DIR="${YBM_INSTALL_DIR:-$HOME/ybm}"

DRY_RUN="${YBM_DRY_RUN:-0}"
VERIFY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --verify) VERIFY=1 ;;
    --no-prompt) : ;;  # accepted for parity; nothing here blocks on input
    --install-dir) shift; INSTALL_DIR="$1" ;;
    -h|--help)
      echo "usage: install.sh [--dry-run] [--verify] [--no-prompt] [--install-dir DIR]"
      exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

log()  { printf '==> %s\n' "$1"; }
info() { printf '    %s\n' "$1"; }
plan() { printf '[dry-run] %s\n' "$1"; }
fail() {
  printf '\nERROR: %s\n' "$1" >&2
  [ $# -gt 1 ] && printf '  %s\n' "$2" >&2
  exit 1
}

# --- 1. uv ---------------------------------------------------------------
# Resolved to an absolute path rather than trusting PATH: a freshly written
# PATH entry is not visible to the already-running shell, which is why the old
# script could dead-end with "open a new shell and re-run" mid-install.
find_uv() {
  for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    [ -x "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  done
  command -v uv 2>/dev/null || return 1
}

UV="$(find_uv || true)"
if [ -n "$UV" ]; then
  log "uv already installed"
  info "$UV"
elif [ "$DRY_RUN" = "1" ]; then
  log "uv not found"
  plan "would install uv ${UV_VERSION} from ${UV_INSTALLER}"
  UV="uv"
else
  log "Installing uv ${UV_VERSION} (standalone; no Python needed)"
  export UV_NO_MODIFY_PATH=1
  curl -LsSf "$UV_INSTALLER" | sh \
    || fail "could not install uv from $UV_INSTALLER" \
            "Check your internet connection, then re-run. uv is the only thing YBM needs to bootstrap."
  UV="$(find_uv || true)"
  [ -n "$UV" ] || fail "uv installed but could not be located" \
                       "Looked in ~/.local/bin and ~/.cargo/bin."
  info "uv at $UV"
fi

# --- 2. Python, provided by uv -------------------------------------------
if [ "$DRY_RUN" = "1" ]; then
  plan "would run: uv python install ${PYTHON_VERSION}"
else
  log "Ensuring Python ${PYTHON_VERSION} (downloaded by uv, not from your system)"
  "$UV" python install "$PYTHON_VERSION" \
    || fail "uv could not provide Python ${PYTHON_VERSION}" \
            "Re-run, or install Python ${PYTHON_VERSION} yourself and re-run - YBM will use it."
fi

# --- 3. The code: git if present, tarball if not -------------------------
if [ -f "backend/pyproject.toml" ] && [ -f "AGENTS.md" ] && [ -f "scripts/ybm.ps1" ]; then
  REPO_DIR="$(pwd)"
  log "Already inside a YBM checkout"
  info "$REPO_DIR"
elif [ "$DRY_RUN" = "1" ]; then
  REPO_DIR="$INSTALL_DIR"
  if command -v git >/dev/null 2>&1; then
    plan "would clone $REPO_URL into $INSTALL_DIR (git found)"
  else
    plan "would download $TARBALL_URL into $INSTALL_DIR (no git; tarball fallback)"
  fi
elif command -v git >/dev/null 2>&1; then
  if [ -d "$INSTALL_DIR/.git" ]; then
    log "Updating existing checkout at $INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only || info "pull failed (local changes?) - continuing as-is"
  else
    log "Cloning into $INSTALL_DIR"
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR" \
      || fail "git clone failed" "Delete $INSTALL_DIR and re-run."
  fi
  REPO_DIR="$INSTALL_DIR"
elif [ -f "$INSTALL_DIR/backend/pyproject.toml" ]; then
  log "git not found - using the existing install at $INSTALL_DIR"
  info "install git if you want in-place updates"
  REPO_DIR="$INSTALL_DIR"
else
  log "git not found - downloading the source tarball instead"
  tmp="$(mktemp -d)"
  # A private repository answers 404, not 401/403, to an anonymous request, so
  # "not found" here almost always means "not public". Say that, rather than
  # sending someone to check their connection.
  status="$(curl -sL -o "$tmp/src.tar.gz" -w '%{http_code}' "$TARBALL_URL" || echo 000)"
  if [ "$status" = "404" ]; then
    rm -rf "$tmp"
    fail "the source archive is not publicly downloadable (HTTP 404)" \
"The repository is private, so anonymous download cannot work. Either:
  - make the repository public, or
  - install git and authenticate (gh auth login, or a credential helper), then re-run, or
  - copy an existing checkout onto this machine and run ./scripts/ybm.ps1 run inside it."
  fi
  [ "$status" = "200" ] || { rm -rf "$tmp"; fail "download failed (HTTP $status)" "Check your internet connection and re-run."; }
  tar -xzf "$tmp/src.tar.gz" -C "$tmp"
  extracted="$(find "$tmp" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  [ -n "$extracted" ] || { rm -rf "$tmp"; fail "the downloaded archive was empty" "Re-run, or install git and re-run."; }
  mkdir -p "$(dirname "$INSTALL_DIR")"
  mv "$extracted" "$INSTALL_DIR"
  rm -rf "$tmp"
  REPO_DIR="$INSTALL_DIR"
  info "downloaded to $INSTALL_DIR"
fi

# --- 4. Dependencies and start -------------------------------------------
if [ "$DRY_RUN" = "1" ]; then
  plan "would run: uv sync (runtime extras) in $REPO_DIR/backend"
  plan "would run: ybm setup && ybm start --open"
  [ "$VERIFY" = "1" ] && plan "would then run: ybm doctor (--verify)"
  echo ""
  echo "Dry run complete - nothing was installed or changed."
  exit 0
fi

cd "$REPO_DIR/backend"
log "Installing dependencies (uv sync)"
# Keep this extras list identical to scripts/ybm.ps1's Invoke-YbmSetup - these
# drifted before (this line used to say just "--extra dev", which skips
# pytest/telethon/voice/desktop entirely, unlike the Windows path).
"$UV" sync --extra test --extra e2e --extra voice --extra desktop --extra tray --extra dev

cd "$REPO_DIR"
YBM_BIN="$REPO_DIR/backend/.venv/bin/ybm"
log "Setting up config, tokens, and the admin console"
"$YBM_BIN" setup

log "Starting YBM Control"
"$YBM_BIN" start --open \
  || fail "startup failed" "Run '$YBM_BIN doctor' to diagnose. Logs: $REPO_DIR/.agent_control/logs"

if [ "$VERIFY" = "1" ]; then
  log "Verifying the install"
  "$YBM_BIN" doctor \
    || fail "post-install verification failed" \
            "The stack installed but doctor reported problems - see the [FAIL] lines above."
  info "verified"
fi

echo ""
log "Pick a model and (optionally) Telegram in the admin console that just opened."
