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
from agent_control.config import load_settings
from agent_control.storage import AuditLogger, Database, Repositories


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
    service = TelegramIntakeService(adapter, repositories, audit, settings=settings)
    client = TelegramBotApi(load_telegram_token(settings.channels.telegram))
    runner = TelegramPollingRunner(client, service)
    offset: int | None = None
    while True:
        offset, _ = await runner.poll_once(offset=offset, timeout=30)


def main() -> None:
    parser = argparse.ArgumentParser("agent-control")
    parser.add_argument("command", choices=["init-db", "config-summary", "poll-telegram"])
    args = parser.parse_args()

    if args.command == "init-db":
        init_db()
    elif args.command == "config-summary":
        config_summary()
    elif args.command == "poll-telegram":
        asyncio.run(poll_telegram())


if __name__ == "__main__":
    main()
