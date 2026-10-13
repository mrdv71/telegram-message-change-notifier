import asyncio
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace as S
from message_watch import MessageWatch

class Event:
    def __init__(self, ident=1, text='caption', media=10, out=False, private=True):
        self.is_private = private
        self.message = S(id=ident, message=text, sender_id=7, chat_id=7,
                         out=out, photo=None, document=S(id=media) if media else None)
    async def get_sender(self):
        return S(first_name='Test', last_name='', bot=False)

class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.w = MessageWatch(self.path,99,'unused')
    async def asyncTearDown(self):
        self.w.db.close()
        self.tmp.cleanup()
    async def test_edit_delete_versions(self):
        await self.w.record(Event())
        await self.w.record(Event(media=11), True)
        await self.w.record(Event(media=11), True)
        self.assertEqual(self.w.db.execute('SELECT count(*) FROM outbox').fetchone()[0],3)
        await self.w.deleted(S(chat_id=-100,deleted_ids=[1]))
        await self.w.deleted(S(chat_id=None,deleted_ids=[1]))
        await self.w.deleted(S(chat_id=None,deleted_ids=[1]))
        self.assertEqual(self.w.db.execute('SELECT count(*) FROM outbox').fetchone()[0],5)
        self.assertEqual(self.w.db.execute('SELECT count(*) FROM media').fetchone()[0],2)
    async def test_download_delete_race_and_delivery(self):
        await self.w.record(Event())
        await self.w.deleted(S(chat_id=None,deleted_ids=[1]))
        class Client:
            async def download_media(self,msg,file):
                p=Path(file+'.ogg');p.write_bytes(b'test-audio');return str(p)
        task=asyncio.create_task(self.w.download_media(Client()))
        for _ in range(100):
            if self.w.db.execute('SELECT status FROM media').fetchone()[0]=='ready':break
            await asyncio.sleep(.01)
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        path,status=self.w.db.execute('SELECT path,status FROM media').fetchone()
        self.assertEqual(status,'ready');self.assertEqual(Path(path).read_bytes(),b'test-audio')
        sent=[]
        self.w.send=lambda text:sent.append(('text',text))
        async def upload(path,caption):sent.append(('file',Path(path).read_bytes()));return True
        self.w.send_file=upload
        task=asyncio.create_task(self.w.deliver())
        for _ in range(300):
            if not self.w.db.execute('SELECT count(*) FROM outbox').fetchone()[0]:break
            await asyncio.sleep(.01)
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.assertEqual([x[0] for x in sent],['text','file'])
    async def test_exclusions_and_persistence(self):
        await self.w.record(Event(out=True));await self.w.record(Event(private=False))
        self.assertEqual(self.w.db.execute('SELECT count(*) FROM media').fetchone()[0],0)
        await self.w.record(Event())
        self.w.db.close();self.w=MessageWatch(self.path,99,'unused')
        self.assertEqual(self.w.db.execute('SELECT status FROM media').fetchone()[0],'pending')
    async def test_migrate_old_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=sqlite3.connect(str(Path(tmp)/'message-history.sqlite3'))
            db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,chat INTEGER,sender TEXT,text TEXT,deleted INTEGER DEFAULT 0)')
            db.execute("INSERT INTO messages VALUES(5,7,'test','preserve',0)")
            db.commit();db.close()
            w=MessageWatch(Path(tmp),99,'unused')
            self.assertEqual(w.db.execute('SELECT text,media_key FROM messages').fetchone(),('preserve',None))
            w.db.close()

class ExtraTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = Tests.asyncSetUp
    asyncTearDown = Tests.asyncTearDown
    async def test_unknown_original_and_caption_edit(self):
        await self.w.record(Event(text='new', media=None), True)
        text = self.w.db.execute('SELECT text FROM outbox').fetchone()[0]
        self.assertIn('saqlanmagan', text)
        self.assertIn('new', text)

    async def test_sender_labels(self):
        self.assertEqual(await self.w.sender_label(S(first_name='Example', last_name='Person', username='example_user')), 'Example Person (@example_user)')
        self.assertEqual(await self.w.sender_label(fallback='Example (ID: 123)'), 'Example')

    async def test_pending_delivery_survives_restart(self):
        await self.w.record(Event(text='old', media=None))
        await self.w.record(Event(text='new', media=None), True)
        before = self.w.db.execute('SELECT text FROM outbox').fetchall()
        self.w.db.close()
        self.w = MessageWatch(self.path, 99, 'unused')
        self.assertEqual(self.w.db.execute('SELECT text FROM outbox').fetchall(), before)

if __name__=='__main__':unittest.main()
