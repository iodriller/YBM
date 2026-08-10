# YBM Control - headless profile.
#
# What runs here: Telegram/WhatsApp intake, the operator loop, filesystem tools
# scoped to mounted volumes, the code interpreter, MCP, and the admin console.
#
# What does NOT run here, by nature rather than by omission: desktop control,
# screenshots, the VS Code bridge, and browser automation that needs a real
# display. There is no session to attach to inside a container. YBM_HEADLESS=1
# makes `ybm doctor` report those as unavailable instead of failing at call
# time - see bootstrap.is_headless_runtime.
#
# Built on uv's image so the container and the host installer agree on how
# Python is provided: scripts/install.ps1 also lets uv supply the interpreter
# rather than requiring one.

# --- builder: resolve and install dependencies -----------------------------
FROM ghcr.io/astral-sh/uv:0.9.7-python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app/backend

# Dependency layer first: it changes far less often than the source, so an edit
# to agent_control does not re-resolve the whole lockfile.
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project \
        --extra voice --extra tray

COPY backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra voice --extra tray

# --- runtime ---------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# curl is used by the HEALTHCHECK below; git lets the coding-agent tools work
# against a mounted repository. Both are small and deliberate - nothing else is
# installed, because every extra package is attack surface for a process that
# runs model-chosen tool calls.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git tini \
    && rm -rf /var/lib/apt/lists/*

# Non-root. The code interpreter's own Docker backend already runs sandboxed
# work as a non-root user; the agent process itself should not be root either.
RUN useradd --create-home --uid 10001 ybm

WORKDIR /app
COPY --from=builder --chown=ybm:ybm /app/backend/.venv /app/backend/.venv
COPY --chown=ybm:ybm backend/ /app/backend/
COPY --chown=ybm:ybm config/config.example.yaml /app/config/config.example.yaml
COPY --chown=ybm:ybm scripts/ /app/scripts/
COPY --chown=ybm:ybm docs/ /app/docs/
COPY --chown=ybm:ybm AGENTS.md README.md /app/

# AGENT_ is the settings env prefix and __ the nesting delimiter (config.py's
# SettingsConfigDict), so AGENT_SERVER__HOST maps to server.host. YBM_HEADLESS
# is read directly by bootstrap.is_headless_runtime, not through settings.
#
# 0.0.0.0 binds every interface *inside the container only*; compose publishes
# it to 127.0.0.1 on the host. Binding loopback here would make it unreachable.
# The admin API refuses to serve on a non-loopback host without a token, so set
# AGENT_ADMIN_TOKEN in .env - compose passes it through.
ENV PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    YBM_HEADLESS=1 \
    AGENT_SERVER__HOST=0.0.0.0 \
    AGENT_SERVER__PORT=8765

# Written at runtime, and the mount points compose attaches volumes to.
RUN mkdir -p /app/.agent_control /app/config /app/workspace \
    && chown -R ybm:ybm /app/.agent_control /app/config /app/workspace

USER ybm
EXPOSE 8765

# tini reaps the subprocesses YBM spawns (coding agents, MCP stdio servers),
# which would otherwise accumulate as zombies under PID 1.
ENTRYPOINT ["/usr/bin/tini", "--"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8765/health || exit 1

# --foreground because start_all spawns detached children and returns; a PID 1
# that exits stops the container. --no-localdeploy because the model server is
# a separate concern here (a compose service, or Ollama on the host).
CMD ["ybm", "start", "--foreground", "--no-localdeploy"]
