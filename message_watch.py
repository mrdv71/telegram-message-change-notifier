"""Incoming private-message text/media history and durable Bot API notifications."""
import asyncio
from contextlib import suppress
import json
import re
import os
import shutil
import time
import uuid
import urllib.error
import sqlite3
import urllib.request
import aiohttp
from pathlib import Path
from datetime import datetime, timezone
from telethon import events


def is_ephemeral(msg):
    return bool(getattr(msg, 'ttl_period', None)
                or getattr(getattr(msg, 'media', None), 'ttl_seconds', None)
                or getattr(getattr(msg, 'media', None), 'view_once', False))


class AdmissionRefused(RuntimeError):
    """Roll back an event when its complete notification cannot fit."""


class RateLimited(RuntimeError):
    def __init__(self, seconds):
        try:
            self.seconds = max(1, int(seconds))
        except (TypeError, ValueError, OverflowError):
            self.seconds = 60
        super().__init__('Telegram rate limit')


DEFAULT_LIMITS = dict(file_bytes=49*1024*1024, archive_bytes=10*1024**3,
                      free_bytes=1024**3, queue_items=5000, sender_events=60,
                      sender_window=60, delivery_attempts=5)


class MessageWatch:
    def __init__(self, state, owner_id, token):
        state = Path(state)
        self.state = state
        self.limits = DEFAULT_LIMITS.copy()
        limits_file = self.state / 'limits.json'
        if limits_file.exists():
            overrides = json.loads(limits_file.read_text())
            if not isinstance(overrides, dict) or any(
                    k not in self.limits or type(v) is not int or v <= 0
                    for k, v in overrides.items()):
                raise ValueError('Invalid archive limits')
            self.limits.update(overrides)
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
        self.db.execute("CREATE TABLE IF NOT EXISTS upload_spool (key TEXT PRIMARY KEY, path TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', uploaded_at TEXT)")
        for column in ('attempts INTEGER DEFAULT 0', 'next_attempt REAL DEFAULT 0'):
            if column.split()[0] not in [r[1] for r in self.db.execute('PRAGMA table_info(outbox)')]:
                self.db.execute('ALTER TABLE outbox ADD COLUMN ' + column)
        self.db.execute('CREATE TABLE IF NOT EXISTS delivery_dead_letter (id INTEGER PRIMARY KEY, text TEXT, media_key TEXT, reason TEXT, failed_at REAL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS sender_budget (chat INTEGER PRIMARY KEY, started REAL, count INTEGER)')
        self.spool_root = state / 'upload-spool'
        self.db.commit()
        self.media_dir = state / 'message-media'
        self.media_dir.mkdir(mode=0o700, exist_ok=True)
        self.live_media = {}
        self.handlers = []

    def storage_available(self, reserve=0):
        # Count all private state, including failed/partial files. Never delete.
        try:
            if shutil.disk_usage(self.state).free < self.limits['free_bytes'] + reserve:
                return False
            total = 0
            for root, dirs, files in os.walk(self.state, followlinks=False):
                for name in files:
                    path = Path(root) / name
                    if not path.is_symlink():
                        total += path.stat().st_size
                        if total + reserve >= self.limits['archive_bytes']:
                            return False
            return total + reserve < self.limits['archive_bytes']
        except OSError:
            return False

    def queue_available(self, needed=1):
        count = self.db.execute('SELECT count(*) FROM outbox').fetchone()[0]
        count += self.db.execute('SELECT count(*) FROM delivery_dead_letter').fetchone()[0]
        count += self.db.execute("SELECT count(*) FROM media WHERE status='pending'").fetchone()[0]
        return count + needed <= self.limits['queue_items']

    def admit_sender(self, chat):
        now = time.time()
        row = self.db.execute('SELECT started,count FROM sender_budget WHERE chat=?', (chat,)).fetchone()
        if row and now - row[0] < self.limits['sender_window']:
            if row[1] >= self.limits['sender_events']:
                return False
            self.db.execute('UPDATE sender_budget SET count=count+1 WHERE chat=?', (chat,))
        else:
            self.db.execute('INSERT OR REPLACE INTO sender_budget VALUES (?,?,1)', (chat,now))
        return True

    def next_delivery(self):
        now = time.time()
        pause = self.db.execute("SELECT value FROM settings WHERE key='bot_retry_at'").fetchone()
        if pause and float(pause[0]) > now:
            return None
        return self.db.execute('SELECT id,text,media_key FROM outbox WHERE next_attempt<=? ORDER BY id LIMIT 1', (now,)).fetchone()

    def defer_delivery(self, ident, error):
        row = self.db.execute('SELECT text,media_key,attempts FROM outbox WHERE id=?', (ident,)).fetchone()
        if not row:
            return
        attempts = row[2] + 1
        delay = error.seconds if isinstance(error, RateLimited) else min(3600, 30 * 2**min(attempts-1,7))
        with self.db:
            if isinstance(error, RateLimited):
                self.db.execute("INSERT OR REPLACE INTO settings VALUES ('bot_retry_at',?)", (str(time.time()+delay),))
            if attempts >= self.limits['delivery_attempts']:
                self.db.execute('INSERT INTO delivery_dead_letter VALUES (?,?,?,?,?)',
                                (ident,row[0],row[1],type(error).__name__,time.time()))
                self.db.execute('DELETE FROM outbox WHERE id=?', (ident,))
            else:
                self.db.execute('UPDATE outbox SET attempts=?,next_attempt=? WHERE id=?',
                                (attempts,time.time()+delay,ident))

    def enqueue(self, text):
        # Telegram's UTF-16 limit also counts astral characters as two units.
        if not self.queue_available((len(text)+1799)//1800) or not self.storage_available():
            return False
        for offset in range(0, len(text), 1800):
            self.db.execute('INSERT INTO outbox(text) VALUES (?)', (text[offset:offset+1800],))
        return True

    def enqueue_media(self, key, label):
        if key and self.queue_available() and self.storage_available():
            self.db.execute('INSERT INTO outbox(text,media_key) VALUES (?,?)', (label, key))
            return True
        return not key

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
        if is_ephemeral(msg):
            return
        if (not event.is_private or msg.out or not msg.sender_id
                or msg.sender_id == self.owner_id or msg.chat_id != msg.sender_id):
            return
        # Use an already-resolved entity first; a flaky lookup must not lose
        # an event whose sender identity is already known locally.
        sender = getattr(event, 'sender', None)
        if sender is None:
            try:
                sender = await asyncio.wait_for(event.get_sender(), 10)
            except Exception as exc:
                cached = self.db.execute('SELECT sender FROM messages WHERE chat=? ORDER BY id DESC LIMIT 1',
                                         (msg.chat_id,)).fetchone()
                if cached:
                    from types import SimpleNamespace
                    # This chat was previously admitted by the human-only filter.
                    sender = SimpleNamespace(first_name=cached[0], bot=False)
                else:
                    with self.db:
                        self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('last_unresolved_sender_utc',?)",
                                        (datetime.now(timezone.utc).isoformat(),))
                    print('Message watch: sender unresolved; event not archived:', type(exc).__name__, flush=True)
                    return
        if sender is None or getattr(sender, 'bot', False):
            return
        if not self.storage_available() or not self.queue_available():
            return
        with self.db:
            if not self.admit_sender(msg.chat_id):
                return
        label = await self.sender_label(sender, msg.sender_id if getattr(sender, 'min', False) or not getattr(sender, 'first_name', None) else None)
        attachment = getattr(msg, 'photo', None) or getattr(msg, 'document', None)
        key = f'{msg.chat_id}_{msg.id}_{attachment.id}' if attachment else None
        body = msg.message or ('[Media fayl]' if key else '[Matnsiz xabar]')
        old = self.db.execute('SELECT text,media_key FROM messages WHERE id=?', (msg.id,)).fetchone()
        with suppress(AdmissionRefused), self.db:
            if key:
                self.db.execute("INSERT OR IGNORE INTO media(key,chat,msg,status) VALUES (?,?,?,'pending')", (key,msg.chat_id,msg.id))
            if edited and (old is None or old[0] != body or old[1] != key):
                before = old[0] if old else '[Oldingi matn kuzatuvda saqlanmagan]'
                if not self.enqueue(f'✏️ Xabar tahrirlandi\n{label}\n\nOldingi:\n{before}\n\nYangi:\n{body}'):
                    raise AdmissionRefused()
                if old and old[1]:
                    if not self.enqueue_media(old[1], f'Oldingi fayl — {label}'):
                        raise AdmissionRefused()
                if key and (not old or old[1] != key):
                    if not self.enqueue_media(key, f'Yangi fayl — {label}'):
                        raise AdmissionRefused()
            self.db.execute('INSERT INTO messages(id,chat,sender,text,media_key) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET sender=excluded.sender,text=excluded.text,media_key=excluded.media_key',
                            (msg.id, msg.chat_id, label, body, key))
            self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('last_record_utc',?)",
                            (datetime.now(timezone.utc).isoformat(),))

        # Only retain live objects for media jobs actually committed.
        if key and self.db.execute("SELECT 1 FROM media WHERE key=? AND status='pending'", (key,)).fetchone():
            self.live_media[key] = msg

    async def deleted(self, event):
        # Non-channel private message IDs share the account's ID namespace.
        # Never match a channel deletion against a private message ID.
        if event.chat_id is not None:
            return
        if not self.storage_available() or not self.queue_available():
            return
        for ident in event.deleted_ids:
            row = self.db.execute('SELECT sender,text,media_key,chat FROM messages WHERE id=? AND deleted=0', (ident,)).fetchone()
            if row:
                if not self.queue_available():
                    return
                with self.db:
                    if not self.admit_sender(row[3]):
                        continue
                label = await self.sender_label(chat=row[3], fallback=row[0])
                with suppress(AdmissionRefused), self.db:
                    # Another duplicate deletion may have arrived during lookup.
                    changed = self.db.execute('UPDATE messages SET deleted=1,sender=? WHERE id=? AND deleted=0', (label,ident)).rowcount
                    if changed:
                        if not self.enqueue(f'🗑 Xabar o‘chirildi\n{label}\n\n{row[1]}'):
                            raise AdmissionRefused()
                        if not self.enqueue_media(row[2], f'O‘chirilgan fayl — {label}'):
                            raise AdmissionRefused()
                        self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES ('last_deletion_utc',?)",
                                        (datetime.now(timezone.utc).isoformat(),))

    def send(self, text):
        payload = json.dumps({'chat_id': self.owner_id, 'text': text}).encode()
        req = urllib.request.Request('https://api.telegram.org/bot' + self.token + '/sendMessage',
                                     data=payload, headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                try:
                    delay = json.loads(exc.read(65536)).get('parameters', {}).get('retry_after', 60)
                except (ValueError, AttributeError):
                    delay = 60
                raise RateLimited(delay) from None
            raise
        if result.get('error_code') == 429:
            raise RateLimited(result.get('parameters', {}).get('retry_after', 60))
        if not result.get('ok'):
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
                if is_ephemeral(msg) or not attachment or key != f'{chat}_{ident}_{attachment.id}':
                    raise ValueError('Original media is no longer available')
                size = getattr(attachment, 'size', None)
                if size is None:
                    size = max((getattr(x, 'size', 0) for x in getattr(attachment, 'sizes', [])), default=0)
                if size > self.limits['file_bytes']:
                    with self.db:
                        self.db.execute("UPDATE media SET status='blocked' WHERE key=?", (key,))
                    self.live_media.pop(key, None)
                    continue
                if not self.storage_available(max(size, self.limits['file_bytes'])):
                    await asyncio.sleep(30)
                    continue
                folder = self.media_dir / key
                folder.mkdir(mode=0o700, exist_ok=True)
                # Explicit safe path: never use sender-supplied file names.
                def progress(current, total):
                    if current > self.limits['file_bytes'] or not self.storage_available():
                        raise ValueError('Download quota exceeded')
                downloaded = await asyncio.wait_for(client.download_media(
                    msg, file=str(folder / f'download-{uuid.uuid4().hex}'), progress_callback=progress), 600)
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
                    if response.status == 429 or result.get('error_code') == 429:
                        raise RateLimited(result.get('parameters', {}).get('retry_after', 60))
                    if not result.get('ok'):
                        if response.status == 413 or (response.status == 400 and 'too big' in result.get('description','').lower()):
                            return False
                        raise RuntimeError('Bot file delivery rejected')
                    message = result.get('result', {})
                    if (not message.get('message_id') or not message.get('document')
                            or message.get('chat', {}).get('id') != self.owner_id):
                        raise RuntimeError('Bot file acknowledgement incomplete')
        return True

    def cleanup_spool(self):
        root = self.spool_root.resolve()
        for key, name in self.db.execute("SELECT key,path FROM upload_spool WHERE state='uploaded'").fetchall():
            path = Path(name)
            try:
                if (not key.startswith('upload:') or path.is_symlink()
                        or not path.resolve().is_relative_to(root) or path.resolve() == root):
                    continue
                path.unlink(missing_ok=True)
                with self.db:
                    self.db.execute("UPDATE upload_spool SET state='cleaned' WHERE key=?", (key,))
                    self.db.execute("UPDATE media SET status='delivered',path=NULL WHERE key=?", (key,))
                if path.parent != root:
                    try:
                        path.parent.rmdir()
                    except OSError:
                        pass
            except OSError:
                print('Message watch: temporary upload cleanup will retry.', flush=True)

    async def deliver(self):
        while True:
            self.cleanup_spool()
            row = self.next_delivery()
            if not row:
                await asyncio.sleep(2)
                continue
            try:
                uploaded = False
                if row[2]:
                    media = self.db.execute('SELECT path,status FROM media WHERE key=?', (row[2],)).fetchone()
                    if media and media[1] == 'pending':
                        with self.db:
                            self.db.execute('UPDATE outbox SET next_attempt=? WHERE id=?', (time.time()+5,row[0]))
                        continue
                    if not media or media[1] != 'ready' or not Path(media[0]).is_file():
                        await asyncio.to_thread(self.send, row[1] + '\n⚠️ Faylni saqlashga ulgurilmadi yoki fayl mavjud emas.')
                    elif Path(media[0]).stat().st_size > 49 * 1024 * 1024:
                        await asyncio.to_thread(self.send, row[1] + '\n📁 Katta fayl kompyuterda saqlandi; bot yuklash chegarasi sabab yuborilmadi.\n' + media[0])
                    elif not await self.send_file(media[0], row[1]):
                        await asyncio.to_thread(self.send, row[1] + '\n📁 Fayl kompyuterda saqlandi; Telegram yuklashni rad etdi.\n' + media[0])
                    else:
                        uploaded = True
                else:
                    await asyncio.to_thread(self.send, row[1])
                with self.db:
                    self.db.execute("UPDATE upload_spool SET state=?,uploaded_at=? WHERE key=?",
                                    ('uploaded' if uploaded else 'retained',
                                     datetime.now(timezone.utc).isoformat() if uploaded else None, row[2]))
                    self.db.execute('DELETE FROM outbox WHERE id=?', (row[0],))
                self.cleanup_spool()
                print('Message watch: notification delivered.', flush=True)
                await asyncio.sleep(1)
            except Exception as exc:
                # Never print exception bodies/URLs, which can contain the token.
                print('Message watch delivery retry:', type(exc).__name__, flush=True)
                self.defer_delivery(row[0], exc)
                await asyncio.sleep(0)

    def install(self, client, ready=None):
        self.client = client
        if ready is None:
            ready = asyncio.Event()
            ready.set()
        # All three mutations share one lock before any yielding lookup.
        event_lock = asyncio.Lock()
        async def new(event):
            async with event_lock:
                await ready.wait()
                await self.record(event)
        async def edit(event):
            async with event_lock:
                await ready.wait()
                await self.record(event, edited=True)
        async def deleted(event):
            async with event_lock:
                await ready.wait()
                await self.deleted(event)
        for callback, kind in [(new, events.NewMessage(incoming=True)),
                               (edit, events.MessageEdited(incoming=True)),
                               (deleted, events.MessageDeleted())]:
            client.add_event_handler(callback, kind)
            self.handlers.append(callback)
        with self.db:
            if not self.db.execute("SELECT 1 FROM settings WHERE key='announced'").fetchone():
                self.enqueue('✅ Shaxsiy xabarlar kuzatuvi yoqildi. Endi kuzatuvga tushgan xabar o‘chirilsa yoki tahrirlansa, shu bot orqali bildirishnoma keladi. Matn, caption va mavjud oddiy media fayllari saqlanadi. Kompyuter uyg‘oq va internetga ulangan bo‘lishi kerak.')
                self.db.execute("INSERT INTO settings VALUES ('announced','1')")
            if not self.db.execute("SELECT 1 FROM settings WHERE key='media_announced'").fetchone():
                self.enqueue('✅ Media kuzatuvi yoqildi: rasm, video, audio, ovozli xabar va hujjatlar kelganda kompyuterda saqlanadi. O‘chirilsa yoki tahrirlansa, saqlangan fayl shu botga yuboriladi. Katta fayllar faqat kompyuterda qolishi mumkin.')
                self.db.execute("INSERT INTO settings VALUES ('media_announced','1')")
        print('Message watch: private incoming text and media handlers ready.', flush=True)
        async def supervise():
            await ready.wait()
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
