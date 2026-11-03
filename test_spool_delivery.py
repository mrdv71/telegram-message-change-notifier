import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from message_watch import MessageWatch

class SpoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.spool = self.root/'spool'; self.spool.mkdir()
        self.w = MessageWatch(self.root, 99, 'unused')
        self.addCleanup(self.w.db.close)
        self.w.spool_root = self.spool
        self.file = self.spool/'synthetic.txt'; self.file.write_text('synthetic')
        self.w.send = lambda text: None
    def queue(self, marked=True):
        with self.w.db:
            self.w.db.execute("INSERT INTO media(key,path,status) VALUES('upload:test',?,'ready')", (str(self.file),))
            self.w.enqueue_media('upload:test','SYNTHETIC TEST')
            if marked:
                self.w.db.execute("INSERT INTO upload_spool(key,path) VALUES('upload:test',?)", (str(self.file),))
    async def run_delivery(self):
        task = asyncio.create_task(self.w.deliver())
        for _ in range(100):
            await asyncio.sleep(.01)
            if not self.w.db.execute('SELECT count(*) FROM outbox').fetchone()[0]: break
        task.cancel(); await asyncio.gather(task, return_exceptions=True)
    async def test_success_cleanup(self):
        self.queue()
        async def upload(*a): return True
        self.w.send_file = upload
        await self.run_delivery()
        self.assertFalse(self.file.exists())
        self.assertEqual(self.w.db.execute('SELECT state FROM upload_spool').fetchone()[0], 'cleaned')
    async def test_failure_retains(self):
        self.queue()
        async def upload(*a): raise OSError('synthetic failure')
        self.w.send_file = upload
        await self.run_delivery()
        self.assertTrue(self.file.exists())
        self.assertEqual(self.w.db.execute('SELECT count(*) FROM outbox').fetchone()[0], 1)
    async def test_false_upload_retains(self):
        self.queue()
        async def upload(*a): return False
        self.w.send_file = upload
        await self.run_delivery()
        self.assertTrue(self.file.exists())
        self.assertEqual(self.w.db.execute('SELECT state FROM upload_spool').fetchone()[0], 'retained')
    async def test_ordinary_file_retained(self):
        self.queue(marked=False)
        async def upload(*a): return True
        self.w.send_file = upload
        await self.run_delivery()
        self.assertTrue(self.file.exists())
    async def test_containment(self):
        self.file = self.root/'outside.txt'; self.file.write_text('keep')
        self.queue()
        with self.w.db: self.w.db.execute("UPDATE upload_spool SET state='uploaded'")
        self.w.cleanup_spool()
        self.assertTrue(self.file.exists())
    async def test_cleanup_retry_after_restart(self):
        self.queue()
        with self.w.db: self.w.db.execute("UPDATE upload_spool SET state='uploaded'")
        with patch.object(Path, 'unlink', side_effect=OSError('busy')): self.w.cleanup_spool()
        self.assertTrue(self.file.exists())
        self.w.cleanup_spool(); self.w.cleanup_spool()
        self.assertFalse(self.file.exists())
    async def test_symlink_escape(self):
        outside=self.root/'outside'; outside.write_text('keep')
        self.file.unlink(); self.file.symlink_to(outside)
        self.queue()
        with self.w.db: self.w.db.execute("UPDATE upload_spool SET state='uploaded'")
        self.w.cleanup_spool()
        self.assertTrue(outside.exists())
