from fastapi.testclient import TestClient

from agent_control import main
from agent_control.config import AppSettings


def test_http_responses_have_browser_security_headers(monkeypatch) -> None:
    monkeypatch.setattr(main, "load_settings", lambda: AppSettings(_env_file=None))
    response = TestClient(main.app).get("/health")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "microphone=(self)" in response.headers["permissions-policy"]
