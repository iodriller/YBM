from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import sys

from agent_control.bootstrap import run_doctor, run_setup
from agent_control.config_sync import set_config_path
from agent_control.onboarding import run_onboard
from agent_control.db_tools import db_clean, db_inspect, db_reset
from agent_control.logging_setup import configure_logging
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
from agent_control.config import AppSettings, backend_base_url, load_settings
from agent_control.llm import LLMMessageClassifier, build_default_llm_provider
from agent_control.llm.providers import build_major_llm_provider
from agent_control.observation import ArtifactService, ScreenshotService
from agent_control.persona import persona_prompt_section
from agent_control.orchestration import AuditorService, OperatorLoopService, TaskWorker, ToolExecutor, reconcile_orphaned_tasks
from agent_control.policy import PolicyEngine
from agent_control.recovery import RetryPolicy
from agent_control.scheduler import run_scheduler_forever
from agent_control.schemas import AuditEventType, TaskStatus
from agent_control.storage import ApprovalRepository, AuditLogger, Database, Repositories
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


def _frontend_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "frontend"


def ui_build() -> int:
    """`npm run build` for the React console (docs/UI_REWRITE_PLAN.md §12.3) -
    output lands under agent_control/static/admin per vite.config.ts's
    build.outDir, where admin.py's SPA route serves it from."""
    import subprocess

    frontend_dir = _frontend_dir()
    if not frontend_dir.exists():
        print(f"no frontend/ checkout found at {frontend_dir}")
        return 1
    if not (frontend_dir / "node_modules").exists():
        print("frontend/node_modules is missing - run `npm install` in frontend/ first")
        return 1
    result = subprocess.run(["npm", "run", "build"], cwd=frontend_dir, shell=(os.name == "nt"))
    return result.returncode


def ui_dev() -> int:
    """`npm run dev` for the React console - the Vite dev server with hot
    reload, proxying /admin/api/* to this backend (vite.config.ts)."""
    import subprocess

    frontend_dir = _frontend_dir()
    if not frontend_dir.exists():
        print(f"no frontend/ checkout found at {frontend_dir}")
        return 1
    if not (frontend_dir / "node_modules").exists():
        print("frontend/node_modules is missing - run `npm install` in frontend/ first")
        return 1
    result = subprocess.run(["npm", "run", "dev"], cwd=frontend_dir, shell=(os.name == "nt"))
    return result.returncode


def init_db() -> None:
    settings = load_settings()
    database = Database(settings.storage.database_url)
    database.initialize()
    print(f"initialized {settings.storage.database_url}")


def config_summary() -> None:
    print(json.dumps(load_settings().safe_summary(), indent=2, default=str))


def trace_task(task_id: str, *, as_json: bool = False) -> int:
    """One-command task post-mortem, reading the DB directly - no running
    backend required, unlike the admin UI (docs/HISTORY.md §2.4: "to debug a
    failed task today you need the stack running, then the admin UI, then
    click into a trace"). Shares `build_task_trace()` with the
    `/admin/api/tasks/{id}/trace` endpoint so the two never drift apart.
    Returns a process exit code (0 found, 1 not found).
    """
    from agent_control.admin import build_task_trace

    repositories, _audit = build_repositories()
    trace = build_task_trace(repositories, task_id)
    if trace is None:
        print(f"no task found with id {task_id}")
        return 1
    if as_json:
        print(json.dumps(trace, indent=2, default=str))
        return 0

    task = trace["task"]
    print(f"task    {task['id']}")
    print(f"status  {task['status']}")
    print(f"goal    {task['objective']}")
    metadata = task.get("metadata") or {}
    if metadata.get("synthesized_answer"):
        print(f"answer  {metadata['synthesized_answer']}")
    if metadata.get("last_worker_error"):
        print(f"error   {metadata['last_worker_error']}")
    usage = metadata.get("token_usage")
    if usage:
        by_source = usage.get("by_source") or {}
        breakdown = ", ".join(f"{name}={entry.get('total_tokens', 0)}" for name, entry in by_source.items())
        print(
            f"tokens  {usage.get('total_tokens', 0)} total over {usage.get('calls', 0)} call(s)"
            f"{f' ({breakdown})' if breakdown else ''}"
        )

    history = trace.get("operator_history") or []
    print(f"\noperator steps ({len(history)}):")
    if not history:
        print("  (none recorded)")
    for index, step in enumerate(history, 1):
        tool_name = step.get("tool_name") or "?"
        status = step.get("status") or "?"
        print(f"  {index}. {tool_name}  [{status}]")
        if step.get("input"):
            print(f"     input:  {json.dumps(step['input'], default=str)}")
        if step.get("output_summary"):
            summary = str(step["output_summary"]).replace("\n", " ")
            print(f"     output: {summary[:300]}")
        if step.get("error"):
            print(f"     error:  {step['error']}")

    tool_invocations = trace.get("tool_invocations") or []
    print(f"\ntool invocations recorded in DB: {len(tool_invocations)}")
    print(f"audit events: {len(trace.get('audit') or [])}")

    evidence = trace.get("evidence") or {}
    files = evidence.get("files") or []
    urls = evidence.get("urls") or []
    commands = evidence.get("commands") or []
    if files or urls or commands:
        print("\nevidence (what this task touched):")
        for label, items in (("files", files), ("urls", urls), ("commands", commands)):
            for item in items:
                print(f"  [{label}] {item.get('value')}  ({item.get('tool_name')})")
    return 0


