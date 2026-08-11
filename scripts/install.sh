#!/usr/bin/env bash
# One-command bootstrap for YBM on Linux/macOS:
#   curl -fsSL https://raw.githubusercontent.com/iodriller/YBM/main/scripts/install.sh | bash
#
# Git and Python do not need to be preinstalled. This script requires Bash and
# curl; its no-git source fallback also uses tar. Node.js 22.22+ is required to
# build the admin console; the optional WhatsApp bridge also needs Node.js.
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
# Both now do the same small job: get the code onto the machine, then hand off
# to the platform's launcher (./ybm.sh here, scripts\ybm.ps1 run there), which
# owns uv, the virtualenv, setup, and starting the stack.
set -euo pipefail

# The pinned uv version lives in ./ybm.sh now, which is what installs it.
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

# --- 1. The code: git if present, tarball if not -------------------------
# uv is deliberately NOT bootstrapped here. ./ybm.sh installs it, and that is
# what makes ybm.sh work on its own from an extracted release. Keeping one
# implementation per platform is the whole point: install.ps1 hands uv to
# ybm.ps1 the same way. Nothing above this point needs uv - fetching the source
# uses git or curl, and `uv sync` provides Python itself, so the old separate
# `uv python install` step bought nothing.
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
  - copy an existing checkout onto this machine and run bash ./scripts/install.sh inside it."
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

# --- 2. Hand off to ybm.sh -----------------------------------------------
if [ "$DRY_RUN" = "1" ]; then
  plan "would run: $REPO_DIR/ybm.sh"
  [ "$VERIFY" = "1" ] && plan "would then run: ybm doctor (--verify)"
  echo ""
  echo "Dry run complete - nothing was installed or changed."
  exit 0
fi

cd "$REPO_DIR"
log "Installing dependencies and starting YBM"
# Runtime extras only, because ybm.sh is the consumer path. A contributor who
# wants pytest/ruff/telethon runs the `uv sync --extra test --extra dev` line
# AGENTS.md documents, exactly as on Windows.
bash "$REPO_DIR/ybm.sh" \
  || fail "startup failed" "Run '$REPO_DIR/backend/.venv/bin/ybm doctor' to diagnose. Logs: $REPO_DIR/.agent_control/logs"

YBM_BIN="$REPO_DIR/backend/.venv/bin/ybm"
if [ "$VERIFY" = "1" ]; then
  log "Verifying the install"
  "$YBM_BIN" doctor \
    || fail "post-install verification failed" \
            "The stack installed but doctor reported problems - see the [FAIL] lines above."
  info "verified"
fi

echo ""
log "Pick a model and (optionally) Telegram in the admin console that just opened."
