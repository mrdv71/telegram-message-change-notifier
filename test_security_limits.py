"""Synthetic tests: quotas never delete archives; failed deliveries cannot starve peers."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace as S
from message_watch import MessageWatch
from test_message_watch import Event

class LimitsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.w = MessageWatch(self.root, 99, 'unused')
    async def asyncTearDown(self):
        self.w.db.close()
        self.tmp.cleanup()
    def test_queue_cap_preserves_existing(self):
        self.w.limits['queue_items'] = 2
        self.w.enqueue('first'); self.w.enqueue('second'); self.w.enqueue('third')
        self.assertEqual(self.w.db.execute('SELECT text FROM outbox').fetchall(), [('first',), ('second',)])
    async def test_sender_limit(self):
        self.w.limits['sender_events'] = 1
        await self.w.record(Event(ident=1, media=None))
        await self.w.record(Event(ident=2, media=None))
        self.assertEqual(self.w.db.execute('SELECT id FROM messages').fetchall(), [(1,)])
    async def test_full_disk_stops_admission_without_deleting(self):
        (self.root/'existing').write_bytes(b'keep')
        self.w.limits['archive_bytes'] = 1
        await self.w.record(Event(media=None))
        self.assertEqual(self.w.db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)
        self.assertEqual((self.root/'existing').read_bytes(), b'keep')
    def test_free_space_floor(self):
        with patch('shutil.disk_usage', return_value=S(free=1)):
            self.assertFalse(self.w.storage_available())
    async def test_oversized_media_never_downloaded(self):
        self.w.limits['file_bytes'] = 3
        event = Event(); event.message.document.size = 4
        await self.w.record(event)
        class Client:
            async def download_media(self, *args, **kwargs):
                raise AssertionError('oversized download attempted')
        task = asyncio.create_task(self.w.download_media(Client()))
        await asyncio.sleep(.05); task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.w.db.execute('SELECT status FROM media').fetchone()[0], 'blocked')
    def test_failure_backoff_fairness_and_dead_letter(self):
        self.w.limits['delivery_attempts'] = 2
        self.w.enqueue('bad'); self.w.enqueue('good')
        row = self.w.next_delivery()
        self.w.defer_delivery(row[0], RuntimeError())
        self.assertEqual(self.w.next_delivery()[1], 'good')
        self.w.defer_delivery(row[0], RuntimeError())
        self.assertEqual(self.w.db.execute('SELECT text FROM delivery_dead_letter').fetchall(), [('bad',)])
    def test_retry_after_persisted_global_pause(self):
        import message_watch
        self.w.enqueue('one'); self.w.enqueue('two')
        with patch('time.time', return_value=100):
            self.w.defer_delivery(1, message_watch.RateLimited(120))
            self.assertIsNone(self.w.next_delivery())
        with patch('time.time', return_value=221):
            self.assertIsNotNone(self.w.next_delivery())
    def test_invalid_limits_fail_closed(self):
        (self.root/'limits.json').write_text('{"queue_items": 0}')
        with self.assertRaises(ValueError):
            MessageWatch(self.root, 99, 'unused')

    def test_http_retry_after_parsed_without_exposing_token(self):
        import io
        import urllib.error
        import message_watch
        error = urllib.error.HTTPError('https://example.invalid', 429, 'limited', {},
            io.BytesIO(b'{"parameters":{"retry_after":123}}'))
        with patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(message_watch.RateLimited) as caught:
                self.w.send('synthetic')
        self.assertEqual(caught.exception.seconds, 123)
        self.assertNotIn('unused', str(caught.exception))

    async def test_streaming_limit_stops_unknown_size(self):
        self.w.limits['file_bytes'] = 3
        await self.w.record(Event())
        class Client:
            async def download_media(self, msg, file, progress_callback):
                progress_callback(4, 4)
                raise AssertionError('quota callback failed to abort')
        task = asyncio.create_task(self.w.download_media(Client()))
        await asyncio.sleep(.05); task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.w.db.execute('SELECT attempts FROM media').fetchone()[0], 1)
        self.assertEqual(list(self.w.media_dir.rglob('saved*')), [])

    async def test_pending_media_queue_is_bounded(self):
        self.w.limits['queue_items'] = 1
        await self.w.record(Event(ident=1))
        await self.w.record(Event(ident=2))
        self.assertEqual(self.w.db.execute('SELECT count(*) FROM media').fetchone()[0], 1)

    async def test_delete_burst_obeys_sender_budget(self):
        await self.w.record(Event(ident=1, media=None))
        await self.w.record(Event(ident=2, media=None))
        self.w.limits['sender_events'] = 3
        await self.w.deleted(S(chat_id=None, deleted_ids=[1,2]))
        self.assertEqual(self.w.db.execute('SELECT count(*) FROM outbox').fetchone()[0], 1)

    def test_retry_state_survives_reopen(self):
        from message_watch import RateLimited
        self.w.enqueue('preserved')
        self.w.defer_delivery(1, RateLimited(120))
        self.w.db.close()
        self.w = MessageWatch(self.root, 99, 'unused')
        self.assertIsNone(self.w.next_delivery())
        self.assertEqual(self.w.db.execute('SELECT text,attempts FROM outbox').fetchone(),
                         ('preserved', 1))

    def test_additive_migration_preserves_old_queue(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = sqlite3.connect(root / 'message-history.sqlite3')
            db.executescript("CREATE TABLE outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT);"
                             "INSERT INTO outbox(text) VALUES ('legacy alert');")
            db.close()
            watcher = MessageWatch(root, 99, 'unused')
            try:
                self.assertEqual(watcher.next_delivery(), (1, 'legacy alert', None))
                self.assertEqual(watcher.db.execute(
                    'SELECT attempts,next_attempt FROM outbox').fetchone(), (0, 0))
            finally:
                watcher.db.close()

    async def test_edit_over_budget_preserves_original_and_no_partial_alert(self):
        await self.w.record(Event(text='original', media=None))
        self.w.limits['queue_items'] = 1
        await self.w.record(Event(text='x' * 4000, media=None), edited=True)
        self.assertEqual(self.w.db.execute('SELECT text FROM messages').fetchone(), ('original',))
        self.assertEqual(self.w.db.execute('SELECT count(*) FROM outbox').fetchone()[0], 0)

    async def test_delete_attachment_is_atomic_at_queue_boundary(self):
        await self.w.record(Event())
        self.w.limits['queue_items'] = 2  # pending media + text fits, attachment does not
        await self.w.deleted(S(chat_id=None, deleted_ids=[1]))
        self.assertEqual(self.w.db.execute('SELECT deleted FROM messages').fetchone(), (0,))
        self.assertEqual(self.w.db.execute('SELECT count(*) FROM outbox').fetchone()[0], 0)

    def test_dead_letter_collision_never_overwrites_existing(self):
        import sqlite3
        self.w.limits['delivery_attempts'] = 1
        self.w.enqueue('pending')
        self.w.db.execute("INSERT INTO delivery_dead_letter VALUES (1,'keep',NULL,'old',0)")
        self.w.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.w.defer_delivery(1, RuntimeError())
        self.assertEqual(self.w.db.execute('SELECT text FROM delivery_dead_letter').fetchone(), ('keep',))
        self.assertEqual(self.w.db.execute('SELECT text FROM outbox').fetchone(), ('pending',))

    def test_legacy_receipts_and_quarantine_survive_schema_upgrade(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = sqlite3.connect(root / 'message-history.sqlite3')
            db.executescript("CREATE TABLE delivery_receipts(outbox_id INTEGER PRIMARY KEY,message_id INTEGER,acknowledged_at TEXT);"
                             "INSERT INTO delivery_receipts VALUES (9,42,'synthetic');"
                             "CREATE TABLE delivery_quarantine(outbox_id INTEGER PRIMARY KEY,text TEXT,media_key TEXT,reason TEXT,quarantined_at TEXT);"
                             "INSERT INTO delivery_quarantine VALUES (8,'keep',NULL,'ambiguous','synthetic');")
            db.close()
            watcher = MessageWatch(root, 99, 'unused')
            try:
                self.assertEqual(watcher.db.execute('SELECT * FROM delivery_receipts').fetchall(), [(9,42,'synthetic')])
                self.assertEqual(watcher.db.execute('SELECT text FROM delivery_quarantine').fetchall(), [('keep',)])
                self.assertIsNone(watcher.next_delivery())  # never replay quarantined work
            finally:
                watcher.db.close()

    async def test_retry_keeps_previous_partial_bytes(self):
        await self.w.record(Event())
        paths = []
        original_sleep = asyncio.sleep
        async def fast_sleep(delay):
            await original_sleep(0)
        class Client:
            async def download_media(self, msg, file, progress_callback):
                path = Path(file + '.bin')
                paths.append(path)
                if len(paths) == 1:
                    path.write_bytes(b'partial retained')
                    raise OSError('synthetic interruption')
                self_same = paths[0] == path
                if self_same:
                    raise AssertionError('retry overwrites prior partial')
                path.write_bytes(b'complete')
                return str(path)
        with patch('message_watch.asyncio.sleep', side_effect=fast_sleep):
            task = asyncio.create_task(self.w.download_media(Client()))
            try:
                for _ in range(100):
                    await original_sleep(0)
                    if self.w.db.execute('SELECT status FROM media').fetchone()[0] == 'ready':
                        break
                self.assertEqual(self.w.db.execute('SELECT status FROM media').fetchone()[0], 'ready')
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(paths[0].read_bytes(), b'partial retained')
        self.assertEqual(len(paths), 2)
