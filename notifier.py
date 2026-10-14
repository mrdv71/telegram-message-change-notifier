"""Standalone setup and runner; never imports an existing account session."""
import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path
from telethon import TelegramClient
from message_watch import MessageWatch

STATE = Path(__file__).resolve().parent / "state"

def state_dir():
    os.umask(0o077)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE.chmod(0o700)

async def setup():
    state_dir()
    config_file = STATE / "config.json"
    if config_file.exists():
        raise RuntimeError("Already configured. Keep the existing state or move it aside before fresh setup.")
    api_id = int(input("Telegram API ID (my.telegram.org): ").strip())
    api_hash = getpass.getpass("Telegram API hash (hidden): ").strip()
    token = getpass.getpass("BotFather bot token (hidden): ").strip()
    if not api_hash or ":" not in token:
        raise ValueError("An API hash and bot token are required")
    client = TelegramClient(str(STATE / "account"), api_id, api_hash)
    try:
        await client.start(phone=lambda: input("Your phone number: "),
                           code_callback=lambda: getpass.getpass("Telegram login code (hidden): "),
                           password=lambda: getpass.getpass("Telegram 2FA password (hidden): "))
        me = await client.get_me()
        if me.bot:
            raise RuntimeError("Sign in with a personal Telegram account, not a bot")
        config = dict(api_id=api_id, api_hash=api_hash, bot_token=token, owner_id=me.id)
        with config_file.open("x") as stream:
            json.dump(config, stream)
        config_file.chmod(0o600)
        print("Setup saved locally. Start your notification bot in Telegram, then run: python notifier.py run")
    finally:
        await client.disconnect()

async def run():
    state_dir()
    config = json.loads((STATE / "config.json").read_text())
    client = TelegramClient(str(STATE / "account"), config["api_id"], config["api_hash"])
    watch = None
    worker = None
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Session unauthorized; perform fresh setup")
        me = await client.get_me()
        if me.id != config["owner_id"] or me.bot:
            raise RuntimeError("Configured owner does not match the signed-in account")
        watch = MessageWatch(STATE, me.id, config["bot_token"])
        worker = watch.install(client)
        print("Watching incoming private chats. Press Ctrl+C to stop.", flush=True)
        done, _ = await asyncio.wait([worker, client.disconnected], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        await client.disconnect()
        if watch:
            watch.close(client)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Telegram private-chat edit/delete alerts")
    parser.add_argument("command", choices=["setup", "run"])
    parser.add_argument("--state-dir", type=Path, default=STATE,
                        help="Private persistent data directory (default: state/ beside notifier.py)")
    args = parser.parse_args()
    STATE = args.state_dir.expanduser().resolve()
    try:
        asyncio.run(setup() if args.command == "setup" else run())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # Exception bodies may include credentials, URLs, or message contents.
        print(f"Stopped ({type(exc).__name__}). Check setup, network, and bot access; private details suppressed.")
        raise SystemExit(1)
