"""
One-time script to create the Telethon user session file.
Run this once in your terminal, enter the OTP, and the session is saved.
After that, the live E2E tests run without any prompts.

Usage:
    python e2e/setup_telegram_session.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSION = str(ROOT / ".agent_control" / "telegram_e2e_user")

API_ID = int(os.getenv("TELEGRAM_API_ID") or "***REMOVED-TELEGRAM-API-ID***")
API_HASH = os.getenv("TELEGRAM_API_HASH") or "***REMOVED-TELEGRAM-API-HASH***"
SESSION = os.getenv("TELEGRAM_USER_SESSION") or DEFAULT_SESSION


async def main() -> None:
    try:
        from telethon import TelegramClient
    except ImportError:
        sys.exit("Install telethon first:  pip install telethon")

    Path(SESSION).parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"Already logged in as {me.first_name} (@{me.username})")
        print(f"Session file: {SESSION}.session")
        await client.disconnect()
        return

    phone = input("Enter your phone number (e.g. <phone-number>): ").strip()
    await client.send_code_request(phone)
    code = input("Enter the OTP Telegram sent you: ").strip()
    try:
        await client.sign_in(phone, code)
    except Exception as exc:
        password = input(f"2FA enabled. Enter your password: ").strip()
        await client.sign_in(password=password)

    me = await client.get_me()
    print(f"\nLogged in as {me.first_name} (@{me.username})")
    print(f"Session saved to: {SESSION}.session")
    print("\nYou can now run the E2E tests:")
    print("  python scripts/run_all_e2e_tests.py --only browser_dizibox_new_shows")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
