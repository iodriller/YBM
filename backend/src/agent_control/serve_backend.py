"""Entry point for the FastAPI backend that owns its own logging setup.

`run_backend.ps1` used to invoke `python -m uvicorn agent_control.main:app`
directly. uvicorn's CLI configures its own logging (`Config.configure_logging()`)
*after* importing the app module, which would silently clobber
`logging_setup.configure_logging()` if that ran at `main.py` import time - two
competing root-logger setups, last one wins, and uvicorn always runs last.
Running through `uvicorn.run(..., log_config=None)` here instead means ours
is the only one that touches the root logger.
"""

from __future__ import annotations

import uvicorn

from agent_control.config import load_settings
from agent_control.logging_setup import configure_logging


def main() -> None:
    settings = load_settings()
    configure_logging(settings, "backend")
    host = settings.server.host
    port = settings.server.port
    uvicorn.run("agent_control.main:app", host=host, port=port, log_config=None)


if __name__ == "__main__":
    main()