async def poll_telegram() -> None:
    settings = load_settings()
    repositories, audit = build_repositories()
    adapter = TelegramAdapter(settings.channels.telegram, audit)
    provider = build_default_llm_provider(settings)
    classifier = LLMMessageClassifier(provider) if provider else None
    responder = LLMTelegramResponder(provider, settings, repositories) if provider else None
    memory_service = ConversationMemoryService(repositories, provider=provider)
    client = TelegramBotApi(load_telegram_token(settings.channels.telegram), audit=audit)
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
    reconciled = reconcile_orphaned_tasks(repositories, audit)
    if reconciled:
        print(f"reconciled {reconciled} task(s) left running/interpreting by a previous worker (failed explicitly)")
    provider = build_default_llm_provider(settings)
    policy = PolicyEngine(settings, audit)
    registry = build_tool_registry(
        settings,
        backend_base_url(settings),
        provider=provider,
        should_continue=lambda task_id: _task_allows_tool_continue(repositories, task_id),
        artifact_repository=repositories.artifacts,
        task_repository=repositories.tasks,
        repositories=repositories,
        audit_logger=audit,
        telegram_client=_telegram_client(settings, audit),
    )
    major_provider = build_major_llm_provider(settings)
    operator = OperatorLoopService(provider, major_provider=major_provider) if provider else None
    auditor = AuditorService(provider) if provider else None
    executor = ToolExecutor(
        policy,
        repositories,
        audit,
        adapters=registry.adapters,
        tool_definitions=registry.definition_index,
    )
    notifier = _telegram_notifier(settings, audit, approvals=repositories.approvals)
    # Run max_parallel_tasks worker loops in one process. claim_next() claims
    # atomically per worker_id, so quick tasks (status, delivery) are not
    # starved behind a long-running coding or browser task.
    workers = [
        TaskWorker(
            repositories,
            audit,
            executor=executor,
            retry_policy=RetryPolicy(settings.limits),
            config_context=_worker_config_context(registry, settings),
            config_context_factory=lambda registry=registry, settings=settings: _worker_config_context(registry, settings),
            notification_sink=notifier,
            task_budget_seconds=float(settings.limits.task_budget_seconds),
            operator=operator,
            operator_max_steps=settings.operator.max_steps,
            auditor=auditor,
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
        max_consecutive_failures=settings.scheduler.max_consecutive_failures,
    )


async def run_coding_agent_session(session_root: str, session_id: str) -> None:
    await run_coding_agent_session_once(session_root, session_id)


async def run_coding_session_watcher(poll_interval_seconds: float = 5.0) -> None:
    settings = load_settings()
    repositories, audit = build_repositories()
    telegram = _telegram_client(settings, audit)
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


def _telegram_notifier(
    settings, audit: AuditLogger | None = None, approvals: ApprovalRepository | None = None
) -> TelegramTaskNotifier | None:
    if not settings.channels.telegram.enabled:
        return None
    try:
        client = _telegram_client(settings, audit)
        return TelegramTaskNotifier(client, approvals=approvals) if client else None
    except RuntimeError:
        return None


def _telegram_client(settings, audit: AuditLogger | None = None) -> TelegramBotApi | None:
    if not settings.channels.telegram.enabled:
        return None
    try:
        return TelegramBotApi(load_telegram_token(settings.channels.telegram), audit=audit)
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



def _worker_config_context(registry, settings: AppSettings) -> str:
    persona_section = persona_prompt_section(settings.adapters.persona)
    return f"""{registry.context()}

{registry.vault_summary()}

{persona_section}

Prefer conservative plans. Use registered tool names exactly and include explicit operations when a tool supports them. For exact JSON/REST APIs, prefer http.request when the target is allowlisted. If a needed connector is missing, refresh or install MCP when a matching server is known; otherwise use adapter.factory to scaffold and test a proposal instead of inventing unregistered tool names."""


# Long-running services get their own log file, named to match ybm.ps1's
# service names (`ybm logs <service>` / `ybm start` use the same strings) so
# a log file and a running process are trivially correlated. One-off CLI
# utility commands (doctor, db-*, config-*) share a catch-all "cli" log.
_COMMAND_SERVICE_NAMES = {
    "run-worker": "worker",
    "run-scheduler": "scheduler",
    "poll-telegram": "telegram_polling",
    "run-coding-session-watcher": "coding_session_watcher",
    "run-coding-agent-session": "coding_agent_session",
}


def _configure_logging_for_command(command: str) -> None:
    service_name = _COMMAND_SERVICE_NAMES.get(command, "cli")
    try:
        configure_logging(load_settings(), service_name)
    except Exception:
        # Config itself might be broken (that's what `doctor` exists to
        # diagnose) - fall back to basic stderr logging rather than let a
        # settings-loading failure take down the command before it even runs.
        logging.basicConfig(level=logging.INFO, stream=sys.stderr)
        logging.getLogger(__name__).warning(
            "structured logging setup failed; falling back to basic logging", exc_info=True
        )


def main() -> None:
    parser = argparse.ArgumentParser("ybm")
    parser.add_argument(
        "command",
        choices=[
            "doctor",
            "setup",
            "onboard",
            "start",
            "stop",
            "status",
            "logs",
            "ui-build",
            "ui-dev",
            "init-db",
            "config-summary",
            "config-set",
            "db-inspect",
            "db-clean",
            "db-reset",
            "trace-task",
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
    parser.add_argument("--telegram-token", default=None, help="used by `setup` to save TELEGRAM_BOT_TOKEN")
    parser.add_argument("path", nargs="?", default=None,
                         help="dotted config path (config-set) / task id (trace-task) / service name (logs)")
    parser.add_argument("value", nargs="?", default=None, help="new value, for `config-set`")
    parser.add_argument("--days", type=int, default=30, help="retention window for `db-clean`")
    parser.add_argument("--yes", action="store_true", help="required to confirm `db-reset`")
    parser.add_argument("--json", action="store_true", help="raw JSON output for `trace-task`")
    parser.add_argument("--follow", "-f", action="store_true", help="follow log output, for `logs`")
    parser.add_argument("--lines", type=int, default=60, help="tail line count, for `logs`")
    parser.add_argument("--no-telegram", action="store_true", help="for `start`: skip the Telegram polling service")
    parser.add_argument("--no-worker", action="store_true", help="for `start`: skip the worker + coding session watcher")
    parser.add_argument("--no-scheduler", action="store_true", help="for `start`: skip the scheduler")
    parser.add_argument("--no-localdeploy", action="store_true", help="for `start`: skip launching LocalDeploy")
    parser.add_argument("--open", action="store_true", help="for `start`: open the admin console in a browser once ready")
    args = parser.parse_args()

    _configure_logging_for_command(args.command)

    if args.command == "doctor":
        raise SystemExit(run_doctor())
    elif args.command == "setup":
        raise SystemExit(run_setup(telegram_token=args.telegram_token))
    elif args.command == "onboard":
        raise SystemExit(run_onboard())
    elif args.command == "start":
        from agent_control.supervisor import start_all
        raise SystemExit(start_all(
            no_telegram=args.no_telegram, no_worker=args.no_worker,
            no_scheduler=args.no_scheduler,
            no_localdeploy=args.no_localdeploy,
            open_browser=args.open,
        ))
    elif args.command == "stop":
        from agent_control.supervisor import stop_all
        raise SystemExit(stop_all())
    elif args.command == "status":
        from agent_control.supervisor import status_all
        raise SystemExit(status_all())
    elif args.command == "logs":
        from agent_control.supervisor import tail_log
        if not args.path:
            raise SystemExit("usage: ybm logs <service> [--follow] [--lines N]")
        raise SystemExit(tail_log(args.path, follow=args.follow, lines=args.lines))
    elif args.command == "ui-build":
        raise SystemExit(ui_build())
    elif args.command == "ui-dev":
        raise SystemExit(ui_dev())
    elif args.command == "init-db":
        init_db()
    elif args.command == "config-summary":
        config_summary()
    elif args.command == "config-set":
        if not args.path or args.value is None:
            raise SystemExit("usage: ybm config-set <dotted.path> <value>")
        ok, message = set_config_path(args.path, args.value)
        print(message)
        raise SystemExit(0 if ok else 1)
    elif args.command == "db-inspect":
        raise SystemExit(db_inspect())
    elif args.command == "db-clean":
        raise SystemExit(db_clean(args.days))
    elif args.command == "db-reset":
        raise SystemExit(db_reset(yes=args.yes))
    elif args.command == "trace-task":
        if not args.path:
            raise SystemExit("usage: ybm trace-task <task_id> [--json]")
        raise SystemExit(trace_task(args.path, as_json=args.json))
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
