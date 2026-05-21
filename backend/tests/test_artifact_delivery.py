from __future__ import annotations

from pathlib import Path

import pytest

from agent_control.config import AppSettings, CapabilityPolicy
from agent_control.orchestration import ToolExecutor
from agent_control.orchestration.default_plans import build_default_task_plan
from agent_control.policy import PolicyEngine
from agent_control.schemas import Artifact, ArtifactType, Capability, RiskLevel, TaskRecord, ToolCallRequest, ToolResultStatus
from agent_control.storage import AuditLogger, Database, Repositories
from agent_control.tools.artifact_delivery import ArtifactDeliveryAdapter
from agent_control.tools.registry import build_tool_registry


class FakeTelegramClient:
    def __init__(self) -> None:
        self.photos: list[tuple[str | int, str, str | None]] = []
        self.documents: list[tuple[str | int, str, str | None]] = []

    async def send_photo_file(self, chat_id: str | int, path: str, caption: str | None = None) -> dict:
        self.photos.append((chat_id, path, caption))
        return {"ok": True, "method": "sendPhoto"}

    async def send_document_file(self, chat_id: str | int, path: str, caption: str | None = None) -> dict:
        self.documents.append((chat_id, path, caption))
        return {"ok": True, "method": "sendDocument"}


def _repos(tmp_path) -> tuple[Repositories, AuditLogger]:
    database = Database(f"sqlite:///{tmp_path / 'agent.db'}")
    database.initialize()
    repos = Repositories.for_database(database)
    return repos, AuditLogger(repos.audit)


def test_registry_exposes_artifact_delivery_when_telegram_send_is_enabled(tmp_path) -> None:
    repos, _audit = _repos(tmp_path)
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.TELEGRAM_SEND: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.LOW,
            )
        },
    )

    registry = build_tool_registry(
        settings,
        "http://127.0.0.1:8765",
        artifact_repository=repos.artifacts,
        task_repository=repos.tasks,
        telegram_client=FakeTelegramClient(),
    )
    definitions = {definition.name: definition for definition in registry.definitions}

    assert definitions["artifact.deliver"].enabled is True
    assert definitions["artifact.deliver"].operations == ("send_file", "send_latest", "send_screenshot", "list_artifacts")
    assert "artifact.deliver" in registry.adapters


def test_default_artifact_delivery_plan_sends_latest_document() -> None:
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.TELEGRAM_SEND: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.LOW,
            )
        },
    )

    plan = build_default_task_plan(settings, TaskRecord(objective="Send me the PDF file you found"))

    assert plan is not None
    assert plan.required_capabilities == [Capability.TELEGRAM_SEND]
    assert plan.steps[0].tool_name == "artifact.deliver"
    assert plan.steps[0].tool_input["operation"] == "send_latest"
    assert plan.steps[0].tool_input["artifact_type"] == "document"


@pytest.mark.asyncio
async def test_artifact_delivery_sends_screenshot_from_task_metadata(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"png")
    task = repos.tasks.create(
        "take a screenshot and send it to me",
        metadata={"source_chat_id": "100", "screenshot_path": str(screenshot)},
    )
    client = FakeTelegramClient()
    adapter = ArtifactDeliveryAdapter(
        repos.artifacts,
        repos.tasks,
        telegram_client=client,
        allowed_roots=[str(tmp_path)],
    )

    result = await adapter.execute(
        ToolCallRequest(
            task_id=task.id,
            tool_name="artifact.deliver",
            capability=Capability.TELEGRAM_SEND,
            risk_level=RiskLevel.LOW,
            input={"operation": "send_screenshot", "caption": "desktop"},
        )
    )

    artifacts = repos.artifacts.list_for_task(task.id)
    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.output["delivered"] is True
    assert result.output["delivery_method"] == "telegram.sendPhoto"
    assert client.photos == [("100", str(screenshot.resolve()), "desktop")]
    assert artifacts[0].type == ArtifactType.SCREENSHOT
    assert artifacts[0].uri == str(screenshot.resolve())


@pytest.mark.asyncio
async def test_artifact_delivery_sends_latest_document_artifact(tmp_path) -> None:
    repos, _audit = _repos(tmp_path)
    document = tmp_path / "report.pdf"
    document.write_bytes(b"%PDF-1.4")
    task = repos.tasks.create("send me the PDF file", metadata={"source_chat_id": "100"})
    artifact = repos.artifacts.create(
        Artifact(
            task_id=task.id,
            type=ArtifactType.DOCUMENT,
            uri=str(document),
            content_preview="report.pdf",
        )
    )
    client = FakeTelegramClient()
    adapter = ArtifactDeliveryAdapter(
        repos.artifacts,
        repos.tasks,
        telegram_client=client,
        allowed_roots=[str(tmp_path)],
    )

    result = await adapter.execute(
        ToolCallRequest(
            task_id=task.id,
            tool_name="artifact.deliver",
            capability=Capability.TELEGRAM_SEND,
            risk_level=RiskLevel.LOW,
            input={"operation": "send_latest", "artifact_type": "document"},
        )
    )

    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.output["artifact_id"] == artifact.id
    assert result.output["delivery_method"] == "telegram.sendDocument"
    assert client.documents == [("100", str(document.resolve()), "report.pdf")]


@pytest.mark.asyncio
async def test_artifact_delivery_direct_path_rejects_root_escape(tmp_path) -> None:
    repos, _audit = _repos(tmp_path)
    task = repos.tasks.create("send a file", metadata={"source_chat_id": "100"})
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    adapter = ArtifactDeliveryAdapter(
        repos.artifacts,
        repos.tasks,
        telegram_client=FakeTelegramClient(),
        allowed_roots=[str(tmp_path / "allowed")],
    )

    result = await adapter.execute(
        ToolCallRequest(
            task_id=task.id,
            tool_name="artifact.deliver",
            capability=Capability.TELEGRAM_SEND,
            risk_level=RiskLevel.LOW,
            input={"operation": "send_file", "path": str(outside)},
        )
    )

    assert result.status == ToolResultStatus.FAILED
    assert "outside configured delivery roots" in (result.error_message or "")


@pytest.mark.asyncio
async def test_executor_records_artifact_delivery_output(tmp_path) -> None:
    repos, audit = _repos(tmp_path)
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(b"png")
    task = repos.tasks.create("send a screenshot", metadata={"source_chat_id": "100", "screenshot_path": str(screenshot)})
    settings = AppSettings(
        _env_file=None,
        capabilities={
            Capability.TELEGRAM_SEND: CapabilityPolicy(
                enabled=True,
                requires_approval=False,
                max_risk_level=RiskLevel.LOW,
            )
        },
    )
    registry = build_tool_registry(
        settings,
        "http://127.0.0.1:8765",
        artifact_repository=repos.artifacts,
        task_repository=repos.tasks,
        telegram_client=FakeTelegramClient(),
    )
    executor = ToolExecutor(
        PolicyEngine(settings, audit),
        repos,
        audit,
        adapters=registry.adapters,
        tool_definitions=registry.definitions,
    )

    result = await executor.execute(
        ToolCallRequest(
            task_id=task.id,
            tool_name="artifact.deliver",
            capability=Capability.TELEGRAM_SEND,
            input={"operation": "send_screenshot"},
        )
    )

    assert result.status == ToolResultStatus.SUCCEEDED
    assert result.output["delivered"] is True
