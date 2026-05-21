from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from telethon import TelegramClient


async def main() -> None:
    args = _parse_args()
    api_id = args.api_id or os.getenv("TELEGRAM_API_ID")
    api_hash = args.api_hash or os.getenv("TELEGRAM_API_HASH")
    session = args.session or os.getenv("TELEGRAM_USER_SESSION") or ".agent_control/telegram_e2e_user"
    if not api_id or not api_hash:
        raise SystemExit("TELEGRAM_API_ID and TELEGRAM_API_HASH are required.")
    Path(session).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(session, int(api_id), api_hash)
    await client.start()
    me = await client.get_me()
    print(f"Authorized Telegram E2E user session as @{getattr(me, 'username', None) or me.id}.")
    await client.disconnect()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser("telegram-login")
    parser.add_argument("--api-id")
    parser.add_argument("--api-hash")
    parser.add_argument("--session")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main())
