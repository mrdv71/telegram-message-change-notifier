"""Incoming private-message text/media history and durable Bot API notifications."""
import asyncio
import json
import re
import sqlite3
import urllib.request
import aiohttp
from pathlib import Path
from telethon import events


class MessageWatch:
    def __init__(self, state, owner_id, token):
        self.owner_id, self.token = owner_id, token
        self.db = sqlite3.connect(str(state / 'message-history.sqlite3'))
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY, chat INTEGER, sender TEXT, text TEXT, deleted INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        ''')
        for table, column in [('messages', 'media_key TEXT'), ('outbox', 'media_key TEXT')]:
            if column.split()[0] not in [r[1] for r in self.db.execute(f'PRAGMA table_info({table})')]:
                self.db.execute(f'ALTER TABLE {table} ADD COLUMN {column}')
        self.db.execute('CREATE TABLE IF NOT EXISTS media (key TEXT PRIMARY KEY, chat INTEGER, msg INTEGER, path TEXT, status TEXT, attempts INTEGER DEFAULT 0)')
        self.db.commit()
        self.media_dir = state / 'message-media'
        self.media_dir.mkdir(mode=0o700, exist_ok=True)
        self.live_media = {}
        self.handlers = []

    def enqueue(self, text):
        # Telegram's UTF-16 limit also counts astral characters as two units.
        for offset in range(0, len(text), 1800):
            self.db.execute('INSERT INTO outbox(text) VALUES (?)', (text[offset:offset+1800],))

    def enqueue_media(self, key, label):
        if key:
            self.db.execute('INSERT INTO outbox(text,media_key) VALUES (?,?)', (label, key))

    async def sender_label(self, sender=None, chat=None, fallback=''):
        if chat and getattr(self, 'client', None):
            try:
                sender = await asyncio.wait_for(self.client.get_entity(chat), 5)
            except Exception:
                pass
        name = ' '.join(str(value).strip() for value in
                        [getattr(sender, 'first_name', ''), getattr(sender, 'last_name', '')]
                        if value and str(value).strip())
        username = getattr(sender, 'username', None)
        if not username:
            username = next((u.username for u in (getattr(sender, 'usernames', None) or [])
                             if getattr(u, 'active', False)), None)
        old_name = re.sub(r'\s*\(ID: \d+\)$', '', fallback).strip()
        if not name:
            name = old_name or 'Ismi mavjud emas'
        return f'{name} (@{username})' if username else name

    async def record(self, event, edited=False):
        msg = event.message
        if (not event.is_private or msg.out or not msg.sender_id
                or msg.sender_id == self.owner_id or msg.chat_id != msg.sender_id):
            return
        sender = await event.get_sender()
        if sender is None or getattr(sender, 'bot', False):
            return
        label = await self.sender_label(sender, msg.sender_id if getattr(sender, 'min', False) or not getattr(sender, 'first_name', None) else None)
        attachment = getattr(msg, 'photo', None) or getattr(msg, 'document', None)
        key = f'{msg.chat_id}_{msg.id}_{attachment.id}' if attachment else None
        body = msg.message or ('[Media fayl]' if key else '[Matnsiz xabar]')
        old = self.db.execute('SELECT text,media_key FROM messages WHERE id=?', (msg.id,)).fetchone()
        with self.db:
            if key:
                self.db.execute("INSERT OR IGNORE INTO media(key,chat,msg,status) VALUES (?,?,?,'pending')", (key,msg.chat_id,msg.id))
                status = self.db.execute('SELECT status FROM media WHERE key=?', (key,)).fetchone()[0]
                if status == 'pending':
                    self.live_media[key] = msg
            if edited and (old is None or old[0] != body or old[1] != key):
                before = old[0] if old else '[Oldingi matn kuzatuvda saqlanmagan]'
                self.enqueue(f'✏️ Xabar tahrirlandi\n{label}\n\nOldingi:\n{before}\n\nYangi:\n{body}')
                if old and old[1]:
                    self.enqueue_media(old[1], f'Oldingi fayl — {label}')
                if key and (not old or old[1] != key):
                    self.enqueue_media(key, f'Yangi fayl — {label}')
            self.db.execute('INSERT INTO messages(id,chat,sender,text,media_key) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET sender=excluded.sender,text=excluded.text,media_key=excluded.media_key',
                            (msg.id, msg.chat_id, label, body, key))

    async def deleted(self, event):
        # Non-channel private message IDs share the account's ID namespace.
        # Never match a channel deletion against a private message ID.
        if event.chat_id is not None:
            return
        for ident in event.deleted_ids:
            row = self.db.execute('SELECT sender,text,media_key,chat FROM messages WHERE id=? AND deleted=0', (ident,)).fetchone()
            if row:
                label = await self.sender_label(chat=row[3], fallback=row[0])
                with self.db:
                    # Another duplicate deletion may have arrived during lookup.
                    changed = self.db.execute('UPDATE messages SET deleted=1,sender=? WHERE id=? AND deleted=0', (label,ident)).rowcount
                    if changed:
                        self.enqueue(f'🗑 Xabar o‘chirildi\n{label}\n\n{row[1]}')
                        self.enqueue_media(row[2], f'O‘chirilgan fayl — {label}')

    def send(self, text):
        payload = json.dumps({'chat_id': self.owner_id, 'text': text}).encode()
        req = urllib.request.Request('https://api.telegram.org/bot' + self.token + '/sendMessage',
                                     data=payload, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=25) as response:
            if not json.load(response).get('ok'):
                raise RuntimeError('Bot delivery rejected')

    async def download_media(self, client):
        while True:
            row = self.db.execute("SELECT key,chat,msg,attempts FROM media WHERE status='pending' ORDER BY attempts,rowid LIMIT 1").fetchone()
            if not row:
                await asyncio.sleep(1)
                continue
            key, chat, ident, attempts = row
            try:
                msg = self.live_media.get(key)
                if msg is None:
                    msg = await asyncio.wait_for(client.get_messages(chat, ids=ident), 30)
                attachment = (getattr(msg, 'photo', None) or getattr(msg, 'document', None)) if msg else None
                if not attachment or key != f'{chat}_{ident}_{attachment.id}':
                    raise ValueError('Original media is no longer available')
                folder = self.media_dir / key
                folder.mkdir(mode=0o700, exist_ok=True)
                # Explicit safe path: never use sender-supplied file names.
                downloaded = await asyncio.wait_for(client.download_media(msg, file=str(folder / 'download')), 600)
                if not downloaded or not Path(downloaded).is_file() or not Path(downloaded).stat().st_size:
                    raise ValueError('Empty media download')
                source = Path(downloaded)
                final = folder / ('saved' + source.suffix)
                source.replace(final)
                final.chmod(0o600)
                with self.db:
                    self.db.execute("UPDATE media SET path=?,status='ready' WHERE key=?", (str(final),key))
                self.live_media.pop(key, None)
                print('Message watch: media saved.', flush=True)
            except Exception as exc:
                with self.db:
                    self.db.execute('UPDATE media SET attempts=attempts+1,status=? WHERE key=?', ('failed' if attempts >= 2 else 'pending',key))
                if attempts >= 2:
                    self.live_media.pop(key, None)
                print('Message watch media download:', type(exc).__name__, flush=True)
                await asyncio.sleep(3)

    async def send_file(self, path, caption):
        # sendDocument preserves audio/voice, photo and video bytes unchanged.
        data = aiohttp.FormData()
        data.add_field('chat_id', str(self.owner_id))
        data.add_field('caption', caption[:450])
        with open(path, 'rb') as stream:
            data.add_field('document', stream, filename=Path(path).name, content_type='application/octet-stream')
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as session:
                async with session.post('https://api.telegram.org/bot' + self.token + '/sendDocument', data=data) as response:
                    result = await response.json()
                    if not result.get('ok'):
                        if response.status == 413 or (response.status == 400 and 'too big' in result.get('description','').lower()):
                            return False
                        raise RuntimeError('Bot file delivery rejected')
        return True

    async def deliver(self):
        while True:
            row = self.db.execute('SELECT id,text,media_key FROM outbox ORDER BY id LIMIT 1').fetchone()
            if not row:
                await asyncio.sleep(2)
                continue
            try:
                if row[2]:
                    media = self.db.execute('SELECT path,status FROM media WHERE key=?', (row[2],)).fetchone()
                    if media and media[1] == 'pending':
                        await asyncio.sleep(2)
                        continue
                    if not media or media[1] != 'ready' or not Path(media[0]).is_file():
                        await asyncio.to_thread(self.send, row[1] + '\n⚠️ Faylni saqlashga ulgurilmadi yoki fayl mavjud emas.')
                    elif Path(media[0]).stat().st_size > 49 * 1024 * 1024:
                        await asyncio.to_thread(self.send, row[1] + '\n📁 Katta fayl kompyuterda saqlandi; bot yuklash chegarasi sabab yuborilmadi.\n' + media[0])
                    elif not await self.send_file(media[0], row[1]):
                        await asyncio.to_thread(self.send, row[1] + '\n📁 Fayl kompyuterda saqlandi; Telegram yuklashni rad etdi.\n' + media[0])
                else:
                    await asyncio.to_thread(self.send, row[1])
                with self.db:
                    self.db.execute('DELETE FROM outbox WHERE id=?', (row[0],))
                print('Message watch: notification delivered.', flush=True)
                await asyncio.sleep(1)
            except Exception as exc:
                # Never print exception bodies/URLs, which can contain the token.
                print('Message watch delivery retry:', type(exc).__name__, flush=True)
                await asyncio.sleep(30)

    def install(self, client):
        self.client = client
        async def new(event):
            await self.record(event)
        async def edit(event):
            await self.record(event, edited=True)
        for callback, kind in [(new, events.NewMessage(incoming=True)),
                               (edit, events.MessageEdited(incoming=True)),
                               (self.deleted, events.MessageDeleted())]:
            client.add_event_handler(callback, kind)
            self.handlers.append(callback)
        with self.db:
            if not self.db.execute("SELECT 1 FROM settings WHERE key='announced'").fetchone():
                self.enqueue('✅ Shaxsiy xabarlar kuzatuvi yoqildi. Endi kuzatuvga tushgan xabar o‘chirilsa yoki tahrirlansa, shu bot orqali bildirishnoma keladi. Matn, caption va mavjud media fayllari saqlanadi. Kompyuter uyg‘oq va internetga ulangan bo‘lishi kerak.')
                self.db.execute("INSERT INTO settings VALUES ('announced','1')")
            if not self.db.execute("SELECT 1 FROM settings WHERE key='media_announced'").fetchone():
                self.enqueue('✅ Media kuzatuvi yoqildi: rasm, video, audio, ovozli xabar va hujjatlar kelganda kompyuterda saqlanadi. O‘chirilsa yoki tahrirlansa, saqlangan fayl shu botga yuboriladi. Katta fayllar faqat kompyuterda qolishi mumkin.')
                self.db.execute("INSERT INTO settings VALUES ('media_announced','1')")
        print('Message watch: private incoming text and media handlers ready.', flush=True)
        async def supervise():
            tasks = [asyncio.create_task(self.deliver()), asyncio.create_task(self.download_media(client))]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        return asyncio.create_task(supervise())

    def close(self, client):
        for callback in self.handlers:
            client.remove_event_handler(callback)
        self.db.close()


