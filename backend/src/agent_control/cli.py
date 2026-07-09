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
from agent_control.llm.providers import build_major_llm_provider
from agent_control.llm.planner import PlannerService
from agent_control.llm.synthesizer import ResponseSynthesizer
from agent_control.llm.validator import AnswerValidator
from agent_control.observation import ArtifactService, ScreenshotService
from agent_control.orchestration import TaskWorker, ToolExecutor
from agent_control.orchestration.default_plans import build_default_task_plan, build_evaluator_recovery_plan
from agent_control.policy import PolicyEngine
from agent_control.recovery import RetryPolicy
from agent_control.scheduler import run_scheduler_forever
from agent_control.schemas import AuditEventType, TaskStatus
from agent_control.storage import AuditLogger, Database, Repositories
from agent_control.tools.registry import build_tool_registry
from agent_control.tools.stt import build_stt_adapter
from agent_control.tools.coding_agent import (
    mark_session_notified,
    run_coding_agent_session as run_coding_agent_session_once,
    scan_coding_sessions_once,
    session_completion_message,
    terminal_session_result,
)


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
    client = TelegramBotApi(load_telegram_token(settings.channels.telegram))
    service = TelegramIntakeService(
        adapter,
        repositories,
        audit,
        settings=settings,
        bot_api=client,
        stt=build_stt_adapter(settings.adapters.stt),
        screenshot_service=_screenshot_service(settings, repositories),
        classifier=classifier,
        responder=responder,
        memory_service=memory_service,
    )
    runner = TelegramPollingRunner(client, service)
    offset: int | None = None
    while True:
        try:
            offset, _ = await runner.poll_once(offset=offset, timeout=30)
        except Exception as exc:
            audit.append(
                AuditEventType.ERROR,
                actor="telegram_polling",
                payload={"error": "poll_once_failed", "reason": str(exc)},
            )
            await asyncio.sleep(5)


async def run_worker() -> None:
    settings = load_settings()
    repositories, audit = build_repositories()
    provider = build_default_llm_provider(settings)
    policy = PolicyEngine(settings, audit)
    registry = build_tool_registry(
        settings,
        _backend_base_url(settings),
        provider=provider,
        should_continue=lambda task_id: _task_allows_tool_continue(repositories, task_id),
        artifact_repository=repositories.artifacts,
        task_repository=repositories.tasks,
        repositories=repositories,
        audit_logger=audit,
        telegram_client=_telegram_client(settings),
    )
    major_provider = build_major_llm_provider(settings)
    planner = PlannerService(provider, repositories, audit, plan_validator=registry.validate_plan, major_provider=major_provider) if provider else None
    synthesizer = ResponseSynthesizer(provider) if provider else None
    validator = AnswerValidator(provider) if provider else None
    executor = ToolExecutor(
        policy,
        repositories,
        audit,
        adapters=registry.adapters,
        tool_definitions=registry.definition_index,
    )
    notifier = _telegram_notifier(settings)
    # Run max_parallel_tasks worker loops in one process. claim_next() claims
    # atomically per worker_id, so quick tasks (status, delivery) are not
    # starved behind a long-running coding or browser task.
    workers = [
        TaskWorker(
            repositories,
            audit,
            planner=planner,
            executor=executor,
            retry_policy=RetryPolicy(settings.limits),
            config_context=_worker_config_context(registry),
            config_context_factory=lambda registry=registry: _worker_config_context(registry),
            default_plan_factory=lambda task: build_default_task_plan(settings, task),
            recovery_plan_factory=lambda task, reason: build_evaluator_recovery_plan(settings, task, reason),
            notification_sink=notifier,
            synthesizer=synthesizer,
            validator=validator,
            task_budget_seconds=float(settings.limits.task_budget_seconds),
        )
        for _ in range(max(settings.limits.max_parallel_tasks, 1))
    ]
    await asyncio.gather(*(worker.run_forever() for worker in workers))


async def run_scheduler() -> None:
    settings = load_settings()
    repositories, audit = build_repositories()
    await run_scheduler_forever(
        repositories,
        audit,
        poll_interval_seconds=settings.scheduler.poll_interval_seconds,
    )


async def run_coding_agent_session(session_root: str, session_id: str) -> None:
    await run_coding_agent_session_once(session_root, session_id)


