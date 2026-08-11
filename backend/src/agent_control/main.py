from fastapi import Depends, FastAPI, Header, Request, Response

from agent_control.admin import create_admin_router
from agent_control.config import load_settings
from agent_control.storage import Database, Repositories
from agent_control.tools.vscode_bridge import (
    VSCodeBridgeStore,
    VSCodeHeartbeat,
    VSCodeTerminalCommand,
    VSCodeTerminalOutput,
    VSCodeWorkspaceState,
    require_vscode_bridge_token,
)

app = FastAPI(title="YBM Control Backend")
vscode_store = VSCodeBridgeStore()


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    # The console is same-origin and ships no inline scripts. Inline styles
    # remain necessary for graph/layout measurements; everything else is
    # restricted to the local origin. Artifact responses set an even tighter
    # sandbox policy and are preserved by setdefault.
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
        "media-src 'self' blob:; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'",
    )
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=(self)")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


def get_repositories() -> Repositories:
    settings = load_settings()
    database = Database(settings.storage.database_url)
    database.initialize()
    return Repositories.for_database(database)


app.include_router(create_admin_router(load_settings, get_repositories, vscode_store))


@app.get("/health")
def health() -> dict[str, str]:
    settings = load_settings()
    return {"status": "ok", "instance": settings.identity.instance_name}


def _vscode_auth(token: str | None = Header(default=None, alias="X-Agent-Control-Token")) -> None:
    settings = load_settings()
    require_vscode_bridge_token(settings.adapters.vscode, settings.server.host, token)


@app.post("/vscode/heartbeat", dependencies=[Depends(_vscode_auth)])
def vscode_heartbeat(heartbeat: VSCodeHeartbeat) -> VSCodeHeartbeat:
    return vscode_store.update_heartbeat(heartbeat)


@app.post("/vscode/state", dependencies=[Depends(_vscode_auth)])
def vscode_state(state: VSCodeWorkspaceState) -> VSCodeWorkspaceState:
    return vscode_store.update_state(state)


@app.get("/vscode/state", dependencies=[Depends(_vscode_auth)])
def get_vscode_state() -> VSCodeWorkspaceState | None:
    return vscode_store.state


@app.post("/vscode/terminal-output", dependencies=[Depends(_vscode_auth)])
def vscode_terminal_output(output: VSCodeTerminalOutput) -> VSCodeTerminalOutput:
    return vscode_store.add_terminal_output(output)


@app.get("/vscode/terminal-output", dependencies=[Depends(_vscode_auth)])
def get_vscode_terminal_output(command_id: str | None = None) -> dict[str, list[dict]]:
    outputs = vscode_store.list_terminal_outputs(command_id)
    return {"outputs": [output.model_dump(mode="json") for output in outputs]}


@app.post("/vscode/terminal-commands", dependencies=[Depends(_vscode_auth)])
def enqueue_vscode_terminal_command(command: VSCodeTerminalCommand) -> VSCodeTerminalCommand:
    return vscode_store.enqueue_terminal_command(command)


@app.get("/vscode/terminal-commands", dependencies=[Depends(_vscode_auth)])
def take_vscode_terminal_commands(instance_id: str | None = None) -> list[VSCodeTerminalCommand]:
    return vscode_store.take_terminal_commands(instance_id)
