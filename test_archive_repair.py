import asyncio
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import archive_runner
from pathlib import Path
from types import SimpleNamespace as S

from archive_runner import DeadlineWatchdog, authorize, health_loop
from message_watch import MessageWatch
from test_message_watch import Event


class ArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.w = MessageWatch(self.path, 99, 'unused')

    async def asyncTearDown(self):
        self.w.db.close()
        self.tmp.cleanup()

    async def test_ordered_original_edit_delete(self):
        worker = self.w.install(S(add_event_handler=lambda *args: None))
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        with self.w.db:
            self.w.db.execute('DELETE FROM outbox')
        entered, release = asyncio.Event(), asyncio.Event()
        original = Event(text='original', media=None)
        async def slow_sender():
            entered.set()
            await release.wait()
            return S(first_name='Test', bot=False)
        original.get_sender = slow_sender
        first = asyncio.create_task(self.w.handlers[0](original))
        await entered.wait()
        second = asyncio.create_task(self.w.handlers[1](Event(text='edited', media=None)))
        third = asyncio.create_task(self.w.handlers[2](S(chat_id=None, deleted_ids=[1])))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second, third)
        self.assertEqual(self.w.db.execute('SELECT text,deleted FROM messages').fetchone(), ('edited', 1))
        alerts = [r[0] for r in self.w.db.execute('SELECT text FROM outbox ORDER BY id')]
        self.assertEqual(len(alerts), 2)
        self.assertIn('original', alerts[0])
        self.assertIn('edited', alerts[0])
        self.assertIn('edited', alerts[1])

    async def test_cached_human_survives_sender_lookup_failure(self):
        await self.w.record(Event(ident=1, media=None))
        event = Event(ident=2, text='retained original', media=None)
        async def failed_sender():
            raise asyncio.TimeoutError()
        event.get_sender = failed_sender
        await self.w.record(event)
        await self.w.deleted(S(chat_id=None, deleted_ids=[2]))
        self.assertEqual(self.w.db.execute('SELECT text,deleted FROM messages WHERE id=2').fetchone(),
                         ('retained original', 1))

    async def test_cached_bot_still_excluded(self):
        event = Event(media=None)
        event.sender = S(bot=True)
        await self.w.record(event)
        self.assertEqual(self.w.db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)

    async def test_unknown_sender_failure_visible_without_archiving(self):
        event = Event(media=None)
        async def failed_sender():
            raise asyncio.TimeoutError()
        event.get_sender = failed_sender
        await self.w.record(event)
        self.assertEqual(self.w.db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)
        self.assertIsNotNone(self.w.db.execute("SELECT value FROM settings WHERE key='last_unresolved_sender_utc'").fetchone())

    async def test_expiring_and_view_once_excluded(self):
        for marker in ('ttl_period', 'ttl_seconds', 'view_once'):
            event = Event()
            if marker == 'ttl_period':
                event.message.ttl_period = 60
            else:
                event.message.media = S(**{marker: 1})
            await self.w.record(event)
        self.assertEqual(self.w.db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)
        self.assertEqual(self.w.db.execute('SELECT count(*) FROM media').fetchone()[0], 0)

    async def test_authorization_gate_buffers_events(self):
        ready = asyncio.Event()
        worker = self.w.install(S(add_event_handler=lambda *args: None), ready=ready)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        event_task = asyncio.create_task(self.w.handlers[0](Event(media=None)))
        await asyncio.sleep(.01)
        self.assertFalse(event_task.done())
        self.assertEqual(self.w.db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)
        ready.set()
        await event_task
        self.assertEqual(self.w.db.execute('SELECT count(*) FROM messages').fetchone()[0], 1)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_stall_has_timeout(self):
        class Client:
            async def connect(self):
                await asyncio.Event().wait()
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(authorize(Client(), 99), .02)

    async def test_unauthorized_and_wrong_owner_rejected(self):
        class Client:
            async def connect(self): pass
            async def is_user_authorized(self): return True
            async def get_me(self): return S(id=98, bot=False)
        with self.assertRaises(RuntimeError):
            await authorize(Client(), 99)
        class Unauthorized(Client):
            async def is_user_authorized(self): return False
        with self.assertRaises(RuntimeError):
            await authorize(Unauthorized(), 99)

    async def test_full_runner_startup_and_disconnect_deadlines(self):
        trace = []
        class Client:
            def __init__(self, *a, **kw):
                self.session = S(get_input_entity=lambda ident: S(access_hash=99))
            async def connect(self):
                trace.append('connect')
                await asyncio.Event().wait()
            async def disconnect(self):
                trace.append('disconnect')
                await asyncio.Event().wait()
        class Watch:
            def install(self, client, ready):
                trace.append('installed')
                return asyncio.create_task(asyncio.Event().wait())
            def close(self, client):
                trace.append('closed')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'config.json').write_text(json.dumps(dict(api_id=1, api_hash='unused', owner_id=99)))
            with patch.object(archive_runner, 'TelegramClient', Client), \
                 patch.object(archive_runner, 'create_watch', return_value=Watch()), \
                 patch.object(archive_runner, 'STARTUP_TIMEOUT', .02), \
                 patch.object(archive_runner, 'DISCONNECT_TIMEOUT', .02):
                started = time.monotonic()
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(archive_runner.run(path, S(arm=lambda *a: None)), .5)
                self.assertLess(time.monotonic()-started, .3)
        self.assertEqual(trace, ['installed', 'connect', 'disconnect', 'closed'])

    async def test_health_timeout(self):
        class Client:
            async def __call__(self, request):
                await asyncio.Event().wait()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(asyncio.TimeoutError):
                await health_loop(Client(), Path(tmp), S(arm=lambda *args: None), timeout=.02)

    async def test_health_evidence_written_after_rpc(self):
        class Client:
            async def __call__(self, request): pass
        arms = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            task = asyncio.create_task(health_loop(Client(), path, S(arm=lambda *a: arms.append(a))))
            for _ in range(50):
                if (path / 'archive-health.json').exists(): break
                await asyncio.sleep(.002)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(json.loads((path / 'archive-health.json').read_text())['phase'], 'connected')
            self.assertEqual(len(arms), 1)


class WatchdogTests(unittest.TestCase):
    def test_deadline_and_disarm(self):
        hit = threading.Event()
        w = DeadlineWatchdog(.02, exit_fn=lambda code: hit.set(), interval=.005)
        try:
            self.assertTrue(hit.wait(.5))
        finally:
            w.close()
        hit.clear()
        w = DeadlineWatchdog(.02, exit_fn=lambda code: hit.set(), interval=.005)
        w.close()
        self.assertFalse(hit.wait(.05))

    def test_fatal_exit_does_not_depend_on_logging(self):
        hit = threading.Event()
        with patch.object(archive_runner, 'report', side_effect=OSError('log unavailable')) as log:
            w = DeadlineWatchdog(.02, exit_fn=lambda code: hit.set(), interval=.005)
            try:
                self.assertTrue(hit.wait(.5))
                log.assert_not_called()
            finally:
                w.close()

    def test_hard_deadline_exits_stuck_process(self):
        code = ('from archive_runner import DeadlineWatchdog; import threading; '
                'w=DeadlineWatchdog(.05, interval=.005); threading.Event().wait()')
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).parent,
                                timeout=3, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