async def run_coding_session_watcher(poll_interval_seconds: float = 5.0) -> None:
    settings = load_settings()
    repositories, audit = build_repositories()
    telegram = _telegram_client(settings)
    while True:
        try:
            sessions = await scan_coding_sessions_once(settings.adapters.coding_agent.session_root)
            for session in sessions:
                error = await _handle_coding_session_completion(settings, repositories, session, telegram)
                mark_session_notified(
                    settings.adapters.coding_agent.session_root,
                    str(session.get("session_id")),
                    error=error,
                )
        except Exception as exc:
            audit.append(
                AuditEventType.ERROR,
                actor="coding_session_watcher",
                payload={"error": "watcher_scan_failed", "reason": str(exc)},
            )
        await asyncio.sleep(poll_interval_seconds)


async def _handle_coding_session_completion(settings, repositories: Repositories, session: dict, telegram) -> str | None:
    error: str | None = None
    task_id = session.get("task_id")
    task = repositories.tasks.get(str(task_id)) if task_id else None
    if task is not None:
        result = terminal_session_result(session)
        awaiting = task.metadata.get("awaiting_external") if isinstance(task.metadata, dict) else None
        metadata = {
            **task.metadata,
            "coding_agent_session": _coding_session_brief(session),
        }
        if task.status == TaskStatus.AWAITING_EXTERNAL and isinstance(awaiting, dict):
            metadata["pending_tool_result"] = {
                "step_id": awaiting.get("step_id"),
                "tool_name": "coding.agent",
                "result": result.model_dump(mode="json"),
            }
        status = TaskStatus.RUNNING if task.status == TaskStatus.AWAITING_EXTERNAL else task.status
        repositories.tasks.update_metadata(task.id, metadata, status)
    chat_id = task.metadata.get("source_chat_id") if task is not None else None
    if telegram is not None and chat_id:
        try:
            await telegram.send_message(str(chat_id), session_completion_message(session))
        except Exception as exc:
            error = str(exc)
    return error


def _coding_session_brief(session: dict) -> dict:
    return {
        key: session.get(key)
        for key in ("session_id", "provider", "status", "returncode", "changed_files", "summary", "ended_at")
    }


def _telegram_notifier(settings) -> TelegramTaskNotifier | None:
    if not settings.channels.telegram.enabled:
        return None
    try:
        client = _telegram_client(settings)
        return TelegramTaskNotifier(client) if client else None
    except RuntimeError:
        return None


def _telegram_client(settings) -> TelegramBotApi | None:
    if not settings.channels.telegram.enabled:
        return None
    try:
        return TelegramBotApi(load_telegram_token(settings.channels.telegram))
    except RuntimeError:
        return None


def _task_allows_tool_continue(repositories: Repositories, task_id: str) -> bool:
    task = repositories.tasks.get(task_id)
    if task is None:
        return False
    return task.status not in {TaskStatus.PAUSED, TaskStatus.CANCELLED}


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

Prefer conservative plans. Use registered tool names exactly and include explicit operations when a tool supports them. For exact JSON/REST APIs, prefer http.request when the target is allowlisted. If a needed connector is missing, refresh or install MCP when a matching server is known; otherwise use adapter.factory to scaffold and test a proposal instead of inventing unregistered tool names."""


def main() -> None:
    parser = argparse.ArgumentParser("agent-control")
    parser.add_argument(
        "command",
        choices=[
            "init-db",
            "config-summary",
            "poll-telegram",
            "run-worker",
            "run-scheduler",
            "run-coding-agent-session",
            "run-coding-session-watcher",
        ],
    )
    parser.add_argument("--session-root", default=None)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    args = parser.parse_args()

    if args.command == "init-db":
        init_db()
    elif args.command == "config-summary":
        config_summary()
    elif args.command == "poll-telegram":
        asyncio.run(poll_telegram())
    elif args.command == "run-worker":
        asyncio.run(run_worker())
    elif args.command == "run-scheduler":
        asyncio.run(run_scheduler())
    elif args.command == "run-coding-agent-session":
        if not args.session_root or not args.session_id:
            raise SystemExit("--session-root and --session-id are required")
        asyncio.run(run_coding_agent_session(args.session_root, args.session_id))
    elif args.command == "run-coding-session-watcher":
        asyncio.run(run_coding_session_watcher(args.poll_interval_seconds))


if __name__ == "__main__":
    main()
