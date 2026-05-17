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
from agent_control.channels.responder import LLMTelegramResponder
from agent_control.channels.telegram_notifications import TelegramTaskNotifier
from agent_control.config import load_settings
from agent_control.llm import LLMMessageClassifier, build_default_llm_provider
from agent_control.llm.planner import PlannerService
from agent_control.orchestration import TaskWorker, ToolExecutor
from agent_control.orchestration.default_plans import build_default_vscode_development_plan
from agent_control.policy import PolicyEngine
from agent_control.recovery import RetryPolicy
from agent_control.storage import AuditLogger, Database, Repositories
from agent_control.tools import GenericTerminalAgentAdapter, LocalWorkspaceWebAppAdapter, VSCodeBridgeTerminalAdapter


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
    service = TelegramIntakeService(
        adapter,
        repositories,
        audit,
        settings=settings,
        classifier=classifier,
        responder=responder,
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
    executor = ToolExecutor(
        policy,
        repositories,
        audit,
        adapters=_worker_adapters(settings),
    )
    worker = TaskWorker(
        repositories,
        audit,
        planner=planner,
        executor=executor,
        retry_policy=RetryPolicy(settings.limits),
        config_context=_worker_config_context(settings),
        default_plan_factory=lambda task: build_default_vscode_development_plan(settings, task),
        notification_sink=_telegram_notifier(settings),
    )
    await worker.run_forever()


def _worker_adapters(settings) -> dict[str, object]:
    adapters: dict[str, object] = {}
    if settings.adapters.workspace.enabled:
        adapters["workspace.web_app"] = LocalWorkspaceWebAppAdapter(settings.adapters.workspace)
    if settings.adapters.coding_assistant.enabled:
        adapters["coding_assistant"] = GenericTerminalAgentAdapter(settings.adapters.coding_assistant)
    if settings.adapters.vscode.enabled:
        vscode = VSCodeBridgeTerminalAdapter(settings.adapters.vscode, _backend_base_url(settings))
        adapters["vscode.terminal_command"] = vscode
        adapters["vscode.copilot_terminal"] = vscode
    return adapters


def _telegram_notifier(settings) -> TelegramTaskNotifier | None:
    if not settings.channels.telegram.enabled:
        return None
    try:
        return TelegramTaskNotifier(TelegramBotApi(load_telegram_token(settings.channels.telegram)))
    except RuntimeError:
        return None


def _backend_base_url(settings) -> str:
    if settings.server.public_base_url:
        return settings.server.public_base_url
    host = "127.0.0.1" if settings.server.host in {"0.0.0.0", "::"} else settings.server.host
    return f"http://{host}:{settings.server.port}"


def _worker_config_context(settings) -> str:
    return f"""Available worker tools:
- workspace.web_app: creates files under {settings.adapters.workspace.root_dir}, starts a localhost static preview, and returns the workspace path plus URL. Requires {settings.adapters.workspace.enabled=} and capability filesystem.write.
- vscode.copilot_terminal: queues a prompt to the VS Code bridge terminal and waits for a final terminal-output record. Requires {settings.adapters.vscode.enabled=} and capability vscode.write_files.
- vscode.terminal_command: queues an explicit terminal command to the VS Code bridge terminal. Requires {settings.adapters.vscode.enabled=} and capability vscode.write_files or terminal.run depending on the plan.
- coding_assistant: runs the configured local coding assistant command template. Requires {settings.adapters.coding_assistant.enabled=} and capability terminal.run.

Prefer conservative single-step plans. Use tool_input.prompt for prompts and tool_input.command for explicit commands."""


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
