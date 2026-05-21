from __future__ import annotations

import pytest

from agent_control.config import BrowserAdapterConfig
from agent_control.orchestration.default_plans import build_default_task_plan
from agent_control.schemas import Capability, TaskRecord, ToolCallRequest
from agent_control.tools.browser import BrowserAdapter, BrowserTarget


class FakeClient:
    def __init__(self, config: BrowserAdapterConfig) -> None:
        self.config = config
        self.base_url = "http://127.0.0.1:9222"
        self.target = BrowserTarget("tab_1", "page", "Search", "https://search.test", "ws://example")
        self.navigated: list[str] = []

    def ensure_available(self) -> None:
        return None

    def open_url(self, url: str, *, new_tab: bool = True) -> BrowserTarget:
        self.target = BrowserTarget("tab_1", "page", "Opened", url, "ws://example")
        return self.target

    def navigate(self, target: BrowserTarget, url: str) -> None:
        self.navigated.append(url)
        self.target = BrowserTarget(target.id, "page", "Navigated", url, target.web_socket_debugger_url)

    def wait(self, seconds: float) -> None:
        return None

    def page_targets(self) -> list[BrowserTarget]:
        return [self.target]

    def page_summary(self, target: BrowserTarget, *, max_chars: int) -> dict:
        if "search" in target.url:
            return {
                "url": target.url,
                "title": "Search",
                "text": "Search results",
                "links": [
                    {"href": "https://one.test/page", "text": "One"},
                    {"href": "https://two.test/page", "text": "Two"},
                ],
                "forms": [],
            }
        return {
            "url": target.url,
            "title": "Episode page",
            "text": "Latest episode 7 released 2026-05-20. Contact form available.",
            "links": [],
            "forms": [{"inputs": [{"name": "email", "type": "email"}]}],
        }

    def fill_form(self, target: BrowserTarget, fields: dict[str, str], *, submit: bool, submit_selector: str | None) -> dict:
        return {"filled": sorted(fields), "submitted": submit}


@pytest.mark.asyncio
async def test_browser_research_pages_visits_limited_results(monkeypatch) -> None:
    monkeypatch.setattr("agent_control.tools.browser.ChromeDevToolsClient", FakeClient)
    adapter = BrowserAdapter(BrowserAdapterConfig(enabled=True, default_wait_seconds=0))

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_browser",
            tool_name="browser.open",
            capability=Capability.BROWSER_OPEN,
            input={"operation": "research_pages", "query": "ducks", "page_limit": 2},
        )
    )

    assert result.status.value == "succeeded"
    assert result.output["visited_urls"] == ["https://one.test/page", "https://two.test/page"]
    assert len(result.output["page_summaries"]) == 2


@pytest.mark.asyncio
async def test_browser_check_page_update_extracts_markers(monkeypatch) -> None:
    monkeypatch.setattr("agent_control.tools.browser.ChromeDevToolsClient", FakeClient)
    adapter = BrowserAdapter(BrowserAdapterConfig(enabled=True, default_wait_seconds=0))

    result = await adapter.execute(
        ToolCallRequest(
            task_id="task_browser",
            tool_name="browser.control",
            capability=Capability.BROWSER_CONTROL,
            input={"operation": "check_page_update", "url": "https://show.test"},
        )
    )

    assert result.status.value == "succeeded"
    markers = result.output["browser_state"]["update_check"]["markers"]
    assert any("episode 7" in marker.lower() for marker in markers)


@pytest.mark.asyncio
async def test_browser_extract_state_and_fill_form_step(monkeypatch) -> None:
    monkeypatch.setattr("agent_control.tools.browser.ChromeDevToolsClient", FakeClient)
    adapter = BrowserAdapter(BrowserAdapterConfig(enabled=True, default_wait_seconds=0))

    state = await adapter.execute(
        ToolCallRequest(
            task_id="task_browser",
            tool_name="browser.control",
            capability=Capability.BROWSER_CONTROL,
            input={"operation": "extract_page_state", "url": "https://form.test"},
        )
    )
    filled = await adapter.execute(
        ToolCallRequest(
            task_id="task_browser",
            tool_name="browser.control",
            capability=Capability.BROWSER_CONTROL,
            input={"operation": "fill_form_step", "fields": {"email": "a@example.com"}},
        )
    )

    assert state.output["forms"][0]["inputs"][0]["name"] == "email"
    assert filled.output["browser_state"]["filled_fields"]["filled"] == ["email"]
    assert filled.output["browser_state"]["filled_fields"]["submitted"] is False


def test_default_browser_form_plan_extracts_then_fills() -> None:
    from agent_control.config import AppSettings, CapabilityPolicy
    from agent_control.schemas import RiskLevel

    settings = AppSettings(
        _env_file=None,
        adapters={"browser": {"enabled": True}},
        capabilities={
            Capability.BROWSER_OPEN: CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.LOW),
            Capability.BROWSER_CONTROL: CapabilityPolicy(enabled=True, requires_approval=False, max_risk_level=RiskLevel.CRITICAL),
        },
    )

    plan = build_default_task_plan(
        settings,
        TaskRecord(objective="Open https://form.test and start filling the form email=a@example.com"),
    )

    assert plan is not None
    assert [step.tool_input["operation"] for step in plan.steps] == ["open", "extract_page_state", "fill_form_step"]
    assert plan.steps[2].tool_input["fields"] == {"email": "a@example.com"}
