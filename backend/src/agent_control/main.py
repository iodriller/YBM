from fastapi import FastAPI

from agent_control.config import load_settings

app = FastAPI(title="Agent Control Backend")


@app.get("/health")
def health() -> dict[str, str]:
    settings = load_settings()
    return {"status": "ok", "instance": settings.identity.instance_name}

