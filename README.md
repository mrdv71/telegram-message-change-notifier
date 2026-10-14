# Telegram Message Change Notifier

by mrdv71

A standalone source edition of a personal Telegram edit/delete notifier. A **personal-account Telethon client** observes incoming private messages; your **own Telegram bot** sends notifications back to the signed-in account. A normal bot alone cannot read your personal inbox.

## Run locally or on a server

Choose your own **Mac/Linux/Windows computer** or an **always-on Linux server**.
See [deployment options](DEPLOYMENT.md) for persistent storage, interactive login,
and an optional systemd service with boot startup and automatic restart.
The local commands below still work without installing a service.

## Features

- Incoming one-to-one chats, including non-contacts; excludes outgoing messages, bots, groups, channels, and Saved Messages.
- Edit alerts with cached previous text/caption and new text/caption; deletion alerts with the last cached content.
- Downloads available photos, video, audio, voice notes and documents on arrival; sends cached attachments as documents after edits/deletions.
- Sender names and available usernames; notification text is currently Uzbek.
- Local SQLite cache, persistent media jobs and delivery queue; duplicate event suppression.

## Setup (macOS / Linux, Python 3.10+)

Create your own API ID/hash at https://my.telegram.org and a bot through @BotFather. Open the bot in Telegram and press **Start** using the same account you will sign in with. No credentials are provided by this repository.

```sh
git clone https://github.com/mrdv71/telegram-message-change-notifier.git
cd telegram-message-change-notifier
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python notifier.py setup
python notifier.py run
```

Setup prompts for API credentials, a bot token, and Telegram login. Secrets are entered locally; the notification recipient is derived from the signed-in account. This source edition uses a **fresh, separate session** and does not read OpenClaw configuration or the original installed collector. Do not run multiple processes against the same session/state directory.

On Windows (PowerShell), after cloning and entering the repository:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe notifier.py setup
.\.venv\Scripts\python.exe notifier.py run
```

Windows operation has not been verified.

## Limits and lifecycle

- The computer must remain awake, online, and the command must keep running. The basic command installs no background service; DEPLOYMENT.md provides an optional Linux systemd service. Ctrl+C stops it without logging out.
- Only observed/cached originals can be reported. Already-deleted content cannot be recovered. An edit first observed without a cached original explicitly reports that the old content is unavailable.
- Secret Chats are unsupported. There is no startup history backfill or guaranteed offline edit/delete recovery.
- Media can disappear before download completes; failed/missing originals are labeled accordingly. No guarantee of saving every disappearing item.
- Files above the conservative 49 MiB bot-upload threshold remain local and generate a location notice. Telegram may impose other restrictions. Attachments are sent as documents, not native voice bubbles.
- Delivery retries persist, but a crash after Telegram accepts a notification can produce a duplicate.
- The cache stores the latest message text, retained attachment versions, and pending alerts—not a complete permanent text-version timeline.

Use `python notifier.py --state-dir /path/to/private-state setup` and
`python notifier.py --state-dir /path/to/private-state run` for a custom data
location. Use the same directory for both commands.

## Local data

By default, everything private lives in ignored `state/` (or an explicit `--state-dir`): account session, API credentials, bot token, message cache and downloaded media. Setup applies restrictive permissions on POSIX. Data is retained until you remove it; there is no automatic retention cleanup. Never share this directory. Stop the process before backing up or deleting state. Use for conversations you are authorized to archive.

This repository contains code, synthetic tests and instructions only. It does not include any existing user's messages, contacts, tokens, sessions, media or machine-specific paths. Installing it does not modify an existing collector.

## Offline checks

```sh
python -m unittest -v
python notifier.py --help
```

Tests include ordered overlapping arrival/edit/delete events and long Unicode notification splitting. Tests use temporary databases and generated dummy media; they do not log in or send real Telegram messages. Live account capture, live bot delivery, reboot and sleep/wake behavior must be verified separately in your own setup.
