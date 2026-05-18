from __future__ import annotations

import argparse
import asyncio
import json

from agent_control.channels.telegram import (
    TelegramAdapter,
    TelegramBotApi,
    TelegramIntakeService,
    TelegramPollingRunner,
    load_telegram_token,
)
from agent_control.channels.memory import ConversationMemoryService
from agent_control.channels.responder import LLMTelegramResponder
from agent_control.channels.telegram_notifications import TelegramTaskNotifier
from agent_control.config import load_settings
from agent_control.llm import LLMMessageClassifier, build_default_llm_provider
from agent_control.llm.planner import PlannerService
from agent_control.observation import ArtifactService, ScreenshotService
from agent_control.orchestration import TaskWorker, ToolExecutor
from agent_control.orchestration.default_plans import build_default_vscode_development_plan
from agent_control.policy import PolicyEngine
from agent_control.recovery import RetryPolicy
from agent_control.storage import AuditLogger, Database, Repositories
from agent_control.tools.registry import build_tool_registry


def build_repositories() -> tuple[Repositories, AuditLogger]:
    settings = load_settings()
    database = Database(settings.storage.database_url)
    database.initialize()
    repositories = Repositories.for_database(database)
    return repositories, AuditLogger(repositories.audit, settings.logging.redact_patterns)


def init_db() -> None:
    settings = load_settings()
    database = Database(settings.storage.database_url)
    database.initialize()
    print(f"initialized {settings.storage.database_url}")


def config_summary() -> None:
    print(json.dumps(load_settings().safe_summary(), indent=2, default=str))


async def poll_telegram() -> None:
    settings = load_settings()
    repositories, audit = build_repositories()
    adapter = TelegramAdapter(settings.channels.telegram, audit)
    provider = build_default_llm_provider(settings)
    classifier = LLMMessageClassifier(provider) if provider else None
    responder = LLMTelegramResponder(provider, settings, repositories) if provider else None
    memory_service = ConversationMemoryService(repositories, provider=provider)
    service = TelegramIntakeService(
        adapter,
        repositories,
        audit,
        settings=settings,
        screenshot_service=_screenshot_service(settings, repositories),
        classifier=classifier,
        responder=responder,
        memory_service=memory_service,
    )
    client = TelegramBotApi(load_telegram_token(settings.channels.telegram))
    runner = TelegramPollingRunner(client, service)
    offset: int | None = None
    while True:
        offset, _ = await runner.poll_once(offset=offset, timeout=30)


async def run_worker() -> None:
    settings = load_settings()
    repositories, audit = build_repositories()
    provider = build_default_llm_provider(settings)
    planner = PlannerService(provider, repositories, audit) if provider else None
    policy = PolicyEngine(settings, audit)
    registry = build_tool_registry(settings, _backend_base_url(settings))
    executor = ToolExecutor(
        policy,
        repositories,
        audit,
        adapters=registry.adapters,
        tool_definitions=registry.definitions,
    )
    worker = TaskWorker(
        repositories,
        audit,
        planner=planner,
        executor=executor,
        retry_policy=RetryPolicy(settings.limits),
        config_context=_worker_config_context(registry),
        default_plan_factory=lambda task: build_default_vscode_development_plan(settings, task),
        notification_sink=_telegram_notifier(settings),
    )
    await worker.run_forever()


def _telegram_notifier(settings) -> TelegramTaskNotifier | None:
    if not settings.channels.telegram.enabled:
        return None
    try:
        return TelegramTaskNotifier(TelegramBotApi(load_telegram_token(settings.channels.telegram)))
    except RuntimeError:
        return None


def _screenshot_service(settings, repositories: Repositories) -> ScreenshotService | None:
    if not settings.adapters.desktop.screenshot_enabled:
        return None
    return ScreenshotService(
        settings.adapters.desktop,
        ArtifactService(settings.storage, repositories.artifacts),
    )


def _backend_base_url(settings) -> str:
    if settings.server.public_base_url:
        return settings.server.public_base_url
    host = "127.0.0.1" if settings.server.host in {"0.0.0.0", "::"} else settings.server.host
    return f"http://{host}:{settings.server.port}"


def _worker_config_context(registry) -> str:
    return f"""{registry.context()}

{registry.vault_summary()}

Prefer conservative plans. Use registered tool names exactly and include explicit operations when a tool supports them. If a needed adapter is missing, use adapter.factory to scaffold a reviewed proposal instead of inventing unregistered tool names."""


def main() -> None:
    parser = argparse.ArgumentParser("agent-control")
    parser.add_argument("command", choices=["init-db", "config-summary", "poll-telegram", "run-worker"])
    args = parser.parse_args()

    if args.command == "init-db":
        init_db()
    elif args.command == "config-summary":
        config_summary()
    elif args.command == "poll-telegram":
        asyncio.run(poll_telegram())
    elif args.command == "run-worker":
        asyncio.run(run_worker())


if __name__ == "__main__":
    main()
