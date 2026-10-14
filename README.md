# Telegram Message Change Notifier

by mrdv71

Watch incoming private messages with your own Telegram account and receive edit/delete alerts through your own bot. A bot alone cannot read your personal inbox.

## Choose one setup

- **Your Mac, Linux computer, or Windows PC:** follow the steps below. Keep it awake and the terminal open.
- **An always-on Linux server:** follow the [server guide](DEPLOYMENT.md). It includes automatic startup and restart.

GitHub stores the code; it does not run the program. Each user provides their own Telegram account and credentials. Installing this repository does not migrate or change another installation.

## 1. Get your Telegram credentials

1. Sign in at [my.telegram.org/apps](https://my.telegram.org/apps) and create an application to get your **API ID** and **API hash**.
2. Have your phone number (including country code), Telegram login code, and two-step verification password (if enabled) ready.
3. In Telegram, open the verified [@BotFather](https://t.me/BotFather), send `/newbot`, and follow its instructions. Save the **bot token**.
4. Open your new bot from the **same personal account** you will use for setup and press **Start**. This allows it to send you alerts.

Enter these values only when the local setup command asks. Hidden inputs show no characters while you type; this is normal. Never put credentials in GitHub issues or commits.

## 2. Install Python and Git

Use **Python 3.10 or newer** (3.12 recommended) and Git.

- **Mac:** install Python from [python.org](https://www.python.org/downloads/macos/). Run `git --version`; if macOS offers Command Line Tools, install them before continuing.
- **Ubuntu 24.04 / Debian 12 or newer:** run `sudo apt update`, then `sudo apt install -y python3 python3-venv git`.
- **Windows:** install Python from [python.org](https://www.python.org/downloads/windows/) with the Python launcher, and Git from [git-scm.com](https://git-scm.com/downloads/win). Reopen PowerShell afterward.

Check `python3 --version` and `git --version` on Mac/Linux, or `py -3 --version` and `git --version` on Windows. Stop and fix missing tools or a Python version below 3.10 before continuing.

## 3. Download and install

Run the commands **one line at a time**. Continue only if the previous command succeeds. Choose the block for your operating system. These commands are for a new installation; if you already cloned this repository, enter that folder instead of cloning it again.

### Mac / Linux — Terminal

```sh
git clone https://github.com/mrdv71/telegram-message-change-notifier.git
cd telegram-message-change-notifier
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

### Windows — PowerShell

```powershell
git clone https://github.com/mrdv71/telegram-message-change-notifier.git
cd telegram-message-change-notifier
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

No virtual-environment activation is needed. Keep using the Python path shown below.

## 4. Sign in once

**Mac / Linux:**

```sh
.venv/bin/python notifier.py setup
```

**Windows:**

```powershell
.\.venv\Scripts\python.exe notifier.py setup
```

Follow the prompts. Success prints `Setup saved locally`. Do not repeat setup on every start.

## 5. Start and check

**Mac / Linux:**

```sh
.venv/bin/python notifier.py run
```

**Windows:**

```powershell
.\.venv\Scripts\python.exe notifier.py run
```

Wait for `Watching incoming private chats`. From a second human account, send a new private text message to the signed-in account. Wait a few seconds, edit it, wait for the edit alert, then delete it. Check your notification bot for both alerts. Use a new message sent while this program is running; old messages are not backfilled.

Press **Ctrl+C** to stop. To start again, open a terminal in this repository folder and repeat **step 5 only**. Closing the terminal, sleeping, or shutting down the computer interrupts collection. On Mac, you can use `caffeinate -i .venv/bin/python notifier.py run` to prevent idle sleep while running; this does not guarantee operation with the lid closed.

## If something fails

- **File not found / cannot open script:** enter the cloned repository folder first.
- **Missing module:** repeat the install command in step 3 using the same `.venv` Python.
- **Login/setup missing:** complete step 4, then start again with the same data directory.
- **Database locked:** stop other copies using the same session; run only one process per data directory.
- **No saved media or alerts:** repeat the exact test in step 5, check the network, and read the limits below.
- **No bot alerts:** press Start in your bot from the signed-in account, unblock it if necessary, and check that you used the correct BotFather token.
- **Already configured:** run step 5 instead of setup. Do not delete your state to troubleshoot; it contains cached messages and your login.

Windows instructions are provided but Windows operation has not been verified. On Windows, restrict the private data folder to your own user account using folder permissions.

## Features

- Incoming one-to-one chats, including non-contacts; excludes outgoing messages, bots, groups, channels, and Saved Messages.
- Edit alerts with cached previous text/caption and new text/caption; deletion alerts with the last cached content.
- Downloads available photos, video, audio, voice notes and documents on arrival; sends cached attachments as documents after edits/deletions.
- Sender names and available usernames; notification text is currently Uzbek.
- Local SQLite cache, persistent media jobs and delivery queue; duplicate event suppression.

## Limits and lifecycle

- The computer must remain awake, online, and the command must keep running. The basic command installs no background service; DEPLOYMENT.md provides an optional Linux systemd service. Ctrl+C stops it without logging out.
- Only observed/cached originals can be reported. Already-deleted content cannot be recovered. An edit first observed without a cached original explicitly reports that the old content is unavailable.
- Secret Chats are unsupported. There is no startup history backfill or guaranteed offline edit/delete recovery.
- Media can disappear before download completes; failed/missing originals are labeled accordingly. No guarantee of saving every disappearing item.
- Files above the conservative 49 MiB bot-upload threshold remain local and generate a location notice. Telegram may impose other restrictions. Attachments are sent as documents, not native voice bubbles.
- Delivery retries persist, but a crash after Telegram accepts a notification can produce a duplicate.
- The cache stores the latest message text, retained attachment versions, and pending alerts—not a complete permanent text-version timeline.

On Mac/Linux, use `.venv/bin/python notifier.py --state-dir /path/to/private-state setup` and
`.venv/bin/python notifier.py --state-dir /path/to/private-state run` for a custom data
location. Use the same directory for both commands.

## Local data

By default, everything private lives in ignored `state/` (or an explicit `--state-dir`): account session, API credentials, bot token, message cache and downloaded media. Setup applies restrictive permissions on POSIX. Data is retained until you remove it; there is no automatic retention cleanup. Never share this directory. Stop the process before backing up or deleting state. Use for conversations you are authorized to archive.

This repository contains code, synthetic tests and instructions only. It does not include any existing user's messages, contacts, tokens, sessions, media or machine-specific paths. Installing it does not modify an existing collector.

## Offline checks

On Mac/Linux, from the repository folder:

```sh
.venv/bin/python -m unittest -v
.venv/bin/python notifier.py --help
```

Tests include ordered overlapping arrival/edit/delete events and long Unicode notification splitting. Tests use temporary databases and generated dummy media; they do not log in or send real Telegram messages. Live account capture, live bot delivery, reboot and sleep/wake behavior must be verified separately in your own setup.
