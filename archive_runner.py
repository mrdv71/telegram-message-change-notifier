"""Ordinary private-message archive runner; reuses the existing local session.
No disappearing-media capture, new login, or MTProto security-check changes.
"""
import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient, functions
from message_watch import MessageWatch

STARTUP_TIMEOUT = 60
WORKER_SHUTDOWN_TIMEOUT = 5
DISCONNECT_TIMEOUT = 10


def report(*parts):
    print(datetime.now().astimezone().isoformat(timespec='seconds'), *parts, flush=True)


class DeadlineWatchdog:
    """Last resort if startup, cancellation or shutdown itself becomes stuck."""
    def __init__(self, timeout=90, exit_fn=os._exit, interval=1):
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.exit_fn, self.interval = exit_fn, interval
        self.arm(timeout, 'startup')
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def arm(self, seconds, phase):
        with self.lock:
            self.deadline = time.monotonic() + seconds
            self.phase = phase

    def _run(self):
        while not self.stopped.wait(self.interval):
            with self.lock:
                expired, phase = time.monotonic() >= self.deadline, self.phase
            if expired:
                # Fatal recovery must not depend on a working/nonblocking log sink.
                self.exit_fn(1)
                return

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=2)


def status_file(state, phase, **fields):
    data = dict(phase=phase, pid=os.getpid(), checked_at=datetime.now(timezone.utc).isoformat(), **fields)
    temporary = state / 'archive-health.json.tmp'
    temporary.write_text(json.dumps(data))
    temporary.chmod(0o600)
    temporary.replace(state / 'archive-health.json')


async def authorize(client, owner_id):
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError('Existing session needs local authorization')
    me = await client.get_me()
    if not me or me.bot or me.id != owner_id:
        raise RuntimeError('Signed-in account does not match cached owner')


async def health_loop(client, state, watchdog, interval=60, timeout=30):
    while True:
        await asyncio.wait_for(client(functions.updates.GetStateRequest()), timeout)
        watchdog.arm(interval + timeout + 30, 'runtime health')
        status_file(state, 'connected')
        report('Archive connection health: OK')
        await asyncio.sleep(interval)


def create_watch(state, owner_id):
    config = json.loads((state / 'config.json').read_text())
    return MessageWatch(state, owner_id, config['bot_token'])


async def run(state, watchdog):
    config = json.loads((state / 'config.json').read_text())
    client = TelegramClient(
        str(state / 'account'), config['api_id'], config['api_hash'],
        device_model='Private Message Archive', request_retries=2,
        connection_retries=3, retry_delay=2, timeout=10,
        flood_sleep_threshold=30, catch_up=True, sequential_updates=True)
    watch, tasks = None, []
    ready = asyncio.Event()
    try:
        # Use the identity explicitly saved by setup, never a replacement session identity.
        # Register gated handlers BEFORE connect/catch-up to avoid losing updates.
        owner_id = config['owner_id']
        if not isinstance(owner_id, int) or owner_id <= 0:
            raise RuntimeError('Missing cached owner identity')
        watch = create_watch(state, owner_id)
        tasks.append(watch.install(client, ready=ready))
        status_file(state, 'connecting')
        report('Archive startup: ordinary private messages only; existing session retained.')
        await asyncio.wait_for(authorize(client, owner_id), STARTUP_TIMEOUT)
        ready.set()
        watchdog.arm(120, 'runtime health')
        report('Watching incoming private chats. Press Ctrl+C to stop.')
        # This queues catch-up; it is deliberately NOT logged as completion.
        await client.catch_up()
        report('Archive missed-update catch-up requested; unavailable originals cannot be recovered.')
        tasks.extend([asyncio.create_task(health_loop(client, state, watchdog)),
                      asyncio.ensure_future(client.disconnected)])
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        raise ConnectionError('Archive worker or connection stopped')
    finally:
        watchdog.arm(20, 'shutdown')
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), WORKER_SHUTDOWN_TIMEOUT)
        try:
            await asyncio.wait_for(client.disconnect(), DISCONNECT_TIMEOUT)
        finally:
            if watch:
                watch.close(client)
            else:
                client.session.close()
